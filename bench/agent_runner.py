"""Run one bench task through EdgeAgent's own NativeChat. Sandbox side only.

Approvals are answered the way a user pressing "Allow this turn" would. The
result (metrics plus the bench trajectory) is the last stdout line as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from edge_agent.chat import NativeChat

SESSION = "bench"


def summarize(messages: list[dict]) -> dict:
    tools: dict[str, int] = {}
    tool_errors: dict[str, int] = {}
    usage = {"input": 0, "output": 0, "cacheRead": 0}
    turns = 0
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            turns += 1
            for key in usage:
                value = (message.get("usage") or {}).get(key)
                if isinstance(value, int):
                    usage[key] += value
            for item in message.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    name = str(item.get("name"))
                    tools[name] = tools.get(name, 0) + 1
        elif role == "toolResult" and message.get("isError"):
            name = str(message.get("toolName"))
            tool_errors[name] = tool_errors.get(name, 0) + 1
    return {"turns": turns, "tools": tools, "tool_errors": tool_errors, "usage": usage}


def main() -> None:
    spec = json.loads(os.environ["BENCH_SPEC"])
    if spec.get("search_tools"):
        # rg and fd for Pi's grep/find tools (inherited by the Node process).
        os.environ["PATH"] = "/opt/searchtools/bin" + os.pathsep + os.environ["PATH"]
    relay = subprocess.Popen(
        [sys.executable, "/opt/bench/relay.py", "--listen", "tcp:127.0.0.1:18089",
         "--connect", "unix:/sock/model.sock"],
        stdout=subprocess.PIPE,
    )
    assert relay.stdout is not None
    relay.stdout.readline()
    chat = NativeChat(model_port=18089, core_dir=Path("/opt/edgeagent"))
    chat.configure_context(
        spec["context_size"],
        model=spec["model"],
        capabilities={"vision": spec.get("vision", False), "audio": spec.get("audio", False)},
        max_output_tokens=spec["max_output_tokens"],
        working_context_size=spec["working_context_size"],
    )
    timed_out = threading.Event()

    def expire() -> None:
        timed_out.set()
        chat.cancel(SESSION)

    timer = threading.Timer(spec["timeout_s"], expire)
    timer.start()
    started = time.monotonic()
    outcome, reason, notice, approvals = "none", "", "", 0
    try:
        for kind, payload in chat.stream_reply(
            SESSION, spec["prompt"], thinking_effort=spec["effort"], agent=True,
            working_dir=Path("/work"), permissions=spec.get("permissions"),
        ):
            if kind == "approval":
                approvals += 1
                chat.resolve_approval(SESSION, str(payload["call_id"]), True, "turn")
            elif kind in ("done", "error", "cancelled"):
                outcome = kind
                reason = str(payload.get("reason", ""))
                notice = str(payload.get("message", ""))[:300]
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started
    # NativeChat checkpoints finished messages into Pi session entries.
    session = chat.session(SESSION)
    messages = [entry["message"] for entry in session.entries
                if entry.get("type") == "message" and isinstance(entry.get("message"), dict)]
    messages += list(session.messages)
    chat.close()
    relay.terminate()
    print(json.dumps({
        "outcome": "timeout" if timed_out.is_set() else outcome,
        "reason": reason, "notice": notice, "seconds": round(elapsed, 1),
        "approvals": approvals, **summarize(messages), "trajectory": messages,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
