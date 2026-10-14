"""The control service as its clients reach it: started if it is not running,
waited for until it is ready, stopped. Every wait is woken by the service
itself (the owner, 2026-10-14: nothing asks at a fixed interval): its own
"listening" line when this process launched it, its status stream (GET
/status/events) for readiness, and its process's exit for a stop."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from edge_agent.runtime import (
    CONTROL_PORT,
    CORE_ROOT,
    SERVICE_LISTENING,
    EdgeAgentError,
)
from edge_agent.waits import EventStream, OutputLines, ProcessExit

SHUTDOWN_SECONDS = 10


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


def service_command() -> list[str]:
    return [sys.executable, "-m", "edge_agent.service"]


def _launch(deadline: float) -> None:
    """Start the service; back once it says it is listening
    (service.announce_listening), its port being ours to ask. It says so on a
    pipe of its own (--ready-pipe: an fd on Linux, a handle on Windows), which
    it closes after that one line; its stdout stays the null device."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    read, write = os.pipe()
    try:
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(write)
            os.set_handle_inheritable(handle, True)
            given, inherit = handle, {
                "startupinfo": subprocess.STARTUPINFO(lpAttributeList={"handle_list": [handle]})
            }
        else:
            given, inherit = write, {"pass_fds": (write,)}
        process = subprocess.Popen(
            [*service_command(), "--ready-pipe", str(given)],
            cwd=CORE_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
            **inherit,
        )
    except BaseException:
        os.close(read)
        raise
    finally:
        os.close(write)  # the service has its own: the pipe ends when it lets go
    said = SERVICE_LISTENING.encode()
    output = OutputLines(
        open(read, "rb"),  # noqa: SIM115 - OutputLines closes it at the pipe's end
        lambda line: line.startswith(said), name="edge-agent-launch",
    )
    if output.wait(max(deadline - time.monotonic(), 0)):
        return
    if not output.ended:
        raise EdgeAgentError("EdgeAgent did not start listening in time")
    # The pipe ended without the line: it exited. Another launch may have
    # taken the port first (two clients at once); then that one serves.
    if not control_service_present():
        raise EdgeAgentError(
            f"EdgeAgent exited ({process.wait()}) before it was listening"
        )


def ensure_service(
    timeout: float = 360.0, *, require_ready: bool = True
) -> dict[str, object]:
    """The service's status, once it runs (started here if nothing listens on
    its port) and, with `require_ready`, has its model ready: the status
    stream's first status, else the first that is ready. A status with an
    error before that raises it."""
    deadline = time.monotonic() + timeout
    status = service_status()
    if status is not None and (not require_ready or status.get("ready") is True):
        return status
    if status is None and not control_service_present():
        _launch(deadline)
    last: dict[str, object] | None = status
    try:
        stream = EventStream(
            CONTROL_PORT, "/status/events", timeout=max(deadline - time.monotonic(), 0.001)
        )
    except OSError as exc:
        raise EdgeAgentError(f"EdgeAgent status stream failed: {exc}") from exc
    try:
        for data in stream.events(deadline):
            value = json.loads(data)
            if not isinstance(value, dict):
                continue
            last = value
            if not require_ready or value.get("ready") is True:
                return value
            if value.get("error"):
                raise EdgeAgentError(str(value["error"]))
    except TimeoutError:
        raise EdgeAgentError(f"EdgeAgent did not become ready: {last}") from None
    except (OSError, ValueError) as exc:
        raise EdgeAgentError(f"EdgeAgent status stream failed: {exc}") from exc
    finally:
        stream.close()
    raise EdgeAgentError(f"EdgeAgent stopped before it was ready: {last}")


def request_base() -> dict[str, object]:
    status = ensure_service()
    if status.get("mode") == "base":
        return status
    return post_json("/base", {}, timeout=360)


def shutdown_service() -> dict[str, object]:
    """Stop the service; back once its process has exited (a kernel wait on
    the process its status names), or SHUTDOWN_SECONDS later."""
    status = service_status()
    if status is None:
        return {"mode": "stopped", "ready": False}
    pid = status.get("pid")
    service = None
    if isinstance(pid, int) and not isinstance(pid, bool):
        try:
            # Opened before the request: the id cannot be another process's by then.
            service = ProcessExit.open(pid)
        except OSError:
            service = None  # not ours to wait on: back once it answered
    try:
        result = post_json("/shutdown", {}, timeout=30, require_ready=False)
        if service is not None:
            service.wait(SHUTDOWN_SECONDS)
    finally:
        if service is not None:
            service.close()
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
        raise EdgeAgentError(
            f"EdgeAgent request failed ({exc.code}): {detail}"
        ) from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise EdgeAgentError(f"EdgeAgent request failed: {exc}") from exc
    if not isinstance(value, dict) or (
        require_ready and value.get("ready") is not True
    ):
        raise EdgeAgentError(f"EdgeAgent request did not become ready: {value}")
    return value
