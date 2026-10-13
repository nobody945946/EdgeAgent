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
generated yet) steps aside when the character starts (llama-server fills a
batch slot by slot, so a long prompt would hold the character's every token
back; it notices the cut only after a quiet second, which the character
waits). The prompt read so far stays cached and the request resumes after the
character, up to VOICE_FIRST_SECONDS in all; after that both run side by side.

No conversation or task state lives here. Clients resend their own RAM history
after a handover; only the engine's disposable prompt cache is cleared.
"""

from __future__ import annotations

import http.client
import itertools
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
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
# After cutting an Agent request off, the quiet time llama-server needs to see
# it: it looks at a client's connection only after a second with no result
# for any slot (each result restarts that second), and then drops the request
# at its next step. The character's request goes after that.
PREEMPT_SETTLE_SECONDS = 1.2


class InferenceBusy(RuntimeError):
    """The model cannot take this borrower's request now; it retries."""


class InferenceAccess:
    """The two lanes and the cells each slot's cache holds (or a running
    request may grow to). `pool` is 0 without a character lease: one slot,
    the Agent's, and nothing to count.

    Every change wakes the waiters here and calls `on_change` (the status
    stream, core.StatusChanges): whoever waits is told, nobody asks again on a
    timer."""

    def __init__(self, on_change: Callable[[], None] = lambda: None) -> None:
        self.agent_key = uuid4().hex
        self._condition = threading.Condition()
        self._on_change = on_change
        self._agent_reserved = False
        self._active = {AGENT: False, CHARACTER: False}
        self._paused = False
        self.pool = 0
        self.cells = {AGENT: 0, CHARACTER: 0}
        # Character requests turned away so far, and the cells the last one
        # needed: whether one would be admitted now is asked for that size
        # (character_admission), so the status says when to send it again.
        self._refused = 0
        self._refused_need = 0

    def _changed(self) -> None:
        """With the condition held: the lanes changed."""
        self._condition.notify_all()
        self._on_change()

    @property
    def agent_reserved(self) -> bool:
        with self._condition:
            return self._agent_reserved

    def reset(self, pool: int) -> None:
        """A model (re)loaded: empty caches, `pool` cells shared (0: no lease)."""
        with self._condition:
            self.pool = pool
            self.cells = {AGENT: 0, CHARACTER: 0}
            self._changed()

    @contextmanager
    def agent_turn(self) -> Iterator[None]:
        """One Agent task at a time, from the request to the answer: across
        tool runs, approvals and compaction too. Holds off model reloads, and
        keeps the Agent's cache from being erased by a character request."""
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("Agent 工作正在進行或模型正在載入")
            self._agent_reserved = True
            self._changed()
        try:
            yield
        finally:
            with self._condition:
                self._agent_reserved = False
                self._changed()

    def _fits(self, lane: str, need: int) -> bool:
        other = CHARACTER if lane == AGENT else AGENT
        return not self.pool or self.cells[other] + need <= self.pool

    def _character_refusal(self, need: int) -> str | None:
        """Why a character request of `need` cells cannot run now, or None."""
        if self._paused:
            return "模型正在載入"
        if self._active[CHARACTER]:
            return "角色正在生成"
        if not self._fits(CHARACTER, need) and (self._agent_reserved or self._active[AGENT]):
            return "Agent 工作需要整個視窗，角色推論等待"
        return None

    def character_admission(self) -> tuple[bool, int]:
        """Whether a character request the size of the last one turned away
        would be admitted now, and how many were turned away so far (the
        status stream sends both: a client turned away waits for a status
        after its refusal that admits it)."""
        with self._condition:
            return self._character_refusal(self._refused_need) is None, self._refused

    @contextmanager
    def generation(self, *, agent: bool, need: int = 0) -> Iterator[str | None]:
        """One request in its lane. `need`: the cells it may take. Yields the
        lane whose cache must be erased before it runs (the gateway erases it
        while that lane is held), or None."""
        lane = AGENT if agent else CHARACTER
        evict = None
        with self._condition:
            if agent:
                if self._paused:
                    raise InferenceBusy("模型正在載入")
                if not self._agent_reserved:
                    raise PermissionError("Agent inference requires an active task")
                self._condition.wait_for(lambda: not self._active[AGENT])
                # Voice first: wait while the character speaks, until it ends
                # or the deadline itself; after that run beside it, unless its
                # cells are needed (then until it ends or they fit).
                self._condition.wait_for(lambda: not self._active[CHARACTER], VOICE_FIRST_SECONDS)
                self._condition.wait_for(lambda: not self._active[CHARACTER] or self._fits(AGENT, need))
                if not self._fits(AGENT, need):
                    evict = CHARACTER
            else:
                refusal = self._character_refusal(need)
                if refusal is not None:
                    self._refused += 1
                    self._refused_need = need
                    self._changed()
                    raise InferenceBusy(refusal)
                if not self._fits(CHARACTER, need):
                    evict = AGENT
            self._active[lane] = True
            if evict:
                self._active[evict] = True  # nothing runs there while its cache is erased
            self.cells[lane] = need
            self._changed()
        try:
            yield evict
        finally:
            with self._condition:
                self._active[lane] = False
                self._changed()

    def character_done(self, deadline: float) -> None:
        """Wait until the character's lane is idle, or the deadline."""
        with self._condition:
            self._condition.wait_for(
                lambda: not self._active[CHARACTER], max(0.0, deadline - time.monotonic())
            )

    def evicted(self, lane: str) -> None:
        """The gateway erased `lane`'s cache: its cells are free, its lane open."""
        with self._condition:
            self.cells[lane] = 0
            self._active[lane] = False
            self._changed()

    def settle(self, lane: str, cells: int) -> None:
        """What the slot's cache holds after a request, as the server reported it."""
        with self._condition:
            self.cells[lane] = min(self.cells[lane], cells)
            self._changed()

    @contextmanager
    def maintenance(self) -> Iterator[None]:
        with self._condition:
            if self._agent_reserved or self._paused:
                raise InferenceBusy("工作正在進行，不能重載模型")
            self._paused = True
            self._changed()
        try:
            with self._condition:
                self._condition.wait_for(lambda: not any(self._active.values()))
            yield
        finally:
            with self._condition:
                self._paused = False
                self._changed()


class InferenceGateway:
    """The only inference path used by the Agent and its HTTP borrowers."""

    PATHS = frozenset({"/v1/chat/completions", "/completion"})

    def __init__(self, upstream_port: int, on_change: Callable[[], None] = lambda: None) -> None:
        self.access = InferenceAccess(on_change)
        self.upstream_url = f"http://127.0.0.1:{upstream_port}"
        self.owner_pid: int | None = None
        self.adapter_count = 0
        self.agent_adapter: int | None = None
        self.slots = {AGENT: 0, CHARACTER: 0}
        self._identity: dict[str, tuple[object, ...] | None] = {AGENT: None, CHARACTER: None}
        # The socket of the Agent's request still reading its prompt, which a character request preempts.
        self._reading: socket.socket | None = None
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

    @staticmethod
    def _bound(path: str, value: dict[str, object]) -> tuple[int, int]:
        """(an upper bound on the prompt's tokens, its image and audio parts),
        without asking the server: a token is at least one byte, so the bytes
        of everything the template renders (messages, tools) plus a few tokens
        a message for its markers never fall short."""
        if path == "/completion":
            prompt = value.get("prompt", "")
            return (len(prompt) if isinstance(prompt, list) else len(str(prompt).encode("utf-8")) + 16), 0
        media, rendered = 0, []
        messages = [m for m in value.get("messages") or [] if isinstance(m, dict)]
        for message in messages:
            content = message.get("content")
            for part in content if isinstance(content, list) else [content]:
                if isinstance(part, dict) and part.get("type") not in (None, "text"):
                    media += 1
                else:
                    rendered.append(part)
            rendered.append({k: v for k, v in message.items() if k != "content"})
        rendered += [value.get("tools"), value.get("chat_template_kwargs")]
        size = len(json.dumps(rendered, ensure_ascii=False).encode("utf-8"))
        return size + 16 * len(messages) + 1024, media

    def _need(self, path: str, value: dict[str, object], agent: bool) -> int:
        """The cells a request may take: its prompt, each image or audio part,
        the most it may generate, and a margin. The byte bound is used while
        it fits beside the other slot's cache; nearer a full pool the server
        tokenizes the prompt (chat template and tools included), and when it
        cannot say, the whole pool. Without a pool (one slot) nothing is
        counted."""
        pool = self.access.pool
        if not pool:
            return 0
        limit = next((value[key] for key in ("max_tokens", "max_completion_tokens", "n_predict")
                      if type(value.get(key)) is int and value[key] > 0), pool)
        bound, media = self._bound(path, value)
        need = min(pool, bound + media * MEDIA_CELLS + limit + MARGIN_CELLS)
        if need + self.access.cells[CHARACTER if agent else AGENT] <= pool:
            return need
        try:
            prompt = value.get("prompt", "") if path == "/completion" else self._post("/apply-template", value)["prompt"]
            tokens = len(prompt) if isinstance(prompt, list) else len(self._post("/tokenize", {"content": prompt})["tokens"])
        except (urllib.error.HTTPError, OSError, ValueError, KeyError, TypeError):
            return pool
        return min(pool, tokens + media * MEDIA_CELLS + limit + MARGIN_CELLS)


    def _preempt(self) -> bool:
        """A character request started: an Agent request still reading its
        prompt steps aside (llama-server keeps what it read in the slot's
        cache). True if one did."""
        with self._reading_lock:
            if self._reading is None:
                return False
            self._preempted = True
            try:
                self._reading.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return True

    @staticmethod
    def _generated(line: bytes) -> bool:
        """A streamed line that carries generated output, the end or an error
        (llama-server sends its headers, an empty first chunk and pings
        before the prompt is read)."""
        if not line.startswith(b"data: "):
            return False
        try:
            chunk = json.loads(line[6:])
        except ValueError:
            return True  # [DONE] or anything unexpected: stop holding back
        if not isinstance(chunk, dict) or "error" in chunk or "timings" in chunk:
            return True
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason") or any(delta.get(key) for key in ("content", "reasoning_content", "tool_calls")):
                return True
        return False

    def _agent_response(self, path: str, value: dict[str, object]) -> tuple[http.client.HTTPResponse, list[bytes]]:
        """The Agent's upstream response and the lines read until its first
        generated token. Until then a character request may preempt it
        (nothing has reached the client): it is sent again after the
        character, and the prompt already read stays cached."""
        upstream = urllib.parse.urlsplit(self.upstream_url)
        body = json.dumps(value).encode("utf-8")
        deadline = time.monotonic() + VOICE_FIRST_SECONDS
        while True:
            connection = http.client.HTTPConnection(upstream.hostname, upstream.port, timeout=1800)
            preemptable = bool(self.access.pool) and value.get("stream") is True and time.monotonic() < deadline
            try:
                connection.connect()
                with self._reading_lock:
                    # The socket itself: http.client drops the connection's reference once a response owns it.
                    self._reading, self._preempted = (connection.sock if preemptable else None), False
                connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                held: list[bytes] = []
                if response.status < 400 and preemptable:
                    for line in response:
                        held.append(line)
                        if self._generated(line):
                            break
                    else:
                        with self._reading_lock:
                            preempted = self._preempted
                        if preempted:
                            raise ConnectionAbortedError("preempted")
                return response, held
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
            with self.access.generation(agent=agent, need=self._need(handler.path, value, agent)) as evict:
                if evict:
                    self._erase(evict)
                    self.access.evicted(evict)
                identity = tuple((r["id"], r["scale"]) for r in value["lora"])
                if identity != self._identity[lane]:
                    # Another adapter in this slot: its cache means nothing now.
                    self._erase(lane)
                    self._identity[lane] = identity
                held: list[bytes] = []
                if agent:
                    response, held = self._agent_response(handler.path, value)
                    if response.status >= 400:
                        raise urllib.error.HTTPError(handler.path, response.status, response.reason,
                                                     response.headers, response)
                else:
                    if self._preempt():
                        time.sleep(PREEMPT_SETTLE_SECONDS)
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
                    for line in itertools.chain(held, response):
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
