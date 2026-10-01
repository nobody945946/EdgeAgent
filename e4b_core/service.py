from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from e4b_core.chat import THINKING_PROFILES, NativeChat, attachment_context
from e4b_core.runtime import (
    CONTROL_PORT,
    CORE_ROOT,
    DEFAULT_MODEL,
    GENERAL_CONTEXT_SIZE,
    GENERAL_PORT,
    MODEL_DIR,
    build_server_args,
    find_llama_server,
)
from e4b_core.terminal import NativeTerminal
from e4b_core.web import NATIVE_CHAT_HTML

DEFAULT_CONFIG: dict[str, object] = {
    "thinking_effort": "medium",
    "agent": True,
}


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _listening_pid(port: int) -> int | None:
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


def _clear_reserved_port(port: int) -> None:
    """Recover only an orphaned llama-server on an E4B-owned model port."""
    pid = _listening_pid(port)
    if pid is None:
        return
    image = _process_image(pid)
    if image is None or image.name.lower() != "llama-server.exe":
        raise RuntimeError(f"reserved E4B port {port} is occupied by PID {pid}")
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
    if _listening_pid(port) is not None:
        raise RuntimeError(
            f"orphaned llama-server PID {pid} did not release port {port}"
        )


class E4BCore:
    """Owns the model process; applications can only lease it through HTTP."""

    def __init__(self, core_dir: Path = CORE_ROOT) -> None:
        self.core_dir = core_dir.resolve()
        self.model_dir = (
            MODEL_DIR.resolve()
            if self.core_dir == CORE_ROOT.resolve()
            else (self.core_dir / "models").resolve()
        )
        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.mode = "starting"
        self.port = GENERAL_PORT
        self.owner_pid: int | None = None
        self.owner_dead_since: float | None = None
        self.handoff_until = 0.0
        self.error = ""
        self.loading = False
        self.config_path = self.core_dir / "config.json"
        self.config = self._load_config()
        self.chat = NativeChat(model_port=GENERAL_PORT)
        self.terminal = NativeTerminal()

    def _load_config(self) -> dict[str, object]:
        if not self.config_path.is_file():
            return dict(DEFAULT_CONFIG)
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return dict(DEFAULT_CONFIG)
            return self._validated_config(value)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return dict(DEFAULT_CONFIG)

    @staticmethod
    def _validated_config(value: dict[str, object]) -> dict[str, object]:
        unknown = set(value) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"unknown config fields: {', '.join(sorted(unknown))}")
        merged = {**DEFAULT_CONFIG, **value}
        effort = merged["thinking_effort"]
        if not isinstance(effort, str) or effort not in THINKING_PROFILES:
            raise ValueError("invalid thinking_effort")
        agent = merged["agent"]
        if not isinstance(agent, bool):
            raise TypeError("agent must be a boolean")
        return {
            "thinking_effort": effort,
            "agent": agent,
        }

    def update_config(self, value: dict[str, object]) -> dict[str, object]:
        config = self._validated_config({**self.config, **value})
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.config = config
        return dict(config)

    def list_directories(self, value: object = None) -> dict[str, object]:
        if value is None or value == "":
            value = str(Path.home())
        if not isinstance(value, str):
            raise TypeError("path must be a string")
        current = Path(value).expanduser().resolve()
        if not current.is_dir():
            raise ValueError(f"directory does not exist: {current}")
        try:
            directories = sorted(
                (
                    {"name": child.name, "path": str(child.resolve())}
                    for child in current.iterdir()
                    if child.is_dir()
                ),
                key=lambda item: str(item["name"]).casefold(),
            )
        except OSError as exc:
            raise ValueError(f"cannot read directory: {current}") from exc
        parent = current.parent
        return {
            "current": str(current),
            "parent": str(parent) if parent != current else None,
            "directories": directories,
        }

    def workspace_info(self, directory: str) -> dict[str, object]:
        path = Path(directory).resolve()
        if not path.is_dir():
            raise ValueError("workspace directory does not exist")
        branch = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return {
            "path": str(path),
            "git": branch.returncode == 0,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
        }

    def create_worktree(self, directory: str) -> dict[str, object]:
        info = self.workspace_info(directory)
        if not info["git"]:
            raise ValueError("open a Git project before creating a worktree")
        token = uuid4().hex[:12]
        branch = "e4b/" + token
        destination = self.core_dir / ".worktrees" / token
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "git",
                "-C",
                str(info["path"]),
                "worktree",
                "add",
                "-b",
                branch,
                str(destination),
                "HEAD",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise ValueError("worktree creation failed: " + result.stderr.strip())
        return {"path": str(destination), "branch": branch, "git": True}

    def status(self) -> dict[str, object]:
        process = self.process
        ready = (
            not self.loading
            and process is not None
            and process.poll() is None
            and _health_ok(self.port)
        )
        return {
            "service": "e4b-core",
            "mode": self.mode,
            "ready": ready,
            "port": self.port,
            "url": f"http://127.0.0.1:{CONTROL_PORT}/",
            "model_url": f"http://127.0.0.1:{self.port}",
            "owner_pid": self.owner_pid,
            "error": self.error,
        }

    def switch(
        self,
        mode: str,
        owner_pid: int | None = None,
        character: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with self.lock:
            if (
                mode == "base"
                and self.mode == "character"
                and self.owner_pid is not None
                and _pid_alive(self.owner_pid)
                and owner_pid != self.owner_pid
            ):
                raise PermissionError("SilentTalk still owns character mode")
            if (
                mode == self.mode
                and self.process is not None
                and self.process.poll() is None
                and _health_ok(self.port)
            ):
                if mode == "character" and owner_pid is not None:
                    self.owner_pid = owner_pid
                    self.owner_dead_since = None
                    self.handoff_until = 0.0
                return self.status()

            self.loading = True
            self.error = ""
            self._stop_child()
            try:
                self._start_child(mode, owner_pid, character)
            except Exception as exc:
                self.error = str(exc)
                self.loading = False
                if mode != "base":
                    try:
                        self._start_child("base", None, None)
                    except Exception as base_exc:  # noqa: BLE001 - preserve first failure
                        self.error += f"; base restore failed: {base_exc}"
                raise
            self.loading = False
            return self.status()

    def _start_child(
        self,
        mode: str,
        owner_pid: int | None,
        character: dict[str, object] | None,
    ) -> None:
        if mode == "base":
            model_path = self._model_path(DEFAULT_MODEL)
            lora_path = None
            context_size = GENERAL_CONTEXT_SIZE
            port = GENERAL_PORT
            gpu_layers = 99
            low_vram = True
        elif mode == "character":
            if character is None:
                raise ValueError("character configuration required")
            model_path = self._model_path(self._required_string(character, "model"))
            lora_name = self._required_string(character, "lora")
            lora_path = self._model_path(lora_name) if lora_name else None
            context_size = self._bounded_int(character, "context_size", 1_024, 32_768)
            port = self._bounded_int(character, "port", 1_024, 65_535)
            if port in (CONTROL_PORT, GENERAL_PORT):
                raise ValueError("character port conflicts with E4B core")
            gpu_layers = self._bounded_int(character, "gpu_layers", 0, 999)
            low_vram = character.get("low_vram") is True
        else:
            raise ValueError(f"unknown E4B core mode: {mode}")

        threads = max(2, min(6, (os.cpu_count() or 4) // 2))
        _clear_reserved_port(port)
        args = build_server_args(
            find_llama_server(),
            model_path,
            port=port,
            context_size=context_size,
            threads=threads,
            gpu_layers=gpu_layers,
            low_vram=low_vram,
            lora_path=lora_path,
            agent_tools=mode == "base",
        )
        self.process = subprocess.Popen(
            args,
            cwd=self.core_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.mode = mode
        self.port = port
        self.owner_pid = owner_pid if mode == "character" else None
        self.owner_dead_since = None
        if mode == "character":
            self.handoff_until = 0.0
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.process = None
                raise RuntimeError(f"llama-server exited with {code}")
            if _health_ok(port) and (
                os.name != "nt" or _listening_pid(port) == self.process.pid
            ):
                return
            time.sleep(0.25)
        self._stop_child()
        raise TimeoutError(f"llama-server on port {port} did not become ready")

    def _model_path(self, filename: str) -> Path:
        if (
            not filename
            or Path(filename).name != filename
            or not filename.endswith(".gguf")
        ):
            raise ValueError("model must be a .gguf filename")
        path = (self.model_dir / filename).resolve()
        if path.parent != self.model_dir or not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _required_string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        return value

    @staticmethod
    def _bounded_int(
        payload: dict[str, object], key: str, minimum: int, maximum: int
    ) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{key} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value

    def _stop_child(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def monitor_owner(self) -> None:
        while True:
            time.sleep(1)
            with self.lock:
                dead = (
                    self.mode == "character"
                    and self.owner_pid is not None
                    and not _pid_alive(self.owner_pid)
                )
                if dead and self.owner_dead_since is None:
                    self.owner_dead_since = time.monotonic()
                elif not dead:
                    self.owner_dead_since = None
                stale = (
                    dead
                    and self.owner_dead_since is not None
                    and time.monotonic() - self.owner_dead_since >= 3
                    and time.monotonic() >= self.handoff_until
                )
                owner_pid = self.owner_pid
            if stale:
                try:
                    self.switch("base", owner_pid)
                except (OSError, RuntimeError, ValueError):
                    time.sleep(2)

    def hold_handoff(self, seconds: float = 30.0) -> dict[str, object]:
        with self.lock:
            if self.mode == "character":
                self.handoff_until = time.monotonic() + seconds
            return self.status()

    def stop_service(self) -> dict[str, object]:
        self.chat.cancel_all()
        self.terminal.close_all()
        with self.lock:
            if (
                self.mode == "character"
                and self.owner_pid is not None
                and _pid_alive(self.owner_pid)
            ):
                raise PermissionError("SilentTalk still owns character mode")
            self.loading = True
            self._stop_child()
            self.mode = "stopped"
            self.port = GENERAL_PORT
            self.owner_pid = None
            self.owner_dead_since = None
            self.handoff_until = 0.0
            self.loading = False
            return self.status()


class Handler(BaseHTTPRequestHandler):
    core: E4BCore

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json(HTTPStatus.OK, self.core.status())
        elif self.path == "/config":
            self._json(HTTPStatus.OK, self.core.config)
        elif self.path in ("/", "/index.html"):
            self._html(NATIVE_CHAT_HTML)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        allowed_origins = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        if (origin is not None and origin not in allowed_origins) or self.headers.get(
            "Sec-Fetch-Site"
        ) == "cross-site":
            self._json(
                HTTPStatus.FORBIDDEN, {"error": "cross-origin actions are not allowed"}
            )
            return
        try:
            payload = self._payload()
            if self.path == "/chat":
                self._chat(payload)
                return
            if self.path == "/terminal":
                self._terminal(payload)
                return
            if self.path == "/terminal/close":
                self.core.terminal.close(self._session_id(payload))
                self._json(HTTPStatus.OK, {"closed": True})
                return
            if self.path == "/chat/reset":
                session_id = self._session_id(payload)
                self.core.chat.reset(session_id)
                self.core.terminal.close(session_id)
                self._json(HTTPStatus.OK, {"reset": True})
                return
            if self.path == "/chat/cancel":
                session_id = self._session_id(payload)
                self.core.chat.cancel(session_id)
                self._json(HTTPStatus.OK, {"cancelled": True})
                return
            if self.path == "/chat/approve":
                session_id = self._session_id(payload)
                call_id = payload.get("call_id")
                approved = payload.get("approved")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("valid call_id required")
                if not isinstance(approved, bool):
                    raise TypeError("approved must be a boolean")
                accepted = self.core.chat.resolve_approval(
                    session_id, call_id, approved
                )
                self._json(HTTPStatus.OK, {"accepted": accepted})
                return
            if self.path == "/config":
                result = self.core.update_config(payload)
                self._json(HTTPStatus.OK, result)
                return
            if self.path == "/directories":
                result = self.core.list_directories(payload.get("path"))
                self._json(HTTPStatus.OK, result)
                return
            if self.path in {"/workspace/info", "/workspace/worktree"}:
                directory = payload.get("path")
                if not isinstance(directory, str) or not directory:
                    raise ValueError("workspace path required")
                result = (
                    self.core.workspace_info(directory)
                    if self.path == "/workspace/info"
                    else self.core.create_worktree(directory)
                )
                self._json(HTTPStatus.OK, result)
                return
            stop_control_server = False
            owner = payload.get("owner_pid")
            owner_pid = (
                owner
                if isinstance(owner, int) and not isinstance(owner, bool)
                else None
            )
            if self.path == "/character":
                if owner_pid is None or not _pid_alive(owner_pid):
                    raise ValueError("live owner_pid required")
                character = payload.get("character")
                if not isinstance(character, dict):
                    raise ValueError("character configuration required")
                result = self.core.switch("character", owner_pid, character)
            elif self.path == "/handoff":
                result = self.core.hold_handoff()
            elif self.path == "/base":
                result = self.core.switch("base", owner_pid)
            elif self.path == "/shutdown":
                result = self.core.stop_service()
                stop_control_server = True
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except PermissionError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc), "ready": False})
            return
        except (TypeError, ValueError, FileNotFoundError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "ready": False})
            return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary must return JSON
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc), "ready": False},
            )
            return
        self._json(HTTPStatus.OK, result)
        if stop_control_server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _terminal(self, payload: dict[str, object]) -> None:
        session_id = self._session_id(payload)
        command = payload.get("command")
        directory = payload.get("working_dir")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("non-empty terminal command required")
        if len(command) > 250_000:
            raise ValueError("terminal command is too long")
        if not isinstance(directory, str) or not directory:
            raise ValueError("open a workspace before using terminal")
        terminal = self.core.terminal.session(session_id, Path(directory))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for event, data in terminal.run(command):
                self._event(event, data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.core.terminal.close(session_id)
        except (OSError, ValueError) as exc:
            self._event("error", {"message": str(exc)})

    def _chat(self, payload: dict[str, object]) -> None:
        session_id = self._session_id(payload)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("non-empty text required")
        if len(text) > 250_000:
            raise ValueError("message is too long")
        context = attachment_context(payload.get("attachments", []))
        if context:
            text += "\n\n" + context
        if len(text) > 250_000:
            raise ValueError("message and attachments are too long")
        thinking_effort = payload.get(
            "thinking_effort", self.core.config["thinking_effort"]
        )
        if not isinstance(thinking_effort, str):
            raise TypeError("thinking_effort must be a string")
        agent = payload.get("agent", self.core.config["agent"])
        if not isinstance(agent, bool):
            raise TypeError("agent must be a boolean")
        plan = payload.get("plan", False)
        if not isinstance(plan, bool):
            raise TypeError("plan must be a boolean")
        working_dir_value = payload.get("working_dir")
        if working_dir_value is None or working_dir_value == "":
            if agent:
                raise ValueError("open a workspace folder before using Agent")
            working_dir = Path.home()
        elif isinstance(working_dir_value, str):
            working_dir = Path(working_dir_value).expanduser()
        else:
            raise TypeError("working_dir must be a string")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            with self.core.lock:
                status = self.core.status()
                if status["mode"] != "base" or status["ready"] is not True:
                    raise RuntimeError("E4B core is currently leased by SilentTalk")
                for event, data in self.core.chat.stream_reply(
                    session_id,
                    text.strip(),
                    thinking_effort=thinking_effort,
                    agent=agent,
                    working_dir=working_dir,
                    plan=plan,
                ):
                    self._event(event, data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.core.chat.cancel(session_id)
        except Exception as exc:  # noqa: BLE001 - stream errors must reach the browser
            self._event("error", {"message": str(exc)})

    def _event(self, event: str, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        body = f"event: {event}\ndata: {data}\n\n".encode()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise ConnectionAbortedError("chat client disconnected") from None

    def _payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 3_000_000:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise TypeError("JSON object required")
        return value

    @staticmethod
    def _session_id(payload: dict[str, object]) -> str:
        value = payload.get("session_id")
        if not isinstance(value, str) or not 8 <= len(value) <= 100:
            raise ValueError("valid session_id required")
        return value

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, value: str) -> None:
        body = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=CORE_ROOT)
    args = parser.parse_args()
    core = E4BCore(args.core_dir.resolve())
    Handler.core = core
    server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
    threading.Thread(target=core.monitor_owner, name="e4b-owner", daemon=True).start()
    threading.Thread(
        target=lambda: core.switch("base"), name="e4b-bootstrap", daemon=True
    ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        core._stop_child()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
