"""Undo for the Agent's changes: what makes running them without asking safe.

The owner (2026-10-08): approving every command is tiring; anything that can
be traced and put back may run, like an editor's undo. Before an edit, a
write, or a PowerShell command that writes known files, the host copies those
files here (or notes that they did not exist); restoring a checkpoint puts
them back as they were and undoes every later checkpoint too. What cannot be
put back this way (deleting, system changes, data sent out) still asks.

Copies live in EdgeAgent's own folder, one journal per workspace, never in the
workspace itself. A file over MAX_BYTES is not copied: that change asks.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

from edge_agent.runtime import CORE_ROOT

ROOT = CORE_ROOT / ".runtime/undo"
MAX_BYTES = 50 * 2**20
KEEP = 200  # checkpoints per workspace; the oldest go first
_lock = threading.Lock()


def journal(workspace: Path) -> Path:
    key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:16]
    return ROOT / key


def coverable(workspace: Path, paths: list[Path]) -> bool:
    """Every path is inside the workspace and small enough to copy."""
    root = workspace.resolve()
    for path in paths:
        target = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not target.is_relative_to(root) or target == root or target.is_dir():
            return False
        if target.is_file() and target.stat().st_size > MAX_BYTES:
            return False
    return True


def checkpoint(workspace: Path, paths: list[Path], label: str) -> str:
    """Copy `paths` as they are now; returns the checkpoint id. Written whole
    or not at all: a file that cannot be copied raises OSError and leaves no
    checkpoint behind."""
    root = workspace.resolve()
    with _lock:
        folder = journal(root)
        folder.mkdir(parents=True, exist_ok=True)
        existing = sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / "manifest.json").is_file())
        number = int(existing[-1].split("-")[0]) + 1 if existing else 1
        name = f"{number:06d}-{int(time.time())}"
        partial = folder / (name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir()
        try:
            files = []
            for index, path in enumerate(paths):
                target = (root / path).resolve() if not path.is_absolute() else path.resolve()
                entry = {"path": str(target.relative_to(root)), "existed": target.is_file()}
                if target.is_file():
                    shutil.copy2(target, partial / f"{index}.bak")
                    entry["copy"] = f"{index}.bak"
                files.append(entry)
            (partial / "manifest.json").write_text(json.dumps(
                {"workspace": str(root), "label": label[:300], "time": time.time(), "files": files},
                ensure_ascii=False), encoding="utf-8")
            partial.rename(folder / name)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        for old in existing[: max(0, len(existing) + 1 - KEEP)]:
            shutil.rmtree(folder / old, ignore_errors=True)
        (folder / "workspace.txt").write_text(str(root), encoding="utf-8")
        return name


def history(workspace: Path) -> list[dict]:
    """The checkpoints, newest first: id, time, what the change was, which files."""
    folder = journal(workspace)
    if not folder.is_dir():
        return []
    found = []
    for point in sorted((p for p in folder.iterdir() if (p / "manifest.json").is_file()), reverse=True):
        manifest = json.loads((point / "manifest.json").read_text(encoding="utf-8"))
        found.append({"id": point.name, "time": manifest["time"], "label": manifest["label"],
                      "files": [f["path"] for f in manifest["files"]]})
    return found


def restore(workspace: Path, point_id: str) -> list[str]:
    """Undo checkpoint `point_id` and every later one, newest first; returns the files put back."""
    root = workspace.resolve()
    folder = journal(root)
    with _lock:
        points = sorted((p for p in folder.iterdir() if (p / "manifest.json").is_file() and p.name >= point_id),
                        reverse=True) if folder.is_dir() else []
        if not points or points[-1].name != point_id:
            raise ValueError(f"no checkpoint {point_id}")
        restored = []
        for point in points:
            manifest = json.loads((point / "manifest.json").read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                target = root / entry["path"]
                if entry["existed"]:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(point / entry["copy"], target)
                elif target.is_file():
                    target.unlink()
                restored.append(entry["path"])
            shutil.rmtree(point)
        return sorted(set(restored))
