from pathlib import Path

import pytest

from edge_agent import service
from edge_agent.permissions import (
    DEFAULT_PERMISSIONS,
    bridge_rules,
    profile_for,
    validate_permissions,
    workspace_allowed,
)


def test_defaults_ask_before_changes_and_keep_discord_off(tmp_path):
    config = service.EdgeAgent(tmp_path).config["permissions"]
    assert bridge_rules(config["ui"]) == {"edit": "ask", "shell": "ask", "web": "allow"}
    assert config["discord"]["enabled"] is False
    with pytest.raises(PermissionError):
        profile_for(config, "discord")


def test_partial_profiles_merge_over_defaults_and_persist(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    saved = manager.update_config(
        {"permissions": {"discord": {"enabled": True, "shell": "deny"}}}
    )
    discord = saved["permissions"]["discord"]
    assert discord == {
        **DEFAULT_PERMISSIONS["discord"],
        "enabled": True,
        "shell": "deny",
    }
    assert (
        service.EdgeAgent(tmp_path).config["permissions"]["discord"]["shell"] == "deny"
    )


@pytest.mark.parametrize(
    "value",
    [
        {"discord": {"shell": "sometimes"}},
        {"discord": {"root": True}},
        {"telegram": {}},
        {"ui": {"workspaces": "C:/"}},
        {"ui": {"enabled": "yes"}},
    ],
)
def test_invalid_permissions_are_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        validate_permissions(value)


def test_unknown_client_is_rejected():
    with pytest.raises(ValueError):
        profile_for(validate_permissions({}), "anyone")


def test_workspace_roots_limit_folders(tmp_path: Path):
    allowed = tmp_path / "projects"
    (allowed / "game").mkdir(parents=True)
    (tmp_path / "private").mkdir()
    profile = {**DEFAULT_PERMISSIONS["discord"], "workspaces": [str(allowed)]}
    assert workspace_allowed(profile, allowed / "game")
    assert not workspace_allowed(profile, tmp_path / "private")
    assert not workspace_allowed(profile, allowed / ".." / "private")
    assert workspace_allowed(DEFAULT_PERMISSIONS["ui"], tmp_path / "private")
