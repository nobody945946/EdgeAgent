from pathlib import Path

import pytest

from edge_agent import service
from edge_agent.permissions import (
    DEFAULT_PERMISSIONS,
    bridge_rules,
    profile_for,
    routine_shell,
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
        {"ui": {"edit": "routine"}},
        {"ui": {"web": "routine"}},
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


def test_routine_shell_profile_persists_without_enabling_writes(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    manager.update_config({"permissions": {"discord": {"shell": "routine"}}})
    profile = service.EdgeAgent(tmp_path).config["permissions"]["discord"]
    assert bridge_rules(profile) == {"edit": "ask", "shell": "routine", "web": "allow"}
    assert not profile["enabled"]


@pytest.mark.skipif(service.os.name != "nt", reason="native Windows PowerShell policy")
@pytest.mark.parametrize(
    "command,expected",
    [
        (
            'Get-ChildItem -Path . -Filter "*.tmp" -File | Measure-Object | Select-Object -ExpandProperty Count',
            True,
        ),
        ("Get-Content ./README.md", True),
        ("Select-String -Path ./README.md -Pattern abc", True),
        ("Get-ChildItem $(Remove-Item data) -File", False),
        ("Get-ChildItem | ForEach-Object { Remove-Item $_ }", False),
        (
            'Get-ChildItem | Select-Object @{Name="x";Expression={[IO.File]::WriteAllText("x","y")}}',
            False,
        ),
        ("Get-ChildItem > result.txt", False),
        ("Remove-Item data", False),
        ("Set-Content report.txt 2", False),
        ('[IO.File]::WriteAllText("report.txt","2")', False),
        ("Get-Content ../private.txt", False),
        ("Get-Content *.txt", False),
        ("Get-ChildItem . -File | Get-Content", False),
        ("Get-ChildItem . -r | Measure-Object", True),
        ('Get-ChildItem . -Filter "delete*.tmp" -File', True),
        ("Get-Content HKLM:\\SOFTWARE", False),
        ("Get-Content $env:USERPROFILE", False),
        ("python -m pytest -q", False),
        ("Get-ChildItem -NotAParameter", False),
        ("Get-ChildItem HKLM:\\Software -File", False),
    ],
)
def test_routine_shell_uses_native_syntax_and_workspace_scope(
    tmp_path, command, expected
):
    assert routine_shell(command, tmp_path) is expected
