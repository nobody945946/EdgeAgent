"""Who may generate now, on which slot, and how the one model window is shared.

With a character lease the server has two slots in one pool of cells
(kv-unified): the character's (slot 0, which llama-server fills first) and the
Agent's (slot 1). Both keep their own cache, so the character answers in
voice while the Agent works (owner, 2026-10-08: a voice chat cannot wait for
a task), and the Agent's window stays the whole pool (owner: 64K is the
floor). The gateway counts every request's cells before it runs (its prompt,
tokenized by the server, plus the most it may generate): when the Agent needs
cells the character's cache holds, that cache is erased (a few seconds to
rebuild); a character request that does not fit waits while an Agent task
runs, and erases the Agent's cache when none does. A new Agent request waits
while the character is speaking, and one still reading its prompt (nothing
generated yet) steps aside when the character starts: llama-server fills a
batch slot by slot, so a long prompt would hold the character's every token
back. The prompt read so far stays cached and the request resumes after the
character, up to VOICE_FIRST_SECONDS in all; after that both run side by side.

No conversation or task state lives here. Clients resend their own RAM history
after a handover; only the engine's disposable prompt cache is cleared.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

AGENT, CHARACTER = "agent", "character"
# Cells a request may take beyond its prompt and output (the cache's padding),
# and what an image or audio part takes (Gemma 4: 256 or 280 tokens an image).
MARGIN_CELLS = 256
MEDIA_CELLS = 1100
# How long a new Agent request lets the character finish speaking first; after
# that both run side by side (when the cells allow).
VOICE_FIRST_SECONDS = 30.0


class InferenceBusy(RuntimeError):
    """The model cannot take this borrower's request now; it retries."""


class InferenceAccess:
    """The two lanes and the cells each slot's cache holds (or a running
    request may grow to). `pool` is 0 without a character lease: one slot,
    the Agent's, and nothing to count."""

    def __init__(self) -> None:
        self.agent_key = uuid4().hex
        self._condition = threading.Condition()
        self._agent_reserved = False
        self._active = {AGENT: False, CHARACTER: False}
        self._paused = False
        self.pool = 0
        self.cells = {AGENT: 0, CHARACTER: 0}

    @property
    def agent_reserved(self) -> bool:
        with self._condition:
            return self._agent_reserved

    def reset(self, pool: int) -> None:
        """A model (re)loaded: empty caches, `pool` cells shared (0: no lease)."""
        with self._condition:
            self.pool = pool
            self.cells = {AGENT: 0, CHARACTER: 0}

    @contextmanager
    def agent_turn(self) -> Iterator[None]:
        """One Agent task at a time, from the request to the answer: across
        tool runs, approvals and compaction too. Holds off model reloads, and
        keeps the Agent's cache from being erased by a character request."""
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("Agent 工作正在進行或模型正在載入")
            self._agent_reserved = True
        try:
            yield
        finally:
            with self._condition:
                self._agent_reserved = False
                self._condition.notify_all()

    def _fits(self, lane: str, need: int) -> bool:
        other = CHARACTER if lane == AGENT else AGENT
        return not self.pool or self.cells[other] + need <= self.pool

    @contextmanager
    def generation(self, *, agent: bool, need: int = 0) -> Iterator[str | None]:
        """One request in its lane. `need`: the cells it may take. Yields the
        lane whose cache must be erased before it runs (the gateway erases it
        while that lane is held), or None."""
        lane = AGENT if agent else CHARACTER
        evict = None
        with self._condition:
            if self._paused:
                raise InferenceBusy("模型正在載入")
            if agent:
                if not self._agent_reserved:
                    raise PermissionError("Agent inference requires an active task")
                self._condition.wait_for(lambda: not self._active[AGENT])
                # Voice first: wait while the character speaks; after the
                # deadline run beside it, unless its cells are needed.
                deadline = time.monotonic() + VOICE_FIRST_SECONDS
                while self._active[CHARACTER] and (time.monotonic() < deadline or not self._fits(AGENT, need)):
                    self._condition.wait(timeout=0.5)
                if not self._fits(AGENT, need):
                    evict = CHARACTER
            else:
                if self._active[CHARACTER]:
                    raise InferenceBusy("角色正在生成")
                if not self._fits(CHARACTER, need):
                    if self._agent_reserved or self._active[AGENT]:
                        raise InferenceBusy("Agent 工作需要整個視窗，角色推論等待")
                    evict = AGENT
            self._active[lane] = True
            if evict:
                self._active[evict] = True  # nothing runs there while its cache is erased
            self.cells[lane] = need
        try:
            yield evict
        finally:
            with self._condition:
                self._active[lane] = False
                self._condition.notify_all()

    def character_done(self, deadline: float) -> None:
        """Wait until the character's lane is idle, or the deadline."""
        with self._condition:
            while self._active[CHARACTER] and time.monotonic() < deadline:
                self._condition.wait(timeout=0.5)

    def evicted(self, lane: str) -> None:
        """The gateway erased `lane`'s cache: its cells are free, its lane open."""
        with self._condition:
            self.cells[lane] = 0
            self._active[lane] = False
            self._condition.notify_all()

    def settle(self, lane: str, cells: int) -> None:
        """What the slot's cache holds after a request, as the server reported it."""
        with self._condition:
            self.cells[lane] = min(self.cells[lane], cells)

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("工作正在進行，不能重載模型")
            self._paused = True
        try:
            with self._condition:
                self._condition.wait_for(lambda: not any(self._active.values()))
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
        self.slots = {AGENT: 0, CHARACTER: 0}
        self._identity: dict[str, tuple[object, ...] | None] = {AGENT: None, CHARACTER: None}
        # The Agent's request still reading its prompt, which a character request preempts.
        self._reading: http.client.HTTPConnection | None = None
        self._preempted = False
        self._reading_lock = threading.Lock()

    def configure(self, owner_pid: int | None, adapters: int, agent_adapter: int | None, pool: int = 0) -> None:
        """`pool`: the cells the two slots share under a character lease; 0
        means one slot, the Agent's."""
        self.owner_pid = owner_pid
        self.adapter_count = adapters
        self.agent_adapter = agent_adapter
        self.slots = {CHARACTER: 0, AGENT: 1} if pool else {AGENT: 0, CHARACTER: 0}
        self._identity = {AGENT: None, CHARACTER: None}
        self.access.reset(pool)

    def _prepare(self, payload: dict[str, object], agent: bool) -> dict[str, object]:
        value = {**payload, "model": "local", "id_slot": self.slots[AGENT if agent else CHARACTER]}
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

    def _post(self, path: str, value: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            self.upstream_url + path, data=json.dumps({**value, "model": "local"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")

    def _need(self, path: str, value: dict[str, object]) -> int:
        """The cells a request may take: its prompt as the server tokenizes it
        (chat template and tools included), each image or audio part, the
        most it may generate, and a margin; the whole pool when the server
        cannot say. Without a pool (one slot) nothing is counted."""
        pool = self.access.pool
        if not pool:
            return 0
        try:
            if path == "/completion":
                prompt, media = value.get("prompt", ""), 0
            else:
                prompt = self._post("/apply-template", value)["prompt"]
                media = sum(1 for message in value.get("messages") or [] if isinstance(message, dict)
                            and isinstance(message.get("content"), list)
                            for part in message["content"]
                            if isinstance(part, dict) and part.get("type") not in (None, "text"))
            tokens = len(prompt) if isinstance(prompt, list) else len(self._post("/tokenize", {"content": prompt})["tokens"])
        except (urllib.error.HTTPError, OSError, ValueError, KeyError, TypeError):
            return pool
        limit = next((value[key] for key in ("max_tokens", "max_completion_tokens", "n_predict")
                      if type(value.get(key)) is int and value[key] > 0), pool)
        return min(pool, tokens + media * MEDIA_CELLS + limit + MARGIN_CELLS)

    def _preempt(self) -> None:
        """A character request started: an Agent request still reading its
        prompt steps aside (llama-server cancels it within a second, keeping
        what it read in the slot's cache)."""
        with self._reading_lock:
            if self._reading is not None and self._reading.sock is not None:
                self._preempted = True
                try:
                    self._reading.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _agent_response(self, path: str, value: dict[str, object]) -> http.client.HTTPResponse:
        """The Agent's upstream response, once its first token is out; while
        the prompt is read a character request may preempt it, and it is
        sent again after the character (the cached part is not read twice)."""
        upstream = urllib.parse.urlsplit(self.upstream_url)
        body = json.dumps(value).encode("utf-8")
        deadline = time.monotonic() + VOICE_FIRST_SECONDS
        while True:
            connection = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=1800)
            preemptable = bool(self.access.pool) and value.get("stream") is True and time.monotonic() < deadline
            with self._reading_lock:
                self._reading, self._preempted = (connection if preemptable else None), False
            try:
                connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
                return connection.getresponse()
            except (OSError, http.client.HTTPException):
                with self._reading_lock:
                    preempted = self._preempted
                connection.close()
                if not preempted:
                    raise
                self.access.character_done(deadline)
            finally:
                with self._reading_lock:
                    self._reading = None

    def _erase(self, lane: str) -> None:
        """Erase memory only. Never save/restore a KV cache to disk."""
        self._identity[lane] = None
        try:
            request = urllib.request.Request(
                self.upstream_url + f"/slots/{self.slots[lane]}?action=erase&model=local",
                data=b'{"model":"local"}', headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except (urllib.error.HTTPError, OSError):
            pass

    @staticmethod
    def _cells(line: bytes) -> int | None:
        """The slot's cache after a request, from the server's timings."""
        if b'"timings"' not in line:
            return None
        try:
            timings = json.loads(line.removeprefix(b"data: "))["timings"]
            return int(timings.get("cache_n", 0)) + int(timings["prompt_n"]) + int(timings["predicted_n"])
        except (ValueError, KeyError, TypeError):
            return None

    def forward(self, handler: BaseHTTPRequestHandler, payload: dict[str, object]) -> None:
        agent = handler.headers.get("Authorization") == f"Bearer {self.access.agent_key}"
        if not agent and (
            self.owner_pid is None
            or handler.headers.get("X-Character-Owner") != str(self.owner_pid)
        ):
            self._json(handler, HTTPStatus.FORBIDDEN, {"error": "live character lease required"})
            return
        lane = AGENT if agent else CHARACTER
        started = False
        try:
            value = self._prepare(payload, agent)
            with self.access.generation(agent=agent, need=self._need(handler.path, value)) as evict:
                if evict:
                    self._erase(evict)
                    self.access.evicted(evict)
                identity = tuple((r["id"], r["scale"]) for r in value["lora"])
                if identity != self._identity[lane]:
                    # Another adapter in this slot: its cache means nothing now.
                    self._erase(lane)
                    self._identity[lane] = identity
                if agent:
                    response = self._agent_response(handler.path, value)
                    if response.status >= 400:
                        raise urllib.error.HTTPError(handler.path, response.status, response.reason,
                                                     response.headers, response)
                else:
                    self._preempt()
                    response = urllib.request.urlopen(urllib.request.Request(
                        self.upstream_url + handler.path, data=json.dumps(value).encode("utf-8"),
                        headers={"Content-Type": "application/json"}, method="POST"), timeout=30)
                with response:
                    handler.send_response(response.status)
                    handler.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    # HTTP/1.0 close delimits the streaming body; no buffering.
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    handler.close_connection = True
                    started = True
                    cells = None
                    for line in response:
                        handler.wfile.write(line)
                        handler.wfile.flush()
                        cells = self._cells(line) or cells
                    if cells is not None:
                        self.access.settle(lane, cells)
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
