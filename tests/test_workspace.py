"""The folders the UI opens (workspace.py): the directory picker and a
task's own Git worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edge_agent import workspace


def test_directory_picker_lists_real_unicode_directories(tmp_path: Path) -> None:
    (tmp_path / "故事").mkdir()
    (tmp_path / "not-a-folder.txt").write_text("x", encoding="utf-8")

    listing = workspace.list_directories(str(tmp_path))

    assert {item["name"] for item in listing["directories"]} == {"故事"}
    with pytest.raises(ValueError, match="directory does not exist"):
        workspace.list_directories(str(tmp_path / "missing"))


def test_worktree_is_real_and_original_workspace_is_preserved(tmp_path: Path) -> None:
    project = tmp_path / "專案"
    project.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )

    git("init", "-b", "copilot-story-mode")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (project / "code.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "code.py")
    git("commit", "-m", "fixture")
    result = workspace.create_worktree(str(project), tmp_path / "core")
    worktree = Path(result["path"])
    assert worktree.is_dir()
    assert workspace.workspace_info(str(worktree))["branch"] == result["branch"]
    assert (worktree / "code.py").read_text() == "value = 1\n"
    (worktree / "code.py").write_text("value = 2\n", encoding="utf-8")
    assert (project / "code.py").read_text() == "value = 1\n"
