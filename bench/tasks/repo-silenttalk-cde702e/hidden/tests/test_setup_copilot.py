from __future__ import annotations

from pathlib import Path

import pytest

from scripts import setup_copilot
from silenttalk import agent_lease
from silenttalk.settings_store import SettingsStore


def test_setup_uses_settings_page_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.toml").write_text(
        '[copilot]\nstt_engine = "paraformer"\n', encoding="utf-8"
    )
    SettingsStore(tmp_path / "settings.json").save(
        {"copilot": {"stt_engine": "sensevoice"}}
    )
    monkeypatch.setattr(setup_copilot, "PROJECT_DIR", tmp_path)

    assert setup_copilot._copilot_section()["stt_engine"] == "sensevoice"


def test_runtime_check_verifies_lease_and_restores_service(monkeypatch):
    calls = []
    statuses = iter([None, {"mode": "base"}])
    monkeypatch.setattr(agent_lease, "service_status", lambda: next(statuses))
    monkeypatch.setattr(agent_lease, "ensure_service", lambda: {"mode": "base"})

    def post(path, payload, **kwargs):
        calls.append(path)
        if path == "/shutdown":
            return {"mode": "stopped"}
        return {"mode": "character", "owner_pid": payload["owner_pid"]}

    monkeypatch.setattr(agent_lease, "post_json", post)
    monkeypatch.setattr(
        agent_lease,
        "release_character",
        lambda owner: calls.append("release") or {"mode": "base"},
    )
    setup_copilot.check_runtime({})
    assert calls == ["/character", "release", "/shutdown"]


def test_runtime_check_does_not_steal_existing_lease(monkeypatch):
    monkeypatch.setattr(agent_lease, "service_status", lambda: {"mode": "character"})
    with pytest.raises(RuntimeError, match="already leased"):
        setup_copilot.check_runtime({})


def test_failed_runtime_check_releases_its_lease(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_lease, "service_status", lambda: {"mode": "base"})
    monkeypatch.setattr(agent_lease, "ensure_service", lambda: {"mode": "base"})
    monkeypatch.setattr(
        agent_lease, "post_json", lambda *args, **kwargs: {"mode": "wrong"}
    )
    monkeypatch.setattr(
        agent_lease, "release_character", lambda owner: calls.append(owner)
    )
    with pytest.raises(RuntimeError, match="lease validation"):
        setup_copilot.check_runtime({})
    assert calls == [setup_copilot.os.getpid()]


def test_checksum_marker_does_not_hide_corrupted_speech_asset(tmp_path):
    import hashlib

    expected = hashlib.sha256(b"correct").hexdigest()
    asset = tmp_path / "model.onnx"
    asset.write_bytes(b"damaged")
    asset.with_suffix(".onnx.sha256").write_text(expected, encoding="ascii")
    assert not setup_copilot._is_ready(asset, expected, 1)
    assert asset.read_bytes() == b"damaged"
