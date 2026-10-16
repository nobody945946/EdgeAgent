"""The control and UI endpoints on 127.0.0.1:18087: the routes, their request
checks, and the event streams for chat and the terminal."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from edge_agent import apis, undo
from edge_agent.attachments import decode_attachments
from edge_agent.core import EdgeAgent
from edge_agent.inference import InferenceBusy, InferenceGateway
from edge_agent.mcp import servers_for
from edge_agent.models import hub_catalog
from edge_agent.modes import MODES
from edge_agent.permissions import SCOPES, bridge_rules, profile_for, workspace_allowed
from edge_agent.processes import pid_alive
from edge_agent.records import prepare_record_folder, recording_status
from edge_agent.web import NATIVE_CHAT_HTML
from edge_agent.workspace import create_worktree, list_directories, workspace_info


class Refused(Exception):
    """A chat the Agent cannot take now. `code` is the stable name its error
    event carries (a client such as SilentTalk keeps codes, never the text)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Handler(BaseHTTPRequestHandler):
    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Navigation or closing a browser can disconnect any HTTP response.
            self.close_connection = True

    core: EdgeAgent

    def do_GET(self) -> None:
        if self.path == "/health":
            ready = self.core.status()["ready"] is True
            self._json(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                       {"status": "ok" if ready else "loading"})
        elif self.path == "/status":
            self._json(HTTPStatus.OK, self.core.status())
        elif self.path == "/status/events":
            self._status_events()
        elif self.path == "/config":
            self._json(HTTPStatus.OK, self.core.config)
        elif self.path == "/records":
            config = self.core.config
            self._json(
                HTTPStatus.OK,
                {
                    "record_sessions": config["record_sessions"],
                    "record_folder": config["record_folder"],
                    "status": recording_status(
                        bool(config["record_sessions"]), str(config["record_folder"])
                    ),
                },
            )
        elif self.path == "/models":
            self._json(HTTPStatus.OK, self.core.models_status())
        elif self.path in ("/", "/index.html"):
            self._html(NATIVE_CHAT_HTML)
        elif self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
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
            if self.path == "/records/prepare":
                path = prepare_record_folder(payload.get("folder"))
                self._json(HTTPStatus.OK, {"folder": str(path)})
                return
            if self.path == "/models/catalog":
                self._json(HTTPStatus.OK, hub_catalog(payload.get("repository")))
                return
            if self.path in {"/models/download", "/models/switch"}:
                result = self.core.start_model_job(
                    self.path.rsplit("/", 1)[-1], payload
                )
                self._json(HTTPStatus.ACCEPTED, result)
                return
            if self.path in InferenceGateway.PATHS:
                self.core.inference.forward(self, payload)
                return
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
                scope = payload.get("scope", "once")
                if scope not in {"once", "turn"}:
                    raise ValueError("invalid approval scope")
                accepted = self.core.chat.resolve_approval(
                    session_id, call_id, approved, str(scope)
                )
                self._json(HTTPStatus.OK, {"accepted": accepted})
                return
            if self.path == "/config":
                result = self.core.update_config(payload)
                self._json(HTTPStatus.OK, result)
                return
            if self.path == "/apis":
                # The registry with which APIs have a key (never the keys themselves).
                self._json(HTTPStatus.OK, {"apis": self.core.config["apis"],
                                           "has_key": apis.has_key(self.core.config["apis"])})
                return
            if self.path == "/apis/key":
                self._json(HTTPStatus.OK, {"has_key": self.core.set_api_key(
                    str(payload.get("id") or ""), str(payload.get("key") or ""))})
                return
            if self.path in ("/undo", "/undo/restore"):
                # The Agent's changes the host can put back (edge_agent.undo), per workspace.
                workspace = Path(str(payload.get("workspace") or ""))
                if not workspace.is_dir():
                    raise ValueError("workspace must be an existing folder")
                if self.path == "/undo":
                    self._json(HTTPStatus.OK, {"checkpoints": undo.history(workspace)})
                else:
                    self._json(HTTPStatus.OK, {"restored": undo.restore(workspace, str(payload.get("id") or ""))})
                return
            if self.path == "/directories":
                result = list_directories(payload.get("path"))
                self._json(HTTPStatus.OK, result)
                return
            if self.path in {"/workspace/info", "/workspace/worktree"}:
                directory = payload.get("path")
                if not isinstance(directory, str) or not directory:
                    raise ValueError("workspace path required")
                result = (
                    workspace_info(directory)
                    if self.path == "/workspace/info"
                    else create_worktree(directory, self.core.core_dir)
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
                if owner_pid is None or not pid_alive(owner_pid):
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

    def _status_events(self) -> None:
        """The status as it changes, for a client that keeps this open
        (SilentTalk's lease): the status at once, then one `status` event each
        time it differs from the last one sent, woken by the changes
        themselves (core.StatusChanges), never by a timer. No heartbeat: on
        localhost a client that dies closes its socket, and that ends this."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        changes = self.core.changes
        gone = threading.Event()

        def watch() -> None:
            # The client never sends anything: its socket ending is its leaving.
            try:
                while self.connection.recv(1024):
                    pass
            except OSError:
                pass
            gone.set()
            changes.wake()

        threading.Thread(target=watch, name="status-events-client", daemon=True).start()
        last = None
        try:
            while not gone.is_set():
                seen = changes.count
                status = self.core.status()
                if status != last:
                    self._event("status", status)
                    last = status
                changes.wait(seen, gone)
        except ConnectionAbortedError:
            pass

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
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if len(text) > 250_000:
            raise ValueError("message is too long")
        context, media = decode_attachments(
            payload.get("attachments", []), self.core.capabilities
        )
        if not text.strip() and not context and not media:
            raise ValueError("text or attachments required")
        text = text.strip() or "分析附件"
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
        mode = payload.get("mode", "work")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        # A client can narrow one task's scope (never widen it).
        scope = payload.get("scope", "change")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        client = payload.get("client", "ui")
        profile = profile_for(self.core.config["permissions"], client)
        mcp_servers = servers_for(client, self.core.config["mcp_servers"])
        working_dir_value = payload.get("working_dir")
        if working_dir_value is None or working_dir_value == "":
            if agent:
                raise ValueError("open a workspace folder before using Agent")
            working_dir = Path.home()
        elif isinstance(working_dir_value, str):
            working_dir = Path(working_dir_value).expanduser()
        else:
            raise TypeError("working_dir must be a string")
        if agent and not workspace_allowed(profile, working_dir):
            raise PermissionError("此資料夾不在權限設定允許的工作區內")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            with self.core.lock:
                status = self.core.status()
                if status["ready"] is not True or self.core.agent_slot is None:
                    raise Refused("agent_unavailable", "EdgeAgent is currently leased by SilentTalk")
                if self.core.chat_in_progress:
                    raise Refused("agent_busy", "Agent 工作正在進行；完成或停止後再派工")
                for item in media:
                    capability = (
                        "audio"
                        if str(item["mimeType"]).startswith("audio/")
                        else "vision"
                    )
                    if not self.core.capabilities[capability]:
                        raise Refused("attachment_unsupported", "目前模型不支援此附件，請更換模型")
                self.core.chat_in_progress = True
            self.core.changes.publish()
            try:
                with self.core.inference.access.agent_turn():
                    for event, data in self.core.chat.stream_reply(
                        session_id,
                        text.strip(),
                        thinking_effort=thinking_effort,
                        agent=agent,
                        working_dir=working_dir,
                        mode=mode,
                        media=media,
                        permissions=bridge_rules(profile),
                        scope=scope,
                        mcp_servers=mcp_servers,
                        record_folder=self.core.config["record_folder"]
                        if self.core.config["record_sessions"]
                        else None,
                    ):
                        self._event(event, data)
            finally:
                with self.core.lock:
                    self.core.chat_in_progress = False
                self.core.changes.publish()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.core.chat.cancel(session_id)
        except Exception as exc:  # noqa: BLE001 - stream errors must reach the browser
            reason = (
                exc.code
                if isinstance(exc, Refused)
                else "inference_busy"
                if isinstance(exc, InferenceBusy)
                else "agent_error"
            )
            self._event("error", {"message": str(exc), "reason": reason})

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
        if length < 0 or length > 28_000_000:
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
