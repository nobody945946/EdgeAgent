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
from collections import Counter

RESEARCH = {"web_research", "web_search"}
CITATION = re.compile(r"\[\d+\]")
# Sending the asker off to look it up themselves: the Agent's job, not theirs.
SELF_SEARCH = re.compile(
    r"(您|你)可以(自行|自己|直接)?(上網)?(搜尋|查詢|查閱|前往)"
    r"|請(您|你)?(自行|自己|直接)(上網)?(搜尋|查詢|查閱|查看|前往)"
    r"|建議(您|你)?(直接|自行)?(前往|到|上).{0,15}(查看|查詢|搜尋)"
)



def repeated_calls(calls: list[dict]) -> list[str]:
    seen = Counter(f"{c.get('name')} {json.dumps(c.get('arguments'), ensure_ascii=False, sort_keys=True)}"
                   for c in calls)
    return [call for call, count in seen.items() if count > 1]


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
    if name == "no_tools":
        used = sorted({c.get("name") for turn in turns for c in turn.get("calls") or []})
        return f"called tools: {used}" if used else None
    if name == "first_turn_quiet":
        used = sorted({c.get("name") for c in (turns[0].get("calls") or [])}) if turns else []
        return f"first turn called {used}" if used else None
    raise ValueError(f"unknown check: {name}")


def failures(dialogue: dict, names: list[str]) -> dict[str, str]:
    """Every listed check the dialogue fails, plus turns that did not end
    normally (a crash, a cancel or a timeout is never a clean example)."""
    found = {name: why for name in names if (why := check(name, dialogue)) is not None}
    broken = [i + 1 for i, turn in enumerate(dialogue.get("dialogue") or []) if turn.get("outcome") != "done"]
    if broken:
        found["outcome"] = f"turns not done: {broken}"
    if not dialogue.get("dialogue"):
        found["outcome"] = "no turns"
    if dialogue.get("ended") not in ("end", "max_turns"):
        found["ended"] = f"conversation ended by: {dialogue.get('ended')}"
    return found
