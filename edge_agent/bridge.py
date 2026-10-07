"""The Pi agent process (agent_bridge.mjs) and its JSON-lines RPC.

One process at a time, serving one RAM session: started with that session's
entries, and checkpointed back into it, so nothing is kept on disk.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from edge_agent.chat_session import ChatSession
from edge_agent.runtime import NODE_VERSION


class PiBridge:
    """The running Pi process, the session it serves, and its event stream."""

    def __init__(self, core_dir: Path) -> None:
        self.core_dir = core_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.active: ChatSession | None = None
        # What the process was started with; another key needs a new process.
        self.key: tuple[object, ...] | None = None
        self._events: queue.Queue[dict[str, object]] = queue.Queue()
        self._write_lock = threading.Lock()
        self._buffer: deque[dict[str, object]] = deque()

    def alive(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    def spawn(self, cwd: Path, session: ChatSession, key: tuple[object, ...]) -> None:
        node = self.core_dir / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
        bridge = self.core_dir / "edge_agent/agent_bridge.mjs"
        if not node.is_file():
            raise RuntimeError(
                "Pi runtime is not installed; run the existing setup command"
            )
        self._events = queue.Queue()
        self._buffer.clear()
        process = subprocess.Popen(
            [str(node), str(bridge)],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
                "PATH": str(self.core_dir / ".runtime/tools")
                + os.pathsep
                + os.environ.get("PATH", ""),
            },
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.process, self.active, self.key = process, session, key
        events = self._events

        def read_protocol() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            events.put(value)
                    except ValueError:
                        events.put({"type": "protocol_error"})
            except OSError:
                pass
            finally:
                events.put({"type": "eof"})

        threading.Thread(target=read_protocol, daemon=True, name="pi-rpc").start()

    def send(self, record: dict[str, object]) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Pi agent process is unavailable")
        with self._write_lock:
            process.stdin.write((json.dumps(record, ensure_ascii=True) + "\n").encode())
            process.stdin.flush()

    def receive(self, timeout: float = 120) -> dict[str, object]:
        if self._buffer:
            return self._buffer.popleft()
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                "Agent did not produce an event before the deadline"
            ) from exc
        if event.get("type") == "eof":
            raise RuntimeError("Pi agent exited; completed work is retained in RAM")
        return event

    def request(self, command: dict[str, object]) -> dict[str, object]:
        command = {**command, "id": uuid4().hex}
        self.send(command)
        deadline = time.monotonic() + 15
        deferred: list[dict[str, object]] = []
        try:
            while time.monotonic() < deadline:
                event = self.receive(max(0.1, deadline - time.monotonic()))
                checkpoint = self.snapshot(event)
                if event.get("type") == "response" and event.get("id") == command["id"]:
                    if not event.get("success"):
                        raise RuntimeError(str(event.get("error", "Pi command failed")))
                    return event
                if not checkpoint and event.get("type") != "response":
                    deferred.append(event)
            raise TimeoutError("Pi control request timed out")
        finally:
            self._buffer.extendleft(reversed(deferred))

    def snapshot(self, event: dict[str, object]) -> bool:
        """Copy a checkpoint notice's entries into the active session."""
        if (
            event.get("type") != "extension_ui_request"
            or event.get("method") != "notify"
        ):
            return False
        message = event.get("message", "")
        if not isinstance(message, str) or not message.startswith("edge-snapshot:"):
            return False
        if self.active is not None:
            entries = json.loads(message[len("edge-snapshot:") :])
            if not isinstance(entries, list):
                raise TypeError("Invalid RAM checkpoint")
            self.active.entries = entries
            self.active.messages.clear()
        return True

    def checkpoint(self) -> None:
        if self.active and self.alive():
            self.request({"type": "prompt", "message": "/edge-snapshot"})

    def close(self) -> None:
        process, self.process = self.process, None
        self.active = None
        self.key = None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                process.terminate()
            process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
