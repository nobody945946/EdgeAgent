"""Programmatic checks on one scenario dialogue (bench/scenarios/families.json).

A dialogue is what agent_runner.py returns for a scenario card: `dialogue`
(each user line, the answer the asker read, the tool calls and how the turn
ended) and the Pi `trajectory`. These checks run before the judge
model and are the more reliable gate: a dialogue that fails one never
becomes a training row, whatever the judge says.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FAMILIES = Path(__file__).resolve().parent / "scenarios" / "families.json"

RESEARCH = {"web_research", "web_search"}
CITATION = re.compile(r"\[\d+\]")
# Sending the asker off to look it up themselves: the Agent's job, not theirs.
SELF_SEARCH = re.compile(
    r"(您|你)可以(自行|自己|直接)?(上網)?(搜尋|查詢|查閱|前往)"
    r"|請(您|你)?(自行|自己|直接)(上網)?(搜尋|查詢|查閱|查看|前往)"
    r"|建議(您|你)?(直接|自行)?(前往|到|上).{0,15}(查看|查詢|搜尋)"
)



# Calls that can change what a later local call returns. Running a test again
# after editing the code is what family I asks for (2026-10-08: the teacher's
# reruns after a fix failed this check); a web lookup never changes that way.
CHANGES = {"write", "edit", "powershell"}
WEB = RESEARCH | {"web_fetch"}


def repeated_calls(calls: list[dict]) -> list[str]:
    """Calls made again in one turn with nothing in between that could change
    their result: any repeated web lookup, or a local call with no write, edit
    or command since its last run."""
    last: dict[str, int] = {}
    found: list[str] = []
    for index, call in enumerate(calls):
        key = f"{call.get('name')} {json.dumps(call.get('arguments'), ensure_ascii=False, sort_keys=True)}"
        if key in last and key not in found and (
                call.get("name") in WEB or not any(c.get("name") in CHANGES for c in calls[last[key] + 1:index])):
            found.append(key)
        last[key] = index
    return found


# Searching a whole drive from its root (a missing file made the teacher run
# Get-ChildItem / -Recurse): slow and intrusive on the owner's laptop.
ROOT_PATH = re.compile(r"""^\s*['"]?(?:/|[A-Za-z]:[\\/]?)['"]?\s*$""")
ROOT_SCAN = re.compile(r"""(?i)(?:get-childitem|gci|dir|ls)\s+(?:-(?:literal)?path\s+)?['"]?(?:/|[a-z]:[\\/]?)['"]?(?:\s|$)""")


# Reading the machine itself (hardware, OS, processes, disks): in the bench
# that is the dev box's sandbox (2026-10-08: answers told the owner he runs
# Docker on an RTX 3090), so the facts in the answer are false on the laptop.
# Get-Process -Id checks a process the Agent started itself, not the machine.
SYSTEM_PROBE = re.compile(
    r"(?i)get-ciminstance|get-wmiobject|win32_|nvidia-smi|lscpu|systeminfo|get-computerinfo|/proc/|/sys/"
    r"|\buname\b|get-psdrive|\$psversiontable|get-process(?!\s+-id\b)|\bdf\s|\bfree\s+-|\[(?:system\.)?environment\]"
    r"|osversion|\$env:(?:processor|computername|os|number_of)|\bhostname\b|docker|nproc|\bps\s+(?:aux|-e)"
    r"|get-hotfix|get-netadapter|ipconfig|\bip\s+a")
SYSTEM_PATH = re.compile(r"^/(?:proc|sys|etc|var|usr)\b")
# Words that, in an answer about the Agent itself, can only come from the sandbox.
SANDBOX_WORDS = re.compile(r"(?i)docker|容器|ubuntu|\blinux\b|rtx\s*3090|\b3090\b|/proc/")


def family_checks(family_id: str) -> list[str]:
    """A family's checks: judging policy, read when judging (not baked into
    tasks, so changing them never re-records a conversation)."""
    return json.loads(FAMILIES.read_text(encoding="utf-8"))["families"][family_id]["checks"]


def probes_the_machine(call: dict) -> bool:
    arguments = call.get("arguments") or {}
    if call.get("name") == "powershell":
        return bool(SYSTEM_PROBE.search(str(arguments.get("command", ""))))
    return call.get("name") in ("ls", "read", "find", "grep") and bool(SYSTEM_PATH.match(str(arguments.get("path", ""))))


API_CALL = re.compile(r"(?i)invoke-restmethod|invoke-webrequest|\birm\b|\biwr\b|\bcurl\b|requests\.|urllib|httpx")


def calls_an_api(call: dict) -> bool:
    arguments = call.get("arguments") or {}
    if call.get("name") == "web_fetch":
        return True
    return call.get("name") == "powershell" and bool(API_CALL.search(str(arguments.get("command", ""))))


def scans_a_drive(call: dict) -> bool:
    arguments = call.get("arguments") or {}
    if call.get("name") in ("find", "grep", "ls") and ROOT_PATH.match(str(arguments.get("path", ""))):
        return True
    command = str(arguments.get("command", ""))
    return bool(ROOT_SCAN.search(command)) and "-recurse" in command.lower()


def check(name: str, dialogue: dict) -> str | None:
    """None when the dialogue passes check `name`, else why it does not."""
    turns = dialogue.get("dialogue") or []
    if name == "cites":
        missing = [i + 1 for i, turn in enumerate(turns)
                   if RESEARCH & {c.get("name") for c in turn.get("calls") or []}
                   and not CITATION.search(turn.get("answer", ""))]
        return f"researched without citing in turn {missing}" if missing else None
    if name == "no_self_search":
        found = [m.group(0) for turn in turns for m in [SELF_SEARCH.search(turn.get("answer", ""))] if m]
        return f"sends the asker to search: {found[:2]}" if found else None
    if name == "no_repeat_search":
        repeats = [r for turn in turns for r in repeated_calls(turn.get("calls") or [])]
        return f"same call repeated in a turn: {repeats[:2]}" if repeats else None
    if name == "must_call_research":
        called = any(RESEARCH & {c.get("name") for c in turn.get("calls") or []} for turn in turns)
        return None if called else "never researched"
    if name == "called_api":
        called = [c for turn in turns for c in turn.get("calls") or [] if calls_an_api(c)]
        return None if called else "never called an API (web_fetch, Invoke-RestMethod, Python)"
    if name == "no_sandbox_words":
        asked = " ".join(turn.get("user", "") for turn in turns).lower()
        words = sorted({m.group(0) for turn in turns for m in SANDBOX_WORDS.finditer(turn.get("answer", ""))
                        if m.group(0).lower() not in asked})
        return f"describes the sandbox: {words}" if words else None
    raise ValueError(f"unknown check: {name}")


def unfinished_turns(trajectory: list[dict]) -> list[int]:
    """Turns whose last assistant message did not finish (stopped at the
    output limit, after a tool call, aborted, an error): a turn cut at the
    output limit still ends as done, and its answer stops mid-sentence."""
    last: list[dict | None] = []
    for message in trajectory:
        if message.get("role") == "user":
            last.append(None)
        elif message.get("role") == "assistant" and last:
            last[-1] = message
    return [n for n, message in enumerate(last, 1)
            if message is not None and message.get("stopReason", "stop") != "stop"]


def failures(dialogue: dict, names: list[str]) -> dict[str, str]:
    """Every listed check the dialogue fails, plus what no family allows:
    turns that did not end normally (a crash, a cancel or a timeout is never
    a clean example) or whose last message did not finish, searching a whole
    drive and reading the machine itself."""
    found = {name: why for name in names if (why := check(name, dialogue)) is not None}
    calls = [c for turn in dialogue.get("dialogue") or [] for c in turn.get("calls") or []]
    scans = [c for c in calls if scans_a_drive(c)]
    if scans:
        found["drive_scan"] = f"searched a whole drive: {scans[0].get('name')}"
    probes = [c for c in calls if probes_the_machine(c)]
    if probes:
        found["system_probe"] = f"read the machine itself: {json.dumps(probes[0].get('arguments'), ensure_ascii=False)[:120]}"
    unfinished = unfinished_turns(dialogue.get("trajectory") or [])
    if unfinished:
        found["cut_off"] = f"turns whose last message did not finish: {unfinished}"
    broken = [i + 1 for i, turn in enumerate(dialogue.get("dialogue") or []) if turn.get("outcome") != "done"]
    if broken:
        found["outcome"] = f"turns not done: {broken}"
    if not dialogue.get("dialogue"):
        found["outcome"] = "no turns"
    if dialogue.get("ended") not in ("end", "max_turns"):
        found["ended"] = f"conversation ended by: {dialogue.get('ended')}"
    return found
