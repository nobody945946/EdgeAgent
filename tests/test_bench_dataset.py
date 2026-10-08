"""bench/dataset.py: one product version per output, but a task's own
settings (its MCP servers, web denied) are not a version."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import dataset

from edge_agent import prompt

READ = {"name": "read", "description": "Read a file", "parameters": {"type": "object"}}
WEB = {"name": "web_fetch", "description": "Read web page", "parameters": {"type": "object"}}


def _row(run: str, tools: list[dict], extra: str = "") -> dict:
    system = prompt.system_prompt("change") + "\n<cwd>/work</cwd>\n" + extra
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "測試"}],
            "source": {"run": run, "first_tools": {t["name"]: dataset._digest(t) for t in tools}}}


def test_a_tasks_servers_and_tool_subset_are_not_a_version() -> None:
    servers = "<addendum>\n測試伺服器\n</addendum>\n\n<mcp_servers>\n- mcp__test: 測試\n</mcp_servers>\n"
    rows = [_row("tools", [READ, WEB]), _row("mcp", [READ], servers)]
    assert dataset.mixed_versions(rows) == {}


def test_a_tool_defined_two_ways_or_another_prompt_is_refused() -> None:
    changed = READ | {"description": "Read a file (new)"}
    mixed = dataset.mixed_versions([_row("a", [READ]), _row("b", [changed])])
    assert list(mixed) == ["tool read"]
    mixed = dataset.mixed_versions([_row("a", [READ]), _row("b", [READ], "多一段產品文字\n")])
    assert list(mixed) == ["system change"]


def test_the_final_answer_keeps_its_reasoning() -> None:
    trajectory = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "先算日期"},
                                                    {"type": "text", "text": "答案 [1]"}]}]
    assert dataset.final_answer(trajectory) == {"role": "assistant", "content": "答案 [1]",
                                                "reasoning_content": "先算日期"}


def test_both_thinking_modes_from_one_row() -> None:
    row = {"messages": [{"role": "user", "content": "問"},
                        {"role": "assistant", "content": "答", "reasoning_content": "想"}], "tools": []}
    on, off = dataset.with_thinking_modes(row, both=True)
    assert on["thinking"] and on["messages"][1]["reasoning_content"] == "想"
    assert not off["thinking"] and "reasoning_content" not in off["messages"][1]
    assert dataset.with_thinking_modes({"messages": [{"role": "user", "content": "問"}]}, both=True) == [
        {"messages": [{"role": "user", "content": "問"}], "thinking": False}]


def test_a_new_user_turn_ends_a_stretch() -> None:
    first = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "一"}]}
    second = {"messages": [*first["messages"], {"role": "assistant", "content": "答"},
                           {"role": "user", "content": "二"}]}
    assert dataset.user_turns(second) > dataset.user_turns(first)


def test_rows_teach_taiwan_forms_but_leave_traditional_text_alone() -> None:
    assert dataset.taiwan_traditional("这个软件") == "這個軟體"
    assert dataset.taiwan_traditional("測試通過") == "測試通過"
