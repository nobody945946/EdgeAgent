from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from silenttalk.config import load_settings

# SilentTalk is an E4B Core API client. It never owns or launches llama-server.
CONTROL_PORT = 18_087


class EdgeAIError(RuntimeError):
    """Raised when the independent E4B service cannot satisfy a request."""


def service_status(timeout: float = 1.0) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CONTROL_PORT}/status", timeout=timeout
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return value if isinstance(value, dict) else None


def _control_service_present(timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _global_e4b_command() -> str:
    command = shutil.which("e4b.cmd") or shutil.which("e4b")
    if command is None and os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        candidate = Path(local) / "Microsoft/WinGet/Links/e4b.cmd" if local else None
        if candidate is not None and candidate.is_file():
            command = str(candidate)
    if command is None:
        raise EdgeAIError(
            "找不到獨立 E4B Core；請執行啟動 SilentTalk.cmd，自動完成首次部署。"
        )
    return command


def ensure_service(timeout: float = 360.0) -> dict[str, object]:
    status = service_status()
    if status is None and not _control_service_present():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        command = _global_e4b_command()
        if os.name == "nt":
            argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command, "start"]
        else:
            argv = [command, "start"]
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )

    deadline = time.monotonic() + timeout
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        last = service_status()
        if last is not None and last.get("ready") is True:
            return last
        if last is not None and last.get("error"):
            raise EdgeAIError(str(last["error"]))
        time.sleep(0.25)
    raise EdgeAIError(f"E4B core did not become ready: {last}")


def post_json(
    path: str,
    payload: dict[str, object],
    *,
    timeout: float,
    require_ready: bool = True,
) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{CONTROL_PORT}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EdgeAIError(f"E4B core request failed ({exc.code}): {detail}") from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise EdgeAIError(f"E4B core request failed: {exc}") from exc
    if not isinstance(value, dict) or (
        require_ready and value.get("ready") is not True
    ):
        raise EdgeAIError(f"E4B core request did not become ready: {value}")
    return value


def acquire_character(project_dir: Path, owner_pid: int) -> dict[str, object]:
    settings = load_settings(project_dir).copilot
    ensure_service()
    return post_json(
        "/character",
        {
            "owner_pid": owner_pid,
            "character": {
                "model": settings.llm_model,
                "lora": settings.character_lora,
                "port": settings.llama_port,
                "context_size": settings.llm_context_size,
                "gpu_layers": settings.llm_gpu_layers,
                "low_vram": settings.llm_low_vram,
            },
        },
        timeout=360,
    )


def release_character(owner_pid: int) -> dict[str, object]:
    return post_json("/base", {"owner_pid": owner_pid}, timeout=360)


def hold_restart_handoff() -> dict[str, object]:
    return post_json("/handoff", {}, timeout=5)
