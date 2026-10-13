"""Run one bench task through EdgeAgent's own NativeChat. Sandbox side only.

Approvals are answered the way a user pressing "Allow this turn" would. The
result (metrics plus the bench trajectory) is the last stdout line as JSON.

A scenario task (bench/scenario_cards.py) is a conversation instead of one
prompt: a simulated user, an open-weight model reading the scenario card,
writes each user line after reading only what the asker would see (the
Agent's answers, never its thinking or tool output), until it ends the
conversation or the card's turn limit. Its model is the Agent's own server
unless the run gives it one of its own (/simsock).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from edge_agent.chat import NativeChat

SESSION = "bench"
SIMULATOR_PORT = 18090
END = "[END]"
SIMULATOR_RULES = """你在幫忙測試一個 AI 助理。你扮演下面情境卡裡的使用者，用聊天視窗跟助理對話。
規則：
1. 一次只寫一則訊息，就像真人在聊天視窗打字：短、口語、台灣用語、繁體中文。
2. 不要提到情境卡、測試、扮演，也不要把「目標」原句講出來；照「步驟」一步一步來，可以自然地變化說法。
3. 讀助理的回答：目標達成了就結束；答偏了、沒做完、漏了你要的東西、承諾做不到的事，就像真人一樣直接指出或追問。
4. 要結束對話時，只輸出 [END]。
5. 只輸出你要說的那則訊息本身，不加引號、不加說明。"""


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


def mcp_servers(spec: dict) -> dict | None:
    """The task's MCP servers, through the product's own settings owner."""
    if not spec.get("mcp_servers"):
        return None
    from edge_agent.mcp import servers_for, validate_mcp_servers

    return servers_for("ui", validate_mcp_servers(spec["mcp_servers"]))


def session_messages(chat: NativeChat) -> list[dict]:
    # NativeChat checkpoints finished messages into Pi session entries.
    session = chat.session(SESSION)
    return [entry["message"] for entry in session.entries
            if entry.get("type") == "message" and isinstance(entry.get("message"), dict)] + list(session.messages)


def relay(listen_port: int, socket: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "/opt/bench/relay.py", "--listen", f"tcp:127.0.0.1:{listen_port}",
         "--connect", f"unix:{socket}"],
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    process.stdout.readline()
    return process


def simulate(card: dict, port: int, lines: list[str], answers: list[str], turns_left: int) -> str:
    """The simulated user's next line (END to stop). Its own lines are its
    side of the chat; the Agent's answers are what it reads."""
    card_text = json.dumps({key: card[key] for key in ("persona", "topic", "opening", "goal", "plan")
                            if key in card}, ensure_ascii=False, indent=1)
    messages = [{"role": "system", "content": f"{SIMULATOR_RULES}\n\n情境卡：\n{card_text}\n\n"
                                              f"你最多還能說 {turns_left} 則。"},
                {"role": "user", "content": "（對話開始。請說第一則訊息。）"}]
    for line, answer in zip(lines, answers, strict=True):
        messages += [{"role": "assistant", "content": line},
                     {"role": "user", "content": answer or "（助理沒有回答）"}]
    body = json.dumps({"messages": messages, "temperature": 0.8, "max_tokens": 400,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        text = json.loads(response.read())["choices"][0]["message"].get("content") or ""
    return text.strip().strip("「」\"") or END


LAPTOP = "/opt/bench/windows"  # bench/windows, mounted by run.py
FACTS = Path("/tmp/.laptop/machine.json")  # bench/laptop.py FACTS


def windows(machine: dict) -> None:
    """The laptop for the Agent's shell (bench/laptop.py): its facts where
    the module and scripts read them, the module on PSModulePath (PowerShell
    loads it on the first Windows command), the scripts first on PATH, and the
    environment Windows sets."""
    FACTS.parent.mkdir(parents=True, exist_ok=True)
    FACTS.write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
    os.environ["PSModulePath"] = os.pathsep.join([LAPTOP, *filter(None, [os.environ.get("PSModulePath")])])
    os.environ["PATH"] = f"{LAPTOP}/bin" + os.pathsep + os.environ["PATH"]
    os.environ.update({"COMPUTERNAME": machine["computer"], "USERDOMAIN": machine["computer"],
                       "USERNAME": machine["user"], "NUMBER_OF_PROCESSORS": str(machine["cpu"]["threads"]),
                       "PROCESSOR_ARCHITECTURE": "AMD64", "PROCESSOR_IDENTIFIER": machine["cpu"]["identifier"],
                       "OS": "Windows_NT"})


def main() -> None:
    # Read once and taken out of the environment: the Agent's shell inherits
    # it, and the spec holds the simulated user's script.
    spec = json.loads(os.environ.pop("BENCH_SPEC"))
    if spec.get("machine"):
        windows(spec["machine"])
    if spec.get("search_tools"):
        # rg and fd for Pi's grep/find tools (inherited by the Node process).
        os.environ["PATH"] = "/opt/searchtools/bin" + os.pathsep + os.environ["PATH"]
    relays = [relay(18089, "/sock/model.sock")]
    card = spec.get("scenario")
    simulator_port = 18089
    if card and Path("/simsock/model.sock").exists():
        relays.append(relay(SIMULATOR_PORT, "/simsock/model.sock"))
        simulator_port = SIMULATOR_PORT
    chat = NativeChat(model_port=18089, core_dir=Path("/opt/edgeagent"))
    # The product pins its one Agent session to a slot (it shares the server
    # with the character); the bench runs several jobs on one server, so the
    # server picks a free slot for each, as it did before the pinning.
    chat.slot_id = None
    chat.hardware = spec.get("hardware", "")  # the laptop's, for the runtime section (products that have it)
    # The adapter's temperature per mode, as the product's load plan sets it (products that have it).
    chat.sampling = dict(spec.get("sampling") or {})
    # A scenario's registered APIs, as edge_agent.apis.for_session gives them: listed without the key,
    # the key in the shell's environment (products that have it).
    chat.api_session = ([{k: api[k] for k in ("name", "purpose", "base_url", "docs", "key_in", "key_name")}
                         | ({"key_env": api["key_env"]} if api["key_in"] != "none" else {})
                         for api in spec.get("apis", [])],
                        {api["key_env"]: api["key"] for api in spec.get("apis", []) if api["key_in"] != "none"})
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

    approvals = 0

    # A card's budget is per turn (run.py: timeout_s), within the
    # conversation's (conversation_s): a whole-conversation budget cut healthy
    # five-turn conversations at their last turn (2026-10-08).
    deadline = time.monotonic() + spec.get("conversation_s", spec["timeout_s"])

    def turn(text: str) -> dict:
        """One user line through the Agent: what the asker read and how it ended."""
        timer = threading.Timer(max(0.0, min(spec["timeout_s"], deadline - time.monotonic())), expire)
        timer.start()
        try:
            return reply(text)
        finally:
            timer.cancel()

    def reply(text: str) -> dict:
        nonlocal approvals
        outcome, reason, notice = "none", "", ""
        # What the asker reads: every text delta, the host's own notices included.
        answer: list[str] = []
        unverified: list[str] = []
        for kind, payload in chat.stream_reply(
            SESSION, text, thinking_effort=spec["effort"], agent=True,
            working_dir=Path("/work"), permissions=spec.get("permissions"),
            scope=spec.get("scope", "change"), mcp_servers=mcp_servers(spec),
        ):
            if kind == "delta":
                answer.append(str(payload.get("text", "")))
            elif kind == "approval":
                approvals += 1
                chat.resolve_approval(SESSION, str(payload["call_id"]), True, "turn")
            elif kind in ("done", "error", "cancelled"):
                outcome = kind
                unverified = list(payload.get("unverified") or [])
                reason = str(payload.get("reason", ""))
                notice = str(payload.get("message", ""))[:300]
        return {"answer": "".join(answer), "outcome": "timeout" if timed_out.is_set() else outcome,
                "reason": reason, "notice": notice, "unverified": unverified}

    started = time.monotonic()
    dialogue: list[dict] = []
    ended = ""
    if not card:
        last = turn(spec["prompt"])
    else:
        seen = 0
        last = {"answer": "", "outcome": "none", "reason": "", "notice": "", "unverified": []}
        ended = "max_turns"
        while len(dialogue) < card["max_turns"]:
            if time.monotonic() >= deadline:
                ended = "conversation timeout"
                break
            # A takeover (run.py --takeover) replays the student's own
            # conversation up to where the teacher takes over: the user said
            # then what the user said that time.
            script = spec.get("script") or []
            line = (script[len(dialogue)] if len(dialogue) < len(script) else
                    simulate(card, simulator_port, [t["user"] for t in dialogue],
                             [t["answer"] for t in dialogue], card["max_turns"] - len(dialogue)))
            if END in line:
                ended = "end" if dialogue else "simulator ended before speaking"
                break
            last = turn(line)
            messages = session_messages(chat)
            calls = [{"name": item.get("name"), "arguments": item.get("arguments")}
                     for message in messages[seen:] if message.get("role") == "assistant"
                     for item in message.get("content") or []
                     if isinstance(item, dict) and item.get("type") == "toolCall"]
            seen = len(messages)
            dialogue.append({"user": line, "answer": last["answer"], "outcome": last["outcome"],
                             "calls": calls})
            if last["outcome"] != "done":
                ended = "turn " + last["outcome"]
                break
    elapsed = time.monotonic() - started
    messages = session_messages(chat)
    chat.close()
    for process in relays:
        process.terminate()
    if card and last["outcome"] == "done" and ended not in ("end", "max_turns"):
        last["outcome"] = "error"
    print(json.dumps({
        "outcome": last["outcome"], "reason": last["reason"], "notice": last["notice"],
        "seconds": round(elapsed, 1), "approvals": approvals, **summarize(messages),
        "answer": last["answer"], "unverified": last["unverified"],
        **({"dialogue": dialogue, "ended": ended} if card else {}),
        "trajectory": messages,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
