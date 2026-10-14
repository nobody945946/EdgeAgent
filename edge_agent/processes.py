"""Process lookups by exact PID: whether a lease owner still lives, and what
listens on a port the core reserves (Windows only)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from edge_agent.waits import ProcessExit


def pid_alive(pid: int) -> bool:
    """The process has not exited: the same kernel object the core's monitor
    waits on (waits.ProcessExit), looked at once."""
    try:
        process = ProcessExit.open(pid)
    except PermissionError:
        return True  # it runs, as another user's or an elevated process
    except OSError:
        return False
    if process is None:
        return False
    with process:
        return not process.exited()


def listening_pid(port: int) -> int | None:
    """Return the exact Windows PID listening on a reserved core port."""
    if os.name != "nt":
        return None
    listing = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        check=False,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = listing.stdout.decode(errors="replace")
    endpoint = re.compile(rf"^(?:\[.*\]|[^:]+):{port}$")
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) >= 5
            and fields[3].upper() == "LISTENING"
            and endpoint.match(fields[1])
            and fields[-1].isdigit()
        ):
            return int(fields[-1])
    return None


def _process_image(pid: int) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32_768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return Path(buffer.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def clear_reserved_port(port: int) -> None:
    """Recover only an orphaned llama-server on an EdgeAgent-owned model port."""
    pid = listening_pid(port)
    if pid is None:
        return
    image = _process_image(pid)
    if image is None or image.name.lower() != "llama-server.exe":
        raise RuntimeError(f"reserved EdgeAgent port {port} is occupied by PID {pid}")
    import ctypes

    terminate = 0x0001
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(terminate | synchronize, False, pid)
    if not handle:
        raise RuntimeError(f"cannot recover orphaned llama-server PID {pid}")
    try:
        if not ctypes.windll.kernel32.TerminateProcess(handle, 1):
            raise RuntimeError(f"cannot terminate orphaned llama-server PID {pid}")
        ctypes.windll.kernel32.WaitForSingleObject(handle, 5_000)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    if listening_pid(port) is not None:
        raise RuntimeError(
            f"orphaned llama-server PID {pid} did not release port {port}"
        )
