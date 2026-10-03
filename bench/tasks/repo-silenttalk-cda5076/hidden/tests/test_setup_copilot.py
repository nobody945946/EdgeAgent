from __future__ import annotations

from pathlib import Path

import pytest

from scripts import setup_copilot
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
