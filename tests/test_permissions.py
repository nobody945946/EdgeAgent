import os
from pathlib import Path

import pytest

from edge_agent.core import EdgeAgent
from edge_agent.permissions import (
    DEFAULT_PERMISSIONS,
    bridge_rules,
    profile_for,
    routine_shell,
    scope_rules,
    validate_permissions,
    workspace_allowed,
)


def test_defaults_run_what_can_be_put_back_and_keep_discord_off(tmp_path):
    config = EdgeAgent(tmp_path).config["permissions"]
    assert bridge_rules(config["ui"]) == {"edit": "reversible", "shell": "reversible", "web": "allow"}
    assert config["discord"]["enabled"] is False
    with pytest.raises(PermissionError):
        profile_for(config, "discord")


def test_partial_profiles_merge_over_defaults_and_persist(tmp_path):
    manager = EdgeAgent(tmp_path)
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
        EdgeAgent(tmp_path).config["permissions"]["discord"]["shell"] == "deny"
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


def test_the_earlier_routine_shell_becomes_reversible_without_enabling_a_client(tmp_path):
    manager = EdgeAgent(tmp_path)
    manager.update_config({"permissions": {"discord": {"edit": "ask", "shell": "routine"}}})
    profile = EdgeAgent(tmp_path).config["permissions"]["discord"]
    assert bridge_rules(profile) == {"edit": "ask", "shell": "reversible", "web": "allow"}
    assert not profile["enabled"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell policy")
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
        # A list of literals is literal; a path must still be one literal path.
        ("Get-ChildItem . -File | Select-Object Name, Length", True),
        ("Get-Content ./README.md, ../private.txt", False),
        ("Get-ChildItem ., .. -File", False),
    ],
)
def test_routine_shell_uses_native_syntax_and_workspace_scope(
    tmp_path, command, expected
):
    assert routine_shell(command, tmp_path) is expected


def test_a_task_scope_narrows_but_never_widens():
    full = {"edit": "allow", "shell": "allow", "web": "allow"}
    assert scope_rules(full, "change") == full
    assert scope_rules(full, "read") == {"edit": "deny", "shell": "read", "web": "allow"}
    assert scope_rules(full, "public") == {"edit": "deny", "shell": "deny", "web": "allow"}
    assert scope_rules({"edit": "ask", "shell": "routine", "web": "deny"}, "read") == {
        "edit": "deny", "shell": "read", "web": "deny"}
    assert scope_rules({"edit": "deny", "shell": "deny", "web": "ask"}, "read") == {
        "edit": "deny", "shell": "deny", "web": "ask"}
    with pytest.raises(ValueError):
        scope_rules(full, "everything")


@pytest.mark.parametrize(
    "command,expected",
    [
        ("Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory", True),
        ("Get-CimInstance -ClassName Win32_LogicalDisk -Filter \"DeviceID='C:'\"", True),
        ("Get-Volume C", True),
        ("Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus", True),
        ("Get-PSDrive -PSProvider FileSystem", True),
        # Process command lines and environment variables carry keys and passwords.
        ("Get-CimInstance Win32_Process", False),
        ("Get-CimInstance Win32_StartupCommand", False),  # its default view is the command line
        ("Get-CimInstance Win32_Environment", False),
        ("Get-CimInstance -Query 'SELECT * FROM Win32_ComputerSystem'", False),
        ("Get-CimInstance Win32_ComputerSystem -ComputerName server01", False),
        ("Get-CimInstance Win32_ComputerSystem -Namespace root/subscription", False),
        # Get-ComputerInfo here comes from the sandbox's stand-in, not Windows' own module.
        ("Get-ComputerInfo", False),
    ],
)
def test_a_read_only_task_reads_the_machine_from_windows_own_modules(tmp_path, monkeypatch, command, expected):
    windows = Path(__file__).resolve().parents[1] / "bench" / "windows"
    monkeypatch.setenv("PSModulePath", f"{windows}{os.pathsep}{os.environ.get('PSModulePath', '')}")
    assert routine_shell(command, tmp_path) is expected


@pytest.mark.parametrize(
    "command,expected",
    [
        # Counting and shaping what was read: variables, operators, script blocks, if and foreach.
        (("$lines = Get-Content -Path app.log; $lines | Where-Object { $_ -match 'ERROR' } | "
          "ForEach-Object { $_ -replace '^\\S+ \\S+ ERROR ', '' } | Group-Object | "
          "Sort-Object Count -Descending | Select-Object -First 3 Count, Name"), True),
        (("$h = @{}; Get-Content -Path app.log | ForEach-Object { $h[$_]++ }; "
          "$h.GetEnumerator() | Sort-Object Value -Descending"), True),
        ("Get-ChildItem -Path . -File | Select-Object Name, @{n='KB'; e={[math]::Round($_.Length / 1KB, 1)}}", True),
        ("foreach ($line in Get-Content -Path app.log) { if ($line.StartsWith('2025')) { $line.Split(' ')[2] } }",
         True),
        ("Get-Content -Path data.json -Raw | ConvertFrom-Json | Select-Object -ExpandProperty name", True),
        ("(73600 + 6322) * 0.8 / 4", True),
        ("$PSVersionTable.PSVersion.ToString()", True),
        ("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 Name, Id", True),
        # Keys: other scopes' variables and ones the script did not set (a session's earlier command could have).
        ('Write-Output "$env:OPENAI_API_KEY"', False),
        ("${env:HOME}", False),
        ("$secret", False),
        ("$x = $x + 'a'; $x", False),
        ("$PSDefaultParameterValues = @{}", False),
        ("$a = @{Path = '/'}; Get-ChildItem @a", False),
        # Command lines, and the views that show every property.
        ("Get-Process | Select-Object Name, CommandLine", False),
        ("Get-Process | Format-List *", False),
        ("Get-Process | Select-Object C*", False),
        ("Get-Process | ForEach-Object { $_.CommandLine }", False),
        ("Get-Process | Select-Object -ExpandProperty StartInfo", False),
        ("$p = 'Name'; Get-Process | Format-List -Property $p", False),
        ("Get-Process | ForEach-Object { $_.PSObject.Properties }", False),
        # A path passed along a pipeline: one the proof has seen, or none.
        ("Get-ChildItem -Path . -File -Recurse | Get-FileHash -Algorithm SHA256 | Select-Object Hash, Path", True),
        ("Get-Content -Path app.log | Select-String -Pattern ERROR | Measure-Object -Line", True),
        ("Get-Item -Path app.log | Get-Content", True),
        ("Write-Output / | Get-ChildItem", False),
        ("Get-Content -Path paths.txt | Get-Item", False),
        ("Get-Content -Path paths.txt | Get-FileHash", False),
        ("Write-Output /etc/passwd | Get-FileHash", False),
        ("Get-ChildItem -Path . | Select-String -Pattern key", False),
        ("Get-ChildItem -Path . | ForEach-Object { Get-Content -Path $_.FullName }", False),
        # Errors discarded or merged; nothing written.
        ("Get-Content -Path app.log 2>$null | Out-Null", True),
        ("Get-ChildItem -Path . 2>&1", True),
        ("Get-ChildItem -Path . -Recurse -Filter '*.log' -ErrorAction SilentlyContinue", True),
        ("Get-ChildItem -Path . -ErrorVariable PSDefaultParameterValues", False),
        ("'x' > out.txt", False),
        # Methods, properties and types that change things.
        ("Get-ChildItem -Path . | ForEach-Object { $_.Delete() }", False),
        ("Get-Item -Path app.log | ForEach-Object { $_.IsReadOnly = $true }", False),
        ("(Get-Item -Path app.log).Replace('b.log', 'c.log')", False),
        ("@(Get-Item -Path app.log).ForEach('Delete')", False),
        ("'out.txt' -as 'IO.StreamWriter'", False),
        ("[IO.StreamWriter]'out.txt'", False),
        ("[IO.File]::ReadAllText('/etc/passwd')", False),
        ("[string]::new('x')", False),
        ("switch -File /etc/passwd { default { $_ } }", False),
        ("Get-ChildItem -Path . &", False),
    ],
)
def test_a_read_only_task_computes_on_what_it_read_but_never_reaches_further(tmp_path, command, expected):
    assert routine_shell(command, tmp_path) is expected, command


def test_a_function_named_like_a_system_read_is_not_one(tmp_path):
    assert not routine_shell("function Get-CimInstance { Remove-Item x }; Get-CimInstance Win32_BIOS", tmp_path)
    assert not routine_shell("Get-CimInstance Win32_BIOS", tmp_path)  # no CimCmdlets here: nothing to prove
