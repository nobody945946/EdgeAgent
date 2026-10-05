"""The source archive SilentTalk's Windows download bundles."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_source", ROOT / "scripts" / "package_source.py")
package_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_source)


def test_players_get_the_package_and_installer_but_not_the_developers_files(tmp_path) -> None:
    result = package_source.build(tmp_path / "a.zip")
    names = set(zipfile.ZipFile(tmp_path / "a.zip").namelist())
    assert result["files"] == len(names)
    for needed in ("scripts/bootstrap.ps1", "pyproject.toml", "開啟 EdgeAgent.cmd", "edge_agent/web.py",
                   "edge_agent/skills/discussion/SKILL.md", "edge_agent/lingua/regex.py", "README.md"):
        assert needed in names, needed
    assert not any(n.startswith(("bench/", "scripts/windows-ai-maintenance/")) for n in names)
    assert "AGENTS.md" not in names


def test_the_same_commit_packs_to_the_same_bytes(tmp_path) -> None:
    first = package_source.build(tmp_path / "a.zip")
    second = package_source.build(tmp_path / "b.zip")
    assert first["sha256"] == second["sha256"]
