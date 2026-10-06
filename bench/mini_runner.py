"""Run one bench task in the mini-swe-agent harness (one `bash` tool), the
harness of the open trajectories in nvidia/Open-SWE-Traces data/minisweagent.
Sandbox side only; same contract as agent_runner.py: the result is the last
stdout line as JSON. The templates, the observation format and its limits
are the recorded ones (bench/mini_templates.json), so a model trained on
those trajectories meets the same harness here.

The workspace is /testbed, as in the recordings (the sandbox mounts the
task workspace there too).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TEMPLATES = json.loads(Path(__file__).with_name("mini_templates.json").read_text(encoding="utf-8"))
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def html_safe_json(value: dict) -> str:
    """Jinja's tojson(indent=2), as the recordings serialize observations."""
    return (json.dumps(value, indent=2).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("'", "\\u0027"))


def observe(command: str, cwd: str) -> tuple[str, str | None]:
    """Run one command in a fresh shell; the observation text and, when the
    command submitted, the submitted output."""
    limit = TEMPLATES["max_output"]
    try:
        done = subprocess.run(command, shell=True, executable="/bin/bash", cwd=cwd, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=TEMPLATES["timeout_s"], encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        info = (f"An error occurred while executing the command: Command '{command}' timed out "
                f"after {TEMPLATES['timeout_s']} seconds")
        return ('{\n  "returncode": -1,\n  "output": "", "exception_info": '
                + json.dumps(info).replace("<", "\\u003c").replace(">", "\\u003e")
                .replace("&", "\\u0026").replace("'", "\\u0027") + "\n}"), None
    output = done.stdout
    lines = output.lstrip().splitlines()
    if done.returncode == 0 and lines and lines[0].strip() == SUBMIT:
        return "", "\n".join(lines[1:])
    if len(output) < limit:
        return html_safe_json({"returncode": done.returncode, "output": output}), None
    half = limit // 2
    return html_safe_json({"returncode": done.returncode, "output_head": output[:half],
                           "output_tail": output[-half:], "elided_chars": len(output) - limit,
                           "warning": "Output too long."}), None


def chat(messages: list[dict], options: dict) -> dict:
    body = {"messages": messages, "tools": [TEMPLATES["tool"]],
            "temperature": float(options.get("temperature", 0.6)),
            "top_p": float(options.get("top_p", 0.95)),
            "max_tokens": int(options.get("max_tokens", 4096)),
            "chat_template_kwargs": {"enable_thinking": bool(options.get("thinking", False))}}
    request = urllib.request.Request("http://127.0.0.1:18089/v1/chat/completions",
                                     json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=900) as response:
        return json.loads(response.read())


def main() -> None:
    spec = json.loads(os.environ["BENCH_SPEC"])
    options = spec.get("mini") or {}
    relay = subprocess.Popen(
        [sys.executable, "/opt/bench/relay.py", "--listen", "tcp:127.0.0.1:18089",
         "--connect", "unix:/sock/model.sock"],
        stdout=subprocess.PIPE,
    )
    assert relay.stdout is not None
    relay.stdout.readline()
    messages = [{"role": "system", "content": TEMPLATES["system"]},
                {"role": "user", "content": TEMPLATES["instance"].replace("{task}", spec["prompt"])}]
    started = time.monotonic()
    deadline = started + max(60, spec["timeout_s"] - 60)
    outcome, submitted, notice = "step_limit", None, ""
    usage = {"input": 0, "output": 0, "cacheRead": 0}
    tools: dict[str, int] = {}
    steps = 0
    try:
        while steps < int(options.get("step_limit", 100)):
            if time.monotonic() > deadline:
                outcome = "timeout"
                break
            steps += 1
            data = chat(messages, options)
            for key, field in (("input", "prompt_tokens"), ("output", "completion_tokens")):
                usage[key] += int((data.get("usage") or {}).get(field) or 0)
            reply = data["choices"][0]["message"]
            calls = reply.get("tool_calls") or []
            messages.append({"role": "assistant", "content": reply.get("content") or "",
                             **({"tool_calls": calls} if calls else {})})
            if not calls:
                messages.append({"role": "user", "content": TEMPLATES["format_error"]})
                continue
            for call in calls:
                tools["bash"] = tools.get("bash", 0) + 1
                try:
                    command = json.loads(call["function"]["arguments"])["command"]
                except (ValueError, KeyError, TypeError):
                    command = None
                if call["function"].get("name") != "bash" or not isinstance(command, str):
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": TEMPLATES["format_error"]})
                    continue
                text, submitted = observe(command, "/testbed")
                if submitted is not None:
                    break
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
            if submitted is not None:
                outcome = "done"
                break
    except Exception as exc:  # noqa: BLE001 - a crash is a result here
        outcome, notice = "error", f"{type(exc).__name__}: {exc}"[:300]
    relay.terminate()
    print(json.dumps({
        "outcome": outcome, "reason": "", "notice": notice, "seconds": round(time.monotonic() - started, 1),
        "approvals": 0, "turns": steps, "tools": tools, "tool_errors": {}, "usage": usage,
        "answer": (submitted or "")[-2000:], "unverified": [], "trajectory": messages,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
