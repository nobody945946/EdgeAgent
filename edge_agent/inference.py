"""Task priority and HTTP transport for one exclusively borrowed model slot.

No conversation or task state lives here. Clients resend their own RAM history
after a handover; only the engine's disposable prompt cache is cleared.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from uuid import uuid4


class InferenceBusy(RuntimeError):
    """The owner is working; an idle borrower must wait."""


class InferenceAccess:
    def __init__(self) -> None:
        self.agent_key = uuid4().hex
        self._condition = threading.Condition()
        self._agent_reserved = False
        self._generation_active = False
        self._paused = False

    @property
    def agent_reserved(self) -> bool:
        with self._condition:
            return self._agent_reserved

    @contextmanager
    def agent_turn(self) -> Iterator[None]:
        """Reserve before waiting: no new borrower can overtake the Agent.

        Keep this reservation across tool execution, approvals and compaction,
        not merely while the model happens to be producing tokens.
        """
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("Agent 工作正在進行或模型正在載入")
            self._agent_reserved = True
        try:
            with self._condition:
                self._condition.wait_for(lambda: not self._generation_active)
            yield
        finally:
            with self._condition:
                self._agent_reserved = False
                self._condition.notify_all()

    @contextmanager
    def generation(self, *, agent: bool) -> Iterator[None]:
        with self._condition:
            if self._paused:
                raise InferenceBusy("模型正在載入")
            if agent:
                if not self._agent_reserved:
                    raise PermissionError("Agent inference requires an active task")
                self._condition.wait_for(lambda: not self._generation_active)
            elif self._agent_reserved or self._generation_active:
                raise InferenceBusy("Agent 正在工作，角色推論等待閒置")
            self._generation_active = True
        try:
            yield
        finally:
            with self._condition:
                self._generation_active = False
                self._condition.notify_all()

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("工作正在進行，不能重載模型")
            self._paused = True
        try:
            with self._condition:
                self._condition.wait_for(lambda: not self._generation_active)
            yield
        finally:
            with self._condition:
                self._paused = False
                self._condition.notify_all()


class InferenceGateway:
    """The only inference path used by the Agent and its HTTP borrowers."""

    PATHS = frozenset({"/v1/chat/completions", "/completion"})

    def __init__(self, upstream_port: int) -> None:
        self.access = InferenceAccess()
        self.upstream_url = f"http://127.0.0.1:{upstream_port}"
        self.owner_pid: int | None = None
        self.adapter_count = 0
        self.agent_adapter: int | None = None
        self._identity: tuple[object, ...] | None = None

    def configure(self, owner_pid: int | None, adapters: int, agent_adapter: int | None) -> None:
        self.owner_pid = owner_pid
        self.adapter_count = adapters
        self.agent_adapter = agent_adapter
        self._identity = None

    def _prepare(self, payload: dict[str, object], agent: bool) -> dict[str, object]:
        value = {**payload, "model": "local", "id_slot": 0}
        if agent:
            scales = {self.agent_adapter: 1.0} if self.agent_adapter is not None else {}
        else:
            requested = payload.get("lora", [])
            if not isinstance(requested, list):
                raise ValueError("lora must be a list")
            scales = {}
            for item in requested:
                if not isinstance(item, dict) or item.get("id") != 0:
                    raise ValueError("a character may only use its own adapter")
                scale = item.get("scale")
                if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale not in (0, 1):
                    raise ValueError("invalid character adapter scale")
                scales[0] = float(scale)
        value["lora"] = [{"id": index, "scale": scales.get(index, 0.0)}
                         for index in range(self.adapter_count)]
        return value

    def forward(self, handler: BaseHTTPRequestHandler, payload: dict[str, object]) -> None:
        agent = handler.headers.get("Authorization") == f"Bearer {self.access.agent_key}"
        if not agent:
            if self.owner_pid is None or handler.headers.get("X-Character-Owner") != str(self.owner_pid):
                self._json(handler, HTTPStatus.FORBIDDEN, {"error": "live character lease required"})
                return
        started = False
        try:
            with self.access.generation(agent=agent):
                value = self._prepare(payload, agent)
                identity = (agent, tuple((r["id"], r["scale"]) for r in value["lora"]))
                if identity != self._identity:
                    # Erase memory only. Never save/restore a KV cache to disk.
                    self._identity = None
                    request = urllib.request.Request(
                        self.upstream_url + "/slots/0?action=erase&model=local",
                        data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=15) as response:
                        response.read()
                    self._identity = identity
                request = urllib.request.Request(
                    self.upstream_url + handler.path,
                    data=json.dumps(value).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(request, timeout=1800 if agent else 30) as response:
                    handler.send_response(response.status)
                    handler.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    # HTTP/1.0 close delimits the streaming body; no buffering.
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    handler.close_connection = True
                    started = True
                    for line in response:
                        handler.wfile.write(line)
                        handler.wfile.flush()
        except InferenceBusy:
            self._json(handler, HTTPStatus.LOCKED, {"error": {"code": "inference_busy"}})
        except PermissionError:
            self._json(handler, HTTPStatus.FORBIDDEN, {"error": "active Agent task required"})
        except ValueError:
            self._json(handler, HTTPStatus.BAD_REQUEST, {"error": "invalid inference request"})
        except urllib.error.HTTPError as exc:
            if not started:
                # Preserve upstream context-overflow semantics for Pi compact.
                handler.send_response(exc.code)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(exc.read())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            handler.close_connection = True
        except OSError:
            if not started:
                self._json(handler, HTTPStatus.BAD_GATEWAY, {"error": "model inference unavailable"})
            else:
                handler.close_connection = True

    @staticmethod
    def _json(handler: BaseHTTPRequestHandler, status: HTTPStatus, value: dict[str, object]) -> None:
        data = json.dumps(value).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
