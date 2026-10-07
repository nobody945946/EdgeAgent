"""The folders the UI works in: browsing directories, a project's Git branch,
and a separate Git worktree for a task."""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4


def list_directories(value: object = None) -> dict[str, object]:
    if value is None or value == "":
        value = str(Path.home())
    if not isinstance(value, str):
        raise TypeError("path must be a string")
    current = Path(value).expanduser().resolve()
    if not current.is_dir():
        raise ValueError(f"directory does not exist: {current}")
    try:
        directories = sorted(
            (
                {"name": child.name, "path": str(child.resolve())}
                for child in current.iterdir()
                if child.is_dir()
            ),
            key=lambda item: str(item["name"]).casefold(),
        )
    except OSError as exc:
        raise ValueError(f"cannot read directory: {current}") from exc
    parent = current.parent
    return {
        "current": str(current),
        "parent": str(parent) if parent != current else None,
        "directories": directories,
    }


def workspace_info(directory: str) -> dict[str, object]:
    path = Path(directory).resolve()
    if not path.is_dir():
        raise ValueError("workspace directory does not exist")
    branch = subprocess.run(
        ["git", "-C", str(path), "branch", "--show-current"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    return {
        "path": str(path),
        "git": branch.returncode == 0,
        "branch": branch.stdout.strip() if branch.returncode == 0 else "",
    }


def create_worktree(directory: str, core_dir: Path) -> dict[str, object]:
    """A new branch checked out under the core's .worktrees, from the project's HEAD."""
    info = workspace_info(directory)
    if not info["git"]:
        raise ValueError("open a Git project before creating a worktree")
    token = uuid4().hex[:12]
    branch = "edge-agent/" + token
    destination = core_dir / ".worktrees" / token
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(info["path"]),
            "worktree",
            "add",
            "-b",
            branch,
            str(destination),
            "HEAD",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError("worktree creation failed: " + result.stderr.strip())
    return {"path": str(destination), "branch": branch, "git": True}
