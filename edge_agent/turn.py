"""One task as the UI sees it: Pi's events of the turn in the UI's terms, the
host's own notices, and how the turn ended."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from edge_agent.chat_session import ChatSession

# Said by the host, not the model, when code was changed and no check passed
# afterwards: the model may still call the work done.
UNVERIFIED_NOTICE = "\n\n（主機提醒：這輪改了 {files}，之後沒有通過的檢查，結果未經驗證。）"
# The host's own check of an answer written from web_research: each cited
# number against its passages and the sources behind it (web_tools.mjs).
SOURCES_NOTICE = "\n\n（主機核對來源：{problems}。）"

Event = tuple[str, dict[str, object]]


@cache
def _converters() -> tuple[object, object] | None:
    try:
        # opencc-python-reimplemented: pure Python, installs as module `opencc`.
        from opencc import OpenCC
    except ImportError:
        return None
    return OpenCC("s2t"), OpenCC("s2twp")


def taiwan_traditional(text: str) -> str:
    """The last gate on what the Agent says (the owner: a safety net; the
    prompt and training fix the model): Simplified characters that slipped
    through, in Taiwan's forms. Text already in Traditional is left alone:
    s2twp also swaps phrases and would turn 測試通過 into 測試透過."""
    converters = _converters()
    if converters is None or not text:
        return text
    characters, taiwan = converters
    if str(characters.convert(text)) == text:  # type: ignore[attr-defined]
        return text
    return str(taiwan.convert(text))  # type: ignore[attr-defined]


# Model request failures by what the server or the transport said, as stable
# codes a client may log without the message (which can quote a request).
MODEL_FAILURES = (
    ("inference_busy", re.compile(r"inference_busy|\b423\b")),
    ("context_overflow", re.compile(r"context|too (?:long|many tokens)|exceed", re.IGNORECASE)),
    ("model_timeout", re.compile(r"time(?:d)? ?out", re.IGNORECASE)),
    ("model_unreachable", re.compile(r"ECONNREFUSED|ECONNRESET|connection (?:refused|reset)|fetch failed|socket",
                                     re.IGNORECASE)),
    ("model_http_error", re.compile(r"\b[45]\d\d\b")),
)


def model_failure(message: str) -> str:
    """The stable code for a failed model request."""
    return next((code for code, pattern in MODEL_FAILURES if pattern.search(message)), "model_error")


class Turn:
    """What one turn has done so far, read from the events it streams."""

    def __init__(self, session: ChatSession, cwd: Path) -> None:
        self.session = session
        self.cwd = cwd
        self.previews: dict[str, tuple[Path, str]] = {}
        self.last_stop = ""
        self.last_usage: dict[str, object] = {}
        self.request_budget: int | None = None
        self.last_assistant_budget: int | None = None
        self.failure = ""
        self.stop_reason = ""
        self.stop_code = ""
        self.unverified: list[str] = []
        self.source_problems: list[str] = []
        self.converted = 0
        self.steps = 0

    def notice(self, notice: object) -> list[Event] | None:
        """The UI events for one of the bridge's edge-* notices; None for any
        other message."""
        if isinstance(notice, str) and notice.startswith("edge-budget:"):
            budget = json.loads(notice.removeprefix("edge-budget:"))
            if type(budget.get("max_output_tokens")) is int:
                self.request_budget = budget["max_output_tokens"]
            return []
        if isinstance(notice, str) and notice.startswith("edge-unverified:"):
            self.unverified = json.loads(notice.removeprefix("edge-unverified:"))
            return []
        if isinstance(notice, str) and notice.startswith("edge-sources:"):
            self.source_problems = json.loads(notice.removeprefix("edge-sources:"))
            return []
        if isinstance(notice, str) and notice.startswith("edge-stop:"):
            stop = json.loads(notice.removeprefix("edge-stop:"))
            self.stop_code, self.stop_reason = str(stop["code"]), str(stop["message"])
            return [("status", {"message": self.stop_reason})]
        return None

    def update(self, update: dict) -> Event | None:
        if update.get("type") == "text_delta":
            text = str(update.get("delta", ""))
            shown = taiwan_traditional(text)
            self.converted += shown != text
            return "delta", {"text": shown}
        if update.get("type") == "thinking_start":
            return "status", {"message": "思考中…"}
        return None

    def message_end(self, message: object) -> None:
        if isinstance(message, dict):
            self.session.messages.append(message)
            if message.get("role") == "assistant":
                self.last_stop = str(message.get("stopReason", ""))
                self.last_assistant_budget = self.request_budget
                usage = message.get("usage")
                self.last_usage = usage if isinstance(usage, dict) else {}
                self.failure = str(message.get("errorMessage", ""))

    def tool_started(self, event: dict[str, object]) -> Event:
        name, call_id = (
            str(event.get("toolName")),
            str(event.get("toolCallId")),
        )
        arguments = event.get("args", {})
        if name in {"edit", "write"} and isinstance(arguments, dict):
            value = arguments.get("path")
            if isinstance(value, str):
                path = (self.cwd / value).resolve()
                if path.is_relative_to(self.cwd):
                    try:
                        before = (
                            path.read_text(encoding="utf-8")
                            if path.exists()
                            else ""
                        )
                        if len(before) <= 2_000_000:
                            self.previews[call_id] = (path, before)
                    except (OSError, UnicodeError):
                        pass
        self.steps += 1
        return (
            "tool",
            {
                "phase": "running",
                "tool": name,
                "call_id": call_id,
                "arguments": arguments,
            },
        )

    def tool_finished(self, event: dict[str, object]) -> Iterator[Event]:
        call_id = str(event.get("toolCallId"))
        yield (
            "tool",
            {
                "phase": "finished",
                "tool": event.get("toolName"),
                "call_id": call_id,
                "result": event.get("result", {}),
                "error": bool(event.get("isError")),
            },
        )
        changed = self.previews.pop(call_id, None)
        if changed and not event.get("isError"):
            path, before = changed
            try:
                after = path.read_text(encoding="utf-8")
                baseline = self.session.changes.get(
                    str(path), (before, before)
                )[0]
                self.session.changes[str(path)] = (baseline, after)
                yield (
                    "change",
                    {
                        "path": str(path),
                        "diff": "".join(
                            difflib.unified_diff(
                                baseline.splitlines(keepends=True),
                                after.splitlines(keepends=True),
                                fromfile=str(path),
                                tofile=str(path),
                            )
                        ),
                    },
                )
            except (OSError, UnicodeError):
                pass

    def ending(
        self, context_size: int, max_output_tokens: int, thinking_effort: str
    ) -> Iterator[Event]:
        """The turn's last events, once Pi has settled and been checkpointed."""
        if self.session.cancelled.is_set():
            yield "cancelled", {}
        elif self.stop_reason:
            yield "error", {"message": self.stop_reason, "reason": self.stop_code}
        elif self.last_stop == "aborted":
            yield "cancelled", {}
        elif self.last_stop == "length":
            output = self.last_usage.get("output")
            usage = (
                f"；本次輸出 {output} token（含思考）"
                if isinstance(output, int)
                else ""
            )
            limit = (
                f"；本次請求上限 {self.last_assistant_budget} token"
                if self.last_assistant_budget is not None
                else ""
            )
            yield (
                "error",
                {
                    "message": (
                        "模型生成被截斷"
                        f"（context {context_size} token，"
                        f"單次輸出額度 {max_output_tokens} token{limit}{usage}）；"
                        "已完成的修改仍然保留。可在模型模組調整單次輸出上限，"
                        "或繼續本輪工作。"
                    ),
                    "reason": "generation_truncated",
                    "context_size": context_size,
                    "max_output_tokens": max_output_tokens,
                    "request_max_output_tokens": self.last_assistant_budget,
                    "usage": self.last_usage,
                },
            )
        elif self.failure or self.last_stop == "error":
            yield (
                "error",
                {"message": self.failure or "模型請求失敗；已完成的工作仍然保留。",
                 "reason": model_failure(self.failure)},
            )
        elif self.last_stop != "stop":
            yield (
                "error",
                {"message": "Agent 未完成回覆；已完成的工作仍然保留。",
                 "reason": "incomplete_reply", "stop": self.last_stop[:40]},
            )
        else:
            if self.unverified:
                yield "delta", {"text": UNVERIFIED_NOTICE.format(
                    files="、".join(self.unverified[:5])
                    + (f" 等 {len(self.unverified)} 個檔案" if len(self.unverified) > 5 else "")
                )}
            if self.source_problems:
                yield "delta", {"text": SOURCES_NOTICE.format(
                    problems="；".join(self.source_problems[:6]))}
            yield (
                "done",
                {"agent_steps": self.steps, "thinking_effort": thinking_effort,
                 "unverified": self.unverified, "source_problems": self.source_problems,
                 # How often the Simplified->Traditional gate fired (for the logs).
                 "converted": self.converted},
            )
