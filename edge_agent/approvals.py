"""An approval Pi asks for during a task: the host answers it itself where
the owner's shell rules allow, or explains the command for the owner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from edge_agent import undo
from edge_agent.lingua import powershell as lingua_powershell
from edge_agent.permissions import REVERSIBLE, reversible_shell, routine_shell


def explain_approval(details: dict, cwd: Path) -> dict[str, str]:
    """A shell command waiting for approval, in plain Chinese: what each part
    does and what it would delete, overwrite, run or reach, from PowerShell's
    own parser (edge_agent/lingua), never a model's reading. A failure only
    leaves the explanation out; the approval itself goes on."""
    command = details.get("arguments", {}).get("command") if isinstance(details.get("arguments"), dict) else None
    if details.get("tool") != "powershell" or not isinstance(command, str) or not command.strip():
        return {}
    try:
        return {"explain": lingua_powershell.translate(command, cwd=str(cwd), timeout=20).text(),
                "explain_title": "這個指令在做什麼（PowerShell 自己解析的結果，不是模型猜的）"}
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"explain_error": f"無法翻譯這個指令：{exc}"}


def host_answer(details: dict, rules: dict[str, str], cwd: Path) -> bool | None:
    """The host's own answer to an approval, or None when the owner is asked.
    Under "read" a provable read runs and anything else is refused. Under
    "reversible" an edit, or a command proven to read and write only files it
    names in the workspace, runs once those files are copied (edge_agent.undo);
    anything else (programs, the network, ...) asks, unless the owner already
    allowed this turn. Deleting and protected settings ("explicit") always ask."""
    tool, arguments = details.get("tool"), details.get("arguments")
    if not isinstance(arguments, dict):
        return None
    if tool == "powershell" and rules.get("shell") == "read":
        # "read" never asks the owner: a provable read runs, anything else is refused.
        return (not details.get("after_web") and details.get("explicit", "") in {"", "destructive"}
                and isinstance(arguments.get("command"), str) and routine_shell(arguments["command"], cwd))
    if details.get("explicit"):  # deleting, protected settings, an address the model built
        return None
    paths: list[Path] | None = None
    if tool in ("edit", "write") and rules.get("edit") == REVERSIBLE:
        # The bridge sends the path as Pi resolves it (~, @, file:// and all).
        path = details.get("resolved_path") or arguments.get("path")
        paths = [Path(path)] if isinstance(path, str) and path else None
    elif tool == "powershell" and rules.get("shell") == REVERSIBLE and isinstance(arguments.get("command"), str):
        paths = reversible_shell(arguments["command"], cwd)
    elif rules.get("edit") != REVERSIBLE and rules.get("shell") != REVERSIBLE:
        return None
    if paths is not None and undo.coverable(cwd, paths):
        try:
            if paths:
                undo.checkpoint(cwd, paths, f"{tool}: {arguments.get('command') or arguments.get('path')}")
            return True
        except (OSError, ValueError):
            pass  # a file that cannot be copied: the owner decides
    # What cannot be put back: the owner's "allow this turn" stands for it
    # (the bridge lifts it once web content enters the turn).
    return True if details.get("turn_approved") else None
