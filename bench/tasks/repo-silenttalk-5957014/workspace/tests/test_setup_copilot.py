from __future__ import annotations

from pathlib import Path

import pytest

from scripts import setup_copilot
from silenttalk import edge_ai
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
    monkeypatch.setattr(edge_ai, "service_status", lambda: next(statuses))
    monkeypatch.setattr(edge_ai, "ensure_service", lambda: {"mode": "base"})

    def post(path, payload, **kwargs):
        calls.append(path)
        if path == "/shutdown":
            return {"mode": "stopped"}
        return {"mode": "character", "owner_pid": payload["owner_pid"]}

    monkeypatch.setattr(edge_ai, "post_json", post)
    monkeypatch.setattr(
        edge_ai,
        "release_character",
        lambda owner: calls.append("release") or {"mode": "base"},
    )
    setup_copilot.check_runtime({})
    assert calls == ["/character", "release", "/shutdown"]


def test_runtime_check_does_not_steal_existing_lease(monkeypatch):
    monkeypatch.setattr(edge_ai, "service_status", lambda: {"mode": "character"})
    with pytest.raises(RuntimeError, match="already leased"):
        setup_copilot.check_runtime({})


def test_failed_runtime_check_releases_its_lease(monkeypatch):
    calls = []
    monkeypatch.setattr(edge_ai, "service_status", lambda: {"mode": "base"})
    monkeypatch.setattr(edge_ai, "ensure_service", lambda: {"mode": "base"})
    monkeypatch.setattr(edge_ai, "post_json", lambda *args, **kwargs: {"mode": "wrong"})
    monkeypatch.setattr(edge_ai, "release_character", lambda owner: calls.append(owner))
    with pytest.raises(RuntimeError, match="lease validation"):
        setup_copilot.check_runtime({})
    assert calls == [setup_copilot.os.getpid()]
