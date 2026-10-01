from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from e4b_core.runtime import CONTROL_PORT, CORE_ROOT, E4BCoreError


def service_status(timeout: float = 1.0) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CONTROL_PORT}/status", timeout=timeout
        ) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return value if isinstance(value, dict) else None


def control_service_present(timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_service(timeout: float = 360.0) -> dict[str, object]:
    status = service_status()
    if status is None and not control_service_present():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "e4b_core.service",
            ],
            cwd=CORE_ROOT,
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
            raise E4BCoreError(str(last["error"]))
        time.sleep(0.25)
    raise E4BCoreError(f"E4B core did not become ready: {last}")


def request_base() -> dict[str, object]:
    status = ensure_service()
    if status.get("mode") == "base":
        return status
    return post_json("/base", {}, timeout=360)


def shutdown_service() -> dict[str, object]:
    status = service_status()
    if status is None:
        return {"mode": "stopped", "ready": False}
    result = post_json("/shutdown", {}, timeout=30, require_ready=False)
    deadline = time.monotonic() + 10
    while control_service_present() and time.monotonic() < deadline:
        time.sleep(0.1)
    return result


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
        raise E4BCoreError(f"E4B core request failed ({exc.code}): {detail}") from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise E4BCoreError(f"E4B core request failed: {exc}") from exc
    if not isinstance(value, dict) or (
        require_ready and value.get("ready") is not True
    ):
        raise E4BCoreError(f"E4B core request did not become ready: {value}")
    return value
