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


def repeated_calls(calls: list[dict]) -> dict[str, int]:
    """Calls made again in one turn with nothing in between that could change
    their result: any repeated web lookup, or a local call with no write, edit
    or command since its last run; each with where it was first made again."""
    last: dict[str, int] = {}
    found: dict[str, int] = {}
    for index, call in enumerate(calls):
        key = f"{call.get('name')} {json.dumps(call.get('arguments'), ensure_ascii=False, sort_keys=True)}"
        if key in last and key not in found and (
                call.get("name") in WEB or not any(c.get("name") in CHANGES for c in calls[last[key] + 1:index])):
            found[key] = index
        last[key] = index
    return found


# Searching a whole drive from its root (a missing file made the teacher run
# Get-ChildItem / -Recurse): slow and intrusive on the owner's laptop.
ROOT_PATH = re.compile(r"""^\s*['"]?(?:/|[A-Za-z]:[\\/]?)['"]?\s*$""")
ROOT_SCAN = re.compile(r"""(?i)(?:get-childitem|gci|dir|ls)\s+(?:-(?:literal)?path\s+)?['"]?(?:/|[a-z]:[\\/]?)['"]?(?:\s|$)""")


# Reading the machine itself (hardware, OS, processes, disks) through what the
# sandbox does not dress as the laptop: there the bench box answers
# (2026-10-08: answers told the owner he runs Docker on an RTX 3090), so the
# facts in the answer are false on the laptop. Windows' own commands
# (Get-CimInstance, Get-Process, Get-PSDrive, Get-ComputerInfo, nvidia-smi,
# systeminfo, hostname, the environment) answer with the laptop
# (bench/laptop.py). Linux's own commands never run on the laptop, so using
# one fails a dialogue whatever it printed.
SYSTEM_PROBE = re.compile(
    r"(?i)lscpu|/proc/|/sys/|\buname\b|\bdf\s"
    r"|\bfree\s+-|docker|nproc|\bps\s+(?:aux|-e)"
    r"|get-hotfix|get-netadapter|ipconfig|\bip\s+a")
# $PSVersionTable (a constant) and [Environment] (.NET itself) cannot be
# dressed as the laptop, but only some of what they print is false there
# ($PSVersionTable.PSVersion is the laptop's 7.6.6 too): a dialogue fails
# when a tool result the Agent read names the sandbox's system.
SANDBOX_OUTPUT = re.compile(r"(?i)GNU/Linux|debian|\bUnix\s+\d|linux-gnu|\bLinux\s+\d+\.\d+\.\d+|/home/bench\b|/opt/pwsh\b|/opt/bench\b")
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


# The sandbox has no network. A card with a stand-in web (its "web") answers
# every name there (web_fixture.py); a card without one has nothing, so what
# the Agent met when it reached out there ("could not be looked up: the network
# may be down", EAI_AGAIN, a failed pip install) is false on the laptop, whose
# network works (2026-10-14: 25 of v3-scen's passing dialogues met it, most
# telling the owner his network was down; 6 tool and Python jobs too). A
# dialogue fails when it reached for the network on such a card, or when a tool
# result it read says a name did not resolve (a script it wrote can reach out
# where no call shows it); dataset.py refuses any job on the second.
NETWORK_COMMAND = re.compile(
    API_CALL.pattern + r"|\bwget\b|\bpip3?\s+install|-m\s+pip\s+install|\bnpm\s+(?:i|install|ci)\b|\bnpx\b"
    r"|winget|install-module|install-package|test-netconnection|test-connection|resolve-dnsname"
    r"|gethostentry|gethostaddresses|\bping\b|nslookup")
NO_NETWORK = re.compile(
    r"(?i)could not be looked up|name resolution|EAI_AGAIN|getaddrinfo|name or service not known"
    r"|could not resolve host|no such host is known|network is unreachable|Resource temporarily unavailable \(")


def unresolved(trajectory: list[dict]) -> list[str]:
    """What the tool results the Agent read said about names that did not resolve."""
    return sorted({m.group(0) for text in tool_results(trajectory) for m in NO_NETWORK.finditer(text)})


def reaches_the_network(call: dict) -> bool:
    if call.get("name") in WEB:
        return True
    return call.get("name") == "powershell" and bool(NETWORK_COMMAND.search(str((call.get("arguments") or {}).get("command", ""))))


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


def tool_results(trajectory: list[dict]) -> list[str]:
    """The text of every tool result the Agent read."""
    return ["".join(part.get("text", "") for part in message.get("content") or [] if isinstance(part, dict))
            for message in trajectory if message.get("role") == "toolResult"]


def failures(dialogue: dict, names: list[str], *, web: bool) -> dict[str, str]:
    """Every listed check the dialogue fails, plus what no family allows:
    turns that did not end normally (a crash, a cancel or a timeout is never
    a clean example) or whose last message did not finish, searching a whole
    drive, reading the machine itself and meeting the sandbox's missing
    network (`web`: the card has a stand-in web)."""
    found = {name: why for name in names if (why := check(name, dialogue)) is not None}
    calls = [c for turn in dialogue.get("dialogue") or [] for c in turn.get("calls") or []]
    scans = [c for c in calls if scans_a_drive(c)]
    if scans:
        found["drive_scan"] = f"searched a whole drive: {scans[0].get('name')}"
    probes = [c for c in calls if probes_the_machine(c)]
    if probes:
        found["system_probe"] = f"read the machine itself: {json.dumps(probes[0].get('arguments'), ensure_ascii=False)[:120]}"
    told = [m.group(0) for text in tool_results(dialogue.get("trajectory") or []) for m in SANDBOX_OUTPUT.finditer(text)]
    if told and "system_probe" not in found:
        found["system_probe"] = f"a tool result named the sandbox's system: {sorted(set(told))[:3]}"
    reached = [] if web else [c for c in calls if reaches_the_network(c)]
    if reached:
        found["no_network"] = f"reached for the network on a card without a web: {reached[0].get('name')}"
    lookups = unresolved(dialogue.get("trajectory") or [])
    if lookups and "no_network" not in found:
        found["no_network"] = f"a tool result said a name did not resolve: {lookups[:2]}"
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


def first_offence(dialogue: dict, names: list[str], *, web: bool) -> tuple[int, int] | None:
    """Where a dialogue first breaks a check that one call breaks by itself,
    as (turn, call) indexes into its turns' calls: a whole-drive search, a
    probe of the machine, the network on a card without a web, and (when the
    family checks no_repeat_search) a call made again with nothing between
    that could change its result. A takeover (bench/run.py) from any later
    point keeps that call and fails the same check; what a tool result said,
    and what the judge failed, name no call."""
    found = []
    for turn, entry in enumerate(dialogue.get("dialogue") or []):
        calls = entry.get("calls") or []
        found += [(turn, index) for index, call in enumerate(calls)
                  if scans_a_drive(call) or probes_the_machine(call) or (not web and reaches_the_network(call))]
        if "no_repeat_search" in names:
            found += [(turn, index) for index in repeated_calls(calls).values()]
    return min(found, default=None)
