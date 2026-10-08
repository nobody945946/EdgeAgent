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
