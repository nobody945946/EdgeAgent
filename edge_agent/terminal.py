from __future__ import annotations

import base64
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4


class TerminalSession:
    """A persistent, RAM-only PowerShell session belonging to one conversation."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        if not self.cwd.is_dir():
            raise ValueError("terminal workspace must be an existing directory")
        self.lock = threading.Lock()
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.cancelled = threading.Event()
        environment = os.environ.copy()
        scripts = Path(__file__).resolve().parents[1] / ".venv" / "Scripts"
        environment["PATH"] = str(scripts) + os.pathsep + environment.get("PATH", "")
        environment["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "-",
            ],
            cwd=self.cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._read, daemon=True).start()
        assert self.process.stdin is not None
        self.process.stdin.write(
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
            "$OutputEncoding = [Console]::OutputEncoding\n"
        )
        self.process.stdin.flush()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def close(self) -> None:
        self.cancelled.set()
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            else:
                self.process.terminate()
            self.process.wait(timeout=10)
        for handle in (self.process.stdin, self.process.stdout):
            if handle is not None:
                handle.close()

    def run(
        self, command: str, timeout: int = 300
    ) -> Iterator[tuple[str, dict[str, object]]]:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("terminal command must be non-empty")
        if not self.lock.acquire(blocking=False):
            raise ValueError("terminal is already running a command")
        try:
            marker = "EDGE_AGENT_DONE_" + uuid4().hex
            encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
            script = (
                "$global:LASTEXITCODE=0; $agentResult=0; try { "
                f"Invoke-Expression ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))); "
                "if (-not $?) { $agentResult=1 } elseif ($global:LASTEXITCODE) { $agentResult=$global:LASTEXITCODE } "
                "} catch { Write-Output $_; $agentResult=1 }; "
                "$agentCwd=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Location).Path)); "
                f"Write-Output ''; Write-Output ('{marker}:'+ $agentResult + ':' + $agentCwd)\n"
            )
            assert self.process.stdin is not None
            self.process.stdin.write(script)
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                if self.cancelled.is_set():
                    yield "cancelled", {"cwd": str(self.cwd)}
                    return
                if time.monotonic() >= deadline:
                    self.close()
                    yield "error", {"message": f"terminal timed out after {timeout}s"}
                    return
                try:
                    line = self.lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    yield (
                        "done",
                        {"exit_code": self.process.poll(), "cwd": str(self.cwd)},
                    )
                    return
                if line.startswith(marker + ":"):
                    _, code, directory = line.strip().split(":", 2)
                    self.cwd = Path(base64.b64decode(directory).decode("utf-8"))
                    yield "done", {"exit_code": int(code), "cwd": str(self.cwd)}
                    return
                yield "delta", {"text": line}
        finally:
            self.lock.release()


class NativeTerminal:
    def __init__(self) -> None:
        self.sessions: dict[str, TerminalSession] = {}
        self.lock = threading.Lock()

    def session(self, session_id: str, cwd: Path) -> TerminalSession:
        with self.lock:
            current = self.sessions.get(session_id)
            if current is None or current.process.poll() is not None:
                current = TerminalSession(cwd if current is None else current.cwd)
                self.sessions[session_id] = current
            return current

    def close(self, session_id: str) -> None:
        with self.lock:
            current = self.sessions.pop(session_id, None)
        if current is not None:
            current.close()

    def close_all(self) -> None:
        with self.lock:
            ids = list(self.sessions)
        for session_id in ids:
            self.close(session_id)
