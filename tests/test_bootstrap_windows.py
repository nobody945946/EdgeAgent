"""Windows deployment owns its interpreter and Git, independent of other apps."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap")


def run_helper(script: str) -> dict:
    prelude = "$ErrorActionPreference = 'Stop'; . '" + str(ROOT / "scripts/bootstrap.ps1").replace("'", "''") + "' -Library; "
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prelude + script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    )
    return json.loads(result.stdout)


def quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


@pytest.mark.parametrize("existing", [False, True])
def test_venv_creation_or_migration_preserves_packages(tmp_path, existing):
    if existing:
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")], check=True, timeout=60)
        marker = tmp_path / ".venv/Lib/site-packages/kept.txt"
        marker.write_text("installed package")
    private_python = tmp_path / ".runtime/python/tools/python.exe"
    result = run_helper(
        "function Invoke-Checked { param($Exe, $Arguments); $script:called = @{exe=$Exe; arguments=$Arguments} }; "
        + "$venv = Ensure-ProjectVenv " + quote(tmp_path) + " " + quote(private_python)
        + "; @{venv=$venv; called=$script:called} | ConvertTo-Json -Depth 5 -Compress"
    )
    assert Path(result["venv"]) == tmp_path / ".venv/Scripts/python.exe"
    assert Path(result["called"]["exe"]) == private_python
    assert result["called"]["arguments"] == ["-m", "venv", *(["--upgrade"] if existing else []), str(tmp_path / ".venv")]
    if existing:
        assert marker.read_text() == "installed package"


def test_owned_venv_is_reused_without_reinstallation(tmp_path):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")], check=True, timeout=60)
    result = run_helper(
        "function Invoke-Checked { throw 'An already-owned environment must not be rebuilt' }; "
        + "$venv = Ensure-ProjectVenv " + quote(tmp_path) + " " + quote(Path(sys._base_executable))
        + "; @{venv=$venv} | ConvertTo-Json -Compress"
    )
    assert Path(result["venv"]) == tmp_path / ".venv/Scripts/python.exe"


def test_git_uses_owned_runtime_even_when_global_git_exists(tmp_path):
    result = run_helper(
        "function Get-VerifiedFile { param($Url,$Target,$Sha256); $script:download=@{url=$Url; target=$Target; sha256=$Sha256} }; "
        "function Expand-Archive { param($LiteralPath,$DestinationPath,[switch]$Force); $script:destination=$DestinationPath }; "
        "function Invoke-Checked { param($Exe,$Arguments); $script:executable=$Exe }; "
        + "Ensure-Git " + quote(tmp_path) + " " + quote(tmp_path / ".runtime/downloads")
        + "; @{executable=$script:executable; destination=$script:destination; download=$script:download} | ConvertTo-Json -Depth 5 -Compress"
    )
    assert Path(result["executable"]) == tmp_path / ".runtime/git/cmd/git.exe"
    assert Path(result["destination"]) == tmp_path / ".runtime/git"
    assert len(result["download"]["sha256"]) == 64


def test_python_is_provisioned_inside_the_application_without_system_install(tmp_path):
    result = run_helper(
        "function Get-VerifiedFile { param($Url,$Target,$Sha256); $script:download=@{url=$Url; target=$Target; sha256=$Sha256} }; "
        "function Expand-Archive { param($LiteralPath,$DestinationPath,[switch]$Force); $script:destination=$DestinationPath }; "
        "function Invoke-Checked { param($Exe,$Arguments); $script:executable=$Exe }; "
        "function Start-Process { throw 'Portable Python must not launch a system installer' }; "
        + "$python = Ensure-EdgePython312 " + quote(tmp_path) + " " + quote(tmp_path / ".runtime/downloads")
        + "; @{python=$python; executable=$script:executable; destination=$script:destination; download=$script:download} | ConvertTo-Json -Depth 5 -Compress"
    )
    assert Path(result["python"]) == tmp_path / ".runtime/python/tools/python.exe"
    assert result["python"] == result["executable"]
    assert Path(result["destination"]) == tmp_path / ".runtime/python"
    assert result["download"]["url"] == "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg"
    assert len(result["download"]["sha256"]) == 64
