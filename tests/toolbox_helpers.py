"""What the toolbox tests share: calling a tool, writing files, comparing folders."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from edge_agent.toolbox import REGISTRY

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, root: Path, **params) -> str:
    return REGISTRY[name].run(params, root)


def preview(name: str, root: Path, **params) -> str:
    return REGISTRY[name].preview(params, root)


def snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    """Every file's bytes and modified time, and every folder, under root."""
    state: dict[str, tuple[bytes, int] | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        state[rel] = (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
    return state


def arrows(result: str) -> list[str]:
    """The per-item plan lines (`- a → b`) of a preview or a run."""
    return sorted(line for line in result.splitlines() if line.startswith("- ") and "→" in line)


def write(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def cli(*args: str, stdin: str = "", cwd: Path = ROOT) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-m", "edge_agent.toolbox", *args], input=stdin.encode(), cwd=cwd,
                          capture_output=True, timeout=120, check=False, env=env)
    return done.returncode, done.stdout.decode("utf-8")
