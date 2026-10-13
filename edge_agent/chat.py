"""The Agent's conversations: RAM sessions, approvals and each task's turn,
carried out by Pi through the bridge process (bridge.py)."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from edge_agent import prompt as agent_prompt
from edge_agent.approvals import explain_approval, host_answer
from edge_agent.bridge import PiBridge
from edge_agent.chat_session import ChatSession
from edge_agent.modes import MODES, THINKING_PROFILES, mode_opening
from edge_agent.permissions import DEFAULT_PERMISSIONS, bridge_rules, scope_rules
from edge_agent.records import save_session
from edge_agent.runtime import (
    CORE_ROOT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_WORKING_CONTEXT_SIZE,
    GENERAL_CONTEXT_SIZE,
)
from edge_agent.turn import Turn

APPROVAL_TIMEOUT_SECONDS = 300
# Where a public task runs (scope "public": anyone in a chat may ask): an
# empty folder of EdgeAgent's own, never the owner's workspace, whose path
# Pi writes into the prompt; the task needs no folder (its files arrive with
# the prompt, the translator works in a temporary one).
PUBLIC_DIR = CORE_ROOT / ".runtime" / "public"


def transport_failure(exc: BaseException) -> str:
    """The stable code for a turn that failed between the host and Pi."""
    if isinstance(exc, TimeoutError):
        return "bridge_timeout"
    if isinstance(exc, OSError):
        return "bridge_io"
    if isinstance(exc, RuntimeError):
        return "bridge_error"
    return "invalid_request"


class NativeChat:
    """Adapt Pi's official RPC stream to the existing UI. Pi owns the agent loop."""

    def __init__(self, *, model_port: int, core_dir: Path = CORE_ROOT) -> None:
        self.bridge = PiBridge(core_dir.resolve())
        self.model_url = f"http://127.0.0.1:{model_port}"
        self.api_key = "local"
        self.context_size = GENERAL_CONTEXT_SIZE
        self.max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
        self.working_context_size = DEFAULT_WORKING_CONTEXT_SIZE
        self.model = ""
        self.slot_id: int | None = 0
        # List the enabled MCP servers in the system prompt, so a small model
        # knows what they are for (measured by the bench mcp-* tasks).
        self.mcp_overview = True
        # The APIs the Agent is told about and the keys its shell gets (edge_agent.apis.for_session).
        self.api_session: tuple[list[dict[str, str]], dict[str, str]] = ([], {})
        # The index of the Agent's adapter on the server (None: no adapter).
        self.lora_id: int | None = None
        self.sampling: dict[str, float] = {}  # the Agent adapter's temperature per mode (models.SAMPLING)
        self.hardware = ""
        self.capabilities = {"vision": False, "audio": False}
        self._sessions: dict[str, ChatSession] = {}
        self._sessions_lock = threading.RLock()

    def configure_context(
        self,
        context_size: int,
        model: str | None = None,
        capabilities: dict[str, bool] | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        working_context_size: int = DEFAULT_WORKING_CONTEXT_SIZE,
        hardware: str | None = None,
    ) -> None:
        self.context_size = context_size
        if hardware is not None:
            self.hardware = hardware  # the machine, for the Agent's runtime section (edge_agent.hardware)
        # One owner for the effective budget passed to Pi and displayed by UI.
        # Reserve at least half the window for instructions, media and history.
        self.max_output_tokens = min(max_output_tokens, context_size // 2)
        # A history-compaction target, not a reduction of the model's window.
        self.working_context_size = min(
            context_size, max(working_context_size, self.max_output_tokens * 2)
        )
        if model is not None:
            self.model = model
        if capabilities is not None:
            self.capabilities = dict(capabilities)

    def session(self, session_id: str) -> ChatSession:
        with self._sessions_lock:
            if session_id not in self._sessions and len(self._sessions) >= 32:
                victim = next(iter(self._sessions))
                if self._sessions[victim] is self.bridge.active:
                    self.bridge.close()
                self._sessions.pop(victim)
            return self._sessions.setdefault(session_id, ChatSession())

    def reset(self, session_id: str) -> None:
        self.cancel(session_id)
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
            if session is self.bridge.active:
                self.bridge.close()

    def cancel(self, session_id: str) -> None:
        with self._sessions_lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if not session.cancelled.is_set():
                session.cancelled_at = time.monotonic()
                session.cancelled.set()
            if session is self.bridge.active and self.bridge.alive():
                for call_id in list(session.approvals):
                    self.bridge.send(
                        {
                            "type": "extension_ui_response",
                            "id": call_id,
                            "value": "Deny",
                        }
                    )
                self.bridge.send({"type": "clear_queue"})
                self.bridge.send({"type": "abort"})
            session.approvals.clear()

    def cancel_all(self) -> None:
        with self._sessions_lock:
            ids = list(self._sessions)
        for session_id in ids:
            self.cancel(session_id)

    def close(self) -> None:
        self.cancel_all()
        self.bridge.close()

    def resolve_approval(
        self, session_id: str, call_id: str, approved: bool, scope: str = "once"
    ) -> bool:
        session = self.session(session_id)
        request = session.approvals.pop(call_id, None)
        if request is None or session is not self.bridge.active or session.cancelled.is_set():
            return False
        if json.loads(str(request.get("title", "{}"))).get("explicit"):
            scope = "once"
        choice = (
            "Allow this turn"
            if approved and scope == "turn"
            else ("Allow once" if approved else "Deny")
        )
        self.bridge.send({"type": "extension_ui_response", "id": call_id, "value": choice})
        return True

    def _start(
        self,
        session: ChatSession,
        cwd: Path,
        effort: str,
        agent: bool,
        read_only: bool,
        public: bool,
        permissions: dict[str, str],
        mcp_servers: dict[str, dict[str, object]],
    ) -> None:
        key = (
            id(session),
            str(cwd),
            self.model,
            self.slot_id,
            self.mcp_overview,
            self.lora_id,
            self.context_size,
            self.max_output_tokens,
            self.working_context_size,
            self.capabilities.get("vision", False),
            self.capabilities.get("audio", False),
            effort,
            agent,
            read_only,
            public,
            tuple(sorted(permissions.items())),
            json.dumps(mcp_servers, sort_keys=True),
            self.hardware,
            json.dumps(self.api_session, sort_keys=True),
        )
        if self.bridge.key == key and self.bridge.alive():
            return
        self.bridge.checkpoint()
        self.bridge.close()
        self.bridge.spawn(cwd, session, key)
        self.bridge.send(
            {
                "cwd": str(cwd),
                "model_url": self.model_url,
                "api_key": self.api_key,
                "model": "local",
                "model_name": self.model,
                "hardware": self.hardware,
                "apis": self.api_session[0],
                # Keys go to the shell's environment and are masked in tool output, never to the model.
                "api_env": self.api_session[1],
                "slot_id": self.slot_id,
                "mcp_overview": self.mcp_overview,
                "lora_id": self.lora_id,
                "sampling": self.sampling,
                "capabilities": self.capabilities,
                "context_size": self.context_size,
                "max_output_tokens": self.max_output_tokens,
                "working_context_size": self.working_context_size,
                "thinking_effort": effort,
                "agent": agent,
                "read_only": read_only,
                "public": public,
                "permissions": permissions,
                "mcp_servers": mcp_servers,
                "system_prompt": agent_prompt.system_prompt(
                    "public" if public else "read" if read_only else "change"
                ) if agent else agent_prompt.chat_prompt(),
                # translate_machine runs edge_agent.lingua with this same Python
                "python": sys.executable,
                "entries": session.entries,
                "pending_messages": session.messages,
            }
        )
        if self.bridge.receive(30).get("type") != "ready":
            raise RuntimeError("Pi failed to initialize")

    def stream_reply(
        self,
        session_id: str,
        user_text: str,
        *,
        thinking_effort: str = "off",
        agent: bool = False,
        working_dir: Path | None = None,
        mode: str = "work",
        media: list[dict[str, str]] | None = None,
        permissions: dict[str, str] | None = None,
        mcp_servers: dict[str, dict[str, object]] | None = None,
        record_folder: str | None = None,
        scope: str = "change",
    ) -> Iterator[tuple[str, dict[str, object]]]:
        """`mode` (MODES): the first message of a stretch in interview or
        discussion opens its skill; the messages that follow go as they are.
        The interview has read-only tools so nothing is built before a spec.
        `scope` (permissions.SCOPES): "read", a task the owner asked only to
        read: inspection tools, and in the shell only provable reads;
        "public", a lookup anyone may ask for: the web tools alone. Either
        way everything else is refused without asking, whatever the model
        tries."""
        if mode not in MODES:
            raise ValueError("invalid mode")
        if thinking_effort not in THINKING_PROFILES:
            raise ValueError("invalid thinking_effort")
        if user_text.strip().split(maxsplit=1)[:1] == ["/edge-snapshot"]:
            raise ValueError("reserved agent control command")
        if scope == "public":
            PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
            cwd = PUBLIC_DIR.resolve()
        else:
            cwd = (working_dir or Path.home()).resolve()
        if not cwd.is_dir():
            raise ValueError("workspace directory does not exist")
        session = self.session(session_id)
        if session.public is None:
            session.public = scope == "public"
        elif session.public != (scope == "public"):
            raise ValueError("this conversation's scope cannot change between public and the owner's work")
        with session.lock:
            session.cancelled.clear()
            session.cancelled_at = None
            turn = Turn(session, cwd)
            settled = False
            try:
                messages = [
                    *session.messages,
                    *(
                        entry.get("message", {})
                        for entry in session.entries
                        if entry.get("type") == "message"
                    ),
                    *(
                        {"content": entry.get("content", [])}
                        for entry in session.entries
                        if entry.get("type") == "custom_message"
                    ),
                ]
                blocks = [
                    *(media or []),
                    *(
                        block
                        for message in messages
                        if isinstance(message.get("content"), list)
                        for block in message["content"]
                    ),
                ]
                for block in blocks:
                    if block.get("type") != "image":
                        continue
                    capability = (
                        "audio"
                        if str(block.get("mimeType", "")).startswith("audio/")
                        else "vision"
                    )
                    if not self.capabilities.get(capability):
                        raise ValueError(
                            "此對話包含目前模型不支援的附件；請換回支援的模型或開啟新對話。"
                        )
                rules = scope_rules(permissions or bridge_rules(DEFAULT_PERMISSIONS["ui"]), scope)
                self._start(session, cwd, thinking_effort, agent,
                            mode == "interview" or scope != "change", scope == "public",
                            rules, mcp_servers or {})
                prompt_id = uuid4().hex
                opens = mode != "work" and session.opened_mode != mode
                self.bridge.send(
                    {
                        "type": "prompt",
                        "message": mode_opening(mode, user_text) if opens else user_text,
                        "id": prompt_id,
                        "images": media or [],
                    }
                )
                session.opened_mode = mode
                while True:
                    try:
                        event = self.bridge.receive(timeout=1)
                    except TimeoutError:
                        if (
                            session.cancelled_at is not None
                            and time.monotonic() - session.cancelled_at > 10
                        ):
                            yield "cancelled", {}
                            return
                        continue
                    kind = event.get("type")
                    if (
                        kind == "extension_ui_request"
                        and event.get("method") == "notify"
                    ):
                        shown = turn.notice(event.get("message", ""))
                        if shown is not None:
                            yield from shown
                            continue
                    if kind == "response" and event.get("id") == prompt_id:
                        if not event.get("success"):
                            raise RuntimeError(
                                str(event.get("error", "Pi rejected this task"))
                            )
                        if event.get("data", {}).get("disposition") == "handled":
                            turn.last_stop = "stop"
                            settled = True
                            break
                    if self.bridge.snapshot(event):
                        continue
                    if kind == "protocol_error":
                        raise RuntimeError("Pi returned an invalid protocol record")
                    if kind == "message_update":
                        shown = turn.update(event.get("assistantMessageEvent", {}))
                        if shown is not None:
                            yield shown
                    elif kind == "message_end":
                        turn.message_end(event.get("message", {}))
                    elif (
                        kind == "extension_ui_request"
                        and event.get("method") == "select"
                    ):
                        details = json.loads(str(event.get("title", "{}")))
                        call_id = str(event["id"])
                        session.approvals[call_id] = event
                        answer = host_answer(details, rules, cwd)
                        if answer is not None:
                            self.resolve_approval(session_id, call_id, answer, "once")
                            continue
                        yield "approval", {"call_id": call_id, **details,
                                           **explain_approval(details, cwd)}
                    elif kind == "tool_execution_start":
                        yield turn.tool_started(event)
                    elif kind == "tool_execution_end":
                        yield from turn.tool_finished(event)
                    elif kind == "compaction_end" and event.get("result"):
                        yield (
                            "compacted",
                            {
                                "message": "Pi 已整理上下文，保留工作摘要與近期工具記錄。"
                            },
                        )
                    elif kind == "auto_retry_start":
                        yield "status", {"message": "Pi 正在重試模型請求…"}
                    elif kind == "agent_settled":
                        settled = True
                        break
                self.bridge.checkpoint()
                # Kept on the user's records drive when that is on (records.py).
                save_session(record_folder, session_id, session.entries)
                yield from turn.ending(
                    self.context_size, self.max_output_tokens, thinking_effort
                )
            except (OSError, ValueError, TypeError, RuntimeError, TimeoutError) as exc:
                # Do not leave an unobserved agent modifying files after a failed
                # transport/preflight. Completed messages and diffs stay in RAM.
                was_cancelled = session.cancelled.is_set()
                self.cancel(session_id)
                self.bridge.close()
                yield (
                    ("cancelled", {})
                    if was_cancelled
                    else ("error", {"message": str(exc), "reason": transport_failure(exc)})
                )
            finally:
                if not settled and session is self.bridge.active:
                    self.cancel(session_id)
                    self.bridge.close()
                session.approvals.clear()
