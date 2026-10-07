"""The llama.cpp router child on the inference port: started once, told which
model to load, asked what it loaded, and stopped. The upstream router owns
model processes, loading and hot replacement."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from edge_agent.processes import clear_reserved_port
from edge_agent.runtime import GENERAL_PORT, find_llama_server


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


class ModelRouter:
    """Owns the router process and the one public inference endpoint."""

    def __init__(self, core_dir: Path) -> None:
        self.core_dir = core_dir
        # Read when the core is built (tests/windows_check.py moves the port).
        self.port = GENERAL_PORT
        self.process: subprocess.Popen[bytes] | None = None

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
        if self.process is None or self.process.poll() is not None:
            clear_reserved_port(self.port)
            self.process = subprocess.Popen(
                [
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
                ],
                cwd=self.core_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 30
            while not _health_ok(self.port):
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"model router exited with {self.process.returncode}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("model router did not become ready")
                time.sleep(0.1)
        self.request("/models?reload=1", timeout=60)
        if self.state().get("value") not in {"loaded", "loading"}:
            result = self.request(
                "/models/load", {"model": "local"}, timeout=300
            )
            if result.get("success") is not True:
                raise RuntimeError("model router rejected loading the selected model")
        deadline = time.monotonic() + 300
        while True:
            state = self.state()
            if state.get("failed"):
                raise RuntimeError(
                    f"model failed to load (exit {state.get('exit_code')})"
                )
            if state.get("value") == "loaded":
                break
            if self.process.poll() is not None:
                raise RuntimeError("model router exited during loading")
            if time.monotonic() >= deadline:
                raise TimeoutError("selected model did not become ready")
            time.sleep(0.1)

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

    def stop(self) -> None:
        process = self.process
        self.process = None
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
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
