"""An approval Pi asks for during a task: the host answers it itself where
the owner's shell rules allow, or explains the command for the owner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from edge_agent.lingua import powershell as lingua_powershell
from edge_agent.permissions import routine_shell


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
    """The host's own answer to a shell approval under the "routine" and
    "read" rules (allow a provable read, or refuse), or None when the owner
    is asked."""
    shell = rules.get("shell")
    if details.get("tool") == "powershell" and shell in ("routine", "read"):
        provable = (
            not details.get("after_web")
            and details.get("explicit", "") in {"", "destructive"}
            and isinstance(details.get("arguments"), dict)
            and isinstance(details["arguments"].get("command"), str)
            and routine_shell(details["arguments"]["command"], cwd)
        )
        # "read" never asks the owner: a provable read
        # runs, anything else is refused.
        if provable or shell == "read":
            return provable
    return None
