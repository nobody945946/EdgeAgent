from __future__ import annotations

import ctypes
import os
from uuid import uuid4

import pytest

from silenttalk import credentials
from silenttalk.config import load_settings


@pytest.mark.skipif(os.name != "nt", reason="Windows Credential Manager")
def test_windows_vault_roundtrip_in_isolated_test_entry(monkeypatch):
    target = f"SilentTalk/Test/{uuid4().hex}"
    monkeypatch.setattr(credentials, "TARGET", target)
    try:
        assert credentials.read() == {}
        credentials.save("fixture-token", "123")
        assert credentials.read() == {"token": "fixture-token", "owner": "123"}
    finally:
        api = credentials._api()
        api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
        api.CredDeleteW(target, 1, 0)


def test_invalid_credentials_are_rejected_before_vault_access(monkeypatch):
    monkeypatch.setattr(
        credentials, "_api", lambda: pytest.fail("must validate before writing")
    )
    with pytest.raises(ValueError):
        credentials.save("", "123")
    with pytest.raises(ValueError):
        credentials.save("fixture", "0")


def test_settings_load_vault_without_creating_env_file(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("[app]\n", encoding="utf-8")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.setattr(
        credentials, "read", lambda: {"token": "fixture", "owner": "456"}
    )
    settings = load_settings(tmp_path)
    assert settings.discord.owner_user_id == 456
    assert settings.discord.token == "fixture"
    assert not (tmp_path / ".env").exists()
