"""The llama.cpp router child on the inference port: started once, told which
model to load, asked what it loaded, and stopped. The upstream router owns
model processes, loading and hot replacement.

Nothing here asks at an interval (the owner, 2026-10-14):
- the router is ready when it says so on its output, which a thread reads for
  the router's whole life: "srv  llama_server: listening on http://..."
  (llama.cpp b11193 tools/server/server.cpp, printed once the listener is
  bound);
- its model's state changes come from the router's own event stream, GET
  /models/sse (server-models.cpp: a "status_change" each time a model's
  status is set, among them "unloaded" with the worker's exit_code when a
  worker exits, crashed or not). Each event wakes whoever waits on the model
  (a load here, and the core's monitor through `on_event`), and they read the
  state (GET /models) once per wake;
- the router's own exit: a thread waits on the process in the kernel
  (RouterProcess) and wakes them too.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from edge_agent.processes import clear_reserved_port
from edge_agent.runtime import GENERAL_PORT, find_llama_server
from edge_agent.waits import EventStream, OutputLines

ROUTER_START_SECONDS = 30
MODEL_LOAD_SECONDS = 300
# The router ends its event stream when it shuts down, then stops its models
# (each within its stop timeout, 10 s) and exits. Still running after this,
# it lost the stream some other way, and the stream is opened again.
STREAM_GONE_SECONDS = 15


class RouterProcess(subprocess.Popen):
    """The router child, its exit an event (`exited`): from its start a
    thread waits on it in the kernel (waitpid / WaitForSingleObject), then
    calls `on_exit`."""

    def __init__(self, args: list[str], on_exit: Callable[[], None], **options: object) -> None:
        super().__init__(args, **options)
        self.exited = threading.Event()
        threading.Thread(
            target=self._await_exit, args=(on_exit,), name="model-router-exit", daemon=True
        ).start()

    def _await_exit(self, on_exit: Callable[[], None]) -> None:
        self.wait()
        self.exited.set()
        on_exit()


class ModelRouter:
    """Owns the router process and the one public inference endpoint."""

    def __init__(self, core_dir: Path, on_event: Callable[[], None] = lambda: None) -> None:
        self.core_dir = core_dir
        # Read when the core is built (tests/windows_check.py moves the port).
        self.port = GENERAL_PORT
        self.process: RouterProcess | None = None
        # Called, on the router's threads, for each model event and when the
        # router exits.
        self.on_event = on_event
        self._changes = threading.Condition()
        self._count = 0
        self._stream: EventStream | None = None

    def running(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    def request(
        self, endpoint: str, payload: dict[str, object] | None = None, *, timeout: float = 5
    ) -> dict[str, object]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}" + endpoint,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc
        if not isinstance(result, dict):
            raise TypeError("invalid model router response")
        return result

    def state(self) -> dict[str, object]:
        models = self.request("/models", timeout=2).get("data", [])
        return next(
            (item.get("status", {}) for item in models if item.get("id") == "local"), {}
        )

    def _publish(self) -> None:
        """A model event, or the router's exit."""
        with self._changes:
            self._count += 1
            self._changes.notify_all()
        self.on_event()

    def _seen(self) -> int:
        with self._changes:
            return self._count

    def _wait_event(self, seen: int, timeout: float) -> bool:
        """Until an event after `seen` (a count); False at the timeout."""
        with self._changes:
            return self._changes.wait_for(lambda: self._count != seen, max(timeout, 0))

    def load(self, args: list[str]) -> None:
        """Load the model a llama-server command line describes, as the
        router's one preset, and wait until it is loaded."""
        preset = self.core_dir / ".runtime/models.ini"
        preset.parent.mkdir(parents=True, exist_ok=True)
        lines = ["version = 1", "", "[local]"]
        index = 1
        while index < len(args):
            key = args[index].removeprefix("--")
            index += 1
            value = "true"
            if index < len(args) and not args[index].startswith("--"):
                value = args[index].replace("\\", "/")
                index += 1
            if key not in {"host", "port"}:
                lines.append(f"{key} = {value}")
        temporary = preset.with_suffix(".ini.part")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(preset)
        if not self.running():
            self._start(preset)
        self.request("/models?reload=1", timeout=60)
        if self.state().get("value") not in {"loaded", "loading"}:
            result = self.request(
                "/models/load", {"model": "local"}, timeout=300
            )
            if result.get("success") is not True:
                raise RuntimeError("model router rejected loading the selected model")
        deadline = time.monotonic() + MODEL_LOAD_SECONDS
        while True:
            seen = self._seen()  # before reading: an event after this ends the wait
            if not self.running():
                raise RuntimeError("model router exited during loading")
            state = self.state()
            if state.get("failed"):
                raise RuntimeError(
                    f"model failed to load (exit {state.get('exit_code')})"
                )
            if state.get("value") == "loaded":
                return
            if not self._wait_event(seen, deadline - time.monotonic()):
                raise TimeoutError("selected model did not become ready")

    def _command(self, preset: Path) -> list[str]:
        return [
            find_llama_server(),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--models-preset",
            str(preset),
            "--models-max",
            "1",
            "--no-models-autoload",
        ]

    def _start(self, preset: Path) -> None:
        """Start the router: back once it is listening and its model events
        are followed (subscribed before any load, so none is missed)."""
        clear_reserved_port(self.port)
        process = self.process = RouterProcess(
            self._command(preset),
            self._publish,
            cwd=self.core_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            listening = f"listening on http://127.0.0.1:{self.port}".encode()
            output = OutputLines(
                process.stdout, lambda line: listening in line, name="model-router-output"
            )
            if not output.wait(ROUTER_START_SECONDS):
                if output.ended:  # it closed its output: it is exiting
                    process.exited.wait()
                    raise RuntimeError(f"model router exited with {process.returncode}")
                raise TimeoutError("model router did not become ready")
            stream = self._stream = EventStream(self.port, "/models/sse", timeout=5)
        except BaseException:
            self._kill(process)
            raise
        threading.Thread(
            target=self._follow, args=(process, stream), name="model-router-events",
            daemon=True,
        ).start()

    def _follow(self, process: RouterProcess, stream: EventStream) -> None:
        """Each model event wakes the waiters. The stream is opened again if
        it ends while the router keeps running."""
        lost = STREAM_GONE_SECONDS
        while True:
            try:
                for _ in stream.events():
                    self._publish()
            except OSError:
                pass  # closed by stop(), or the connection broke
            stream.close()
            if process.exited.wait(lost) or self.process is not process:
                return
            try:
                stream = self._stream = EventStream(self.port, "/models/sse", timeout=5)
            except OSError:
                lost *= 2  # not answering yet: longer before the next try
                continue
            lost = STREAM_GONE_SECONDS
            self._publish()  # an event may have come while it was lost

    def capabilities(self, context_size: int) -> dict[str, bool]:
        """The loaded model's input kinds, once its window is the one asked for."""
        properties = self.request("/props?model=local")
        actual_context = properties.get("default_generation_settings", {}).get("n_ctx")
        if actual_context != context_size:
            raise RuntimeError(
                f"引擎實際 context {actual_context} 與要求的 {context_size} 不符"
            )
        modalities = properties.get("modalities", {})
        return {
            "vision": isinstance(modalities, dict) and modalities.get("vision") is True,
            "audio": isinstance(modalities, dict) and modalities.get("audio") is True,
        }

    @staticmethod
    def _kill(process: RouterProcess) -> None:
        """The router and its models, now."""
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        process.kill()
        process.exited.wait()

    def stop(self) -> None:
        process, stream = self.process, self._stream
        self.process = self._stream = None
        try:
            if process is None or process.poll() is not None:
                return
            try:
                self.request("/models/unload", {"model": "local"}, timeout=15)
            except (OSError, RuntimeError, TypeError):
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
            process.terminate()
            if not process.exited.wait(5):
                process.kill()
                process.exited.wait()
        finally:
            if stream is not None:
                stream.close()
