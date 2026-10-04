"""MCP servers: configuration (edge_agent/mcp.py) and the bridge's gate on
every call, with the pinned Pi SDK and a fixture server (fixtures/mcp_fixture.mjs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edge_agent.chat import NativeChat
from edge_agent.mcp import servers_for, validate_mcp_servers
from tests.test_agent_chat import installed_pi, repeating_model  # noqa: F401 - fixture

FIXTURE = Path(__file__).with_name("fixtures") / "mcp_fixture.mjs"


def test_servers_are_validated_and_filled_with_safe_defaults() -> None:
    servers = validate_mcp_servers({"fin": {"command": "node", "args": ["x.mjs"]}})
    assert servers["fin"] | {} == {
        "command": "node", "args": ["x.mjs"], "env": {}, "description": "",
        "enabled": True, "permission": "ask", "hardware": False, "clients": ["ui"], "tools": [],
        "confirm": "each",
    }
    for bad, message in [
        ({"a b": {"command": "x"}}, "letters"),
        ({"fin": {"url": "https://example.com/mcp"}}, "unknown fields"),
        ({"fin": {"command": "x", "permission": "always"}}, "permission"),
        ({"fin": {"command": "x", "clients": ["web"]}}, "clients"),
        ({"my-fin": {"command": "x"}, "my_fin": {"command": "y"}}, "collides"),
    ]:
        with pytest.raises((ValueError, TypeError), match=message):
            validate_mcp_servers(bad)


def test_discord_gets_only_the_servers_the_owner_gave_it() -> None:
    servers = validate_mcp_servers({
        "fin": {"command": "node"},
        "notes": {"command": "node", "clients": ["ui", "discord"]},
        "off": {"command": "node", "enabled": False, "clients": ["discord"]},
    })
    assert set(servers_for("ui", servers)) == {"fin", "notes"}
    assert set(servers_for("discord", servers)) == {"notes"}


def _run(chat, tmp_path, servers, approve, prompt="查一下台積電"):
    events = []
    for name, data in chat.stream_reply("mcp", prompt, agent=True, working_dir=tmp_path,
                                        permissions={"edit": "ask", "shell": "ask", "web": "deny"},
                                        mcp_servers=servers):
        events.append((name, data))
        if name == "approval":
            chat.resolve_approval("mcp", data["call_id"], approve(data), "turn")
    return events


def _server(tmp_path, **extra):
    log = tmp_path / "calls.jsonl"
    env = {"MCP_FIXTURE_LOG": str(log)}
    if extra.pop("hardware_tools", False):
        env["MCP_FIXTURE_HARDWARE"] = "1"
    return {"command": "node", "args": [str(FIXTURE)], "env": env,
            "description": "Stock quotes and orders, or a drone", **extra}, log


def _ran(log: Path) -> list[str]:
    return [json.loads(line)["name"] for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_read_only_tools_run_and_destructive_ones_ask_once(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=False)
    state["sequence"] = [
        ("tool_search", {"query": "stock quote"}),
        ("mcp__fin__quote", {"symbol": "2330"}),
        ("tool_search", {"query": "place stock order"}),
        ("mcp__fin__place_order", {"symbol": "2330", "shares": 1}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: False)
    finally:
        chat.close()
    approvals = [data for name, data in events if name == "approval"]
    assert [(a["tool"], a["explicit"]) for a in approvals] == [("mcp__fin__place_order", "mcp_destructive")]
    assert _ran(log) == ["quote"]  # the denied order never reached the server
    first = state["requests"][0]
    assert {t["function"]["name"] for t in first.get("tools", [])} >= {"tool_search"}
    assert not any(t["function"]["name"].startswith("mcp__") for t in first.get("tools", []))
    assert not (Path(__file__).resolve().parents[1] / ".runtime/pi-state/mcp.log").exists()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_hardware_needs_its_own_approval_every_time(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=True, hardware_tools=True)
    state["sequence"] = [
        ("tool_search", {"query": "drone arm telemetry"}),
        ("mcp__drone__telemetry", {}),
        ("mcp__drone__arm", {}),
        ("mcp__drone__arm", {}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"drone": server}, approve=lambda data: True, prompt="起飛前檢查")
    finally:
        chat.close()
    approvals = [data for name, data in events if name == "approval"]
    # "Allow this turn" was pressed, yet each arm asked again.
    assert [(a["tool"], a["explicit"]) for a in approvals] == [("mcp__drone__arm", "hardware")] * 2
    assert _ran(log) == ["telemetry", "arm", "arm"]


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_an_ask_server_asks_even_for_reads(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    server, log = _server(tmp_path, permission="ask", hardware=False)
    state["sequence"] = [("tool_search", {"query": "stock quote"}), ("mcp__fin__quote", {"symbol": "2330"})]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: False)
    finally:
        chat.close()
    assert [(d["tool"], d["explicit"]) for n, d in events if n == "approval"] == [("mcp__fin__quote", "")]
    assert _ran(log) == []


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_with_mcp_the_read_only_mode_still_cannot_change_anything(tmp_path, repeating_model):  # noqa: F811
    """With MCP the tool set is an exclude list, not an allowlist: the
    interview mode must still have no editing or shell tool, and an MCP tool
    that changes things is refused without asking."""
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=False)
    state["sequence"] = [
        ("tool_search", {"query": "place stock order"}),
        ("mcp__fin__place_order", {"symbol": "2330", "shares": 1}),
        ("write", {"path": "x.txt", "content": "x"}),
    ]
    chat = NativeChat(model_port=port)
    events = []
    try:
        for name, data in chat.stream_reply("mcp", "想做一個下單工具", agent=True, working_dir=tmp_path,
                                            interview=True, mcp_servers={"fin": server}):
            events.append((name, data))
            if name == "approval":
                chat.resolve_approval("mcp", data["call_id"], True, "once")
    finally:
        chat.close()
    assert not any(name == "approval" for name, _ in events)
    assert _ran(log) == [] and not (tmp_path / "x.txt").exists()
    for request in state["requests"]:
        names = {t["function"]["name"] for t in request.get("tools", [])}
        assert not names & {"edit", "write", "powershell", "bash"}
    assert "inspection only" in json.dumps(state["requests"][2])


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_only_the_listed_tools_can_be_found_or_called(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=False, tools=["quo*"])
    state["sequence"] = [
        ("tool_search", {"query": "stock quote order journal note"}),
        ("mcp__fin__quote", {"symbol": "2330"}),
        ("mcp__fin__place_order", {"symbol": "2330", "shares": 1}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: True)
    finally:
        chat.close()
    found = next(d for n, d in events if n == "tool" and d["phase"] == "finished" and d["tool"] == "tool_search")
    assert "quote" in json.dumps(found) and "place_order" not in json.dumps(found)
    assert not any(n == "approval" for n, _ in events)
    assert _ran(log) == ["quote"]


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_a_turn_confirm_server_lets_one_approval_cover_the_turn(tmp_path, repeating_model):  # noqa: F811
    """For servers that mark every action destructive (a browser's clicks),
    the owner can let "allow this turn" cover them; hardware never."""
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=False, confirm="turn")
    state["sequence"] = [
        ("tool_search", {"query": "place stock order"}),
        ("mcp__fin__place_order", {"symbol": "2330", "shares": 1}),
        ("mcp__fin__place_order", {"symbol": "2317", "shares": 1}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: True)
    finally:
        chat.close()
    assert [(d["tool"], d["explicit"]) for n, d in events if n == "approval"] == [("mcp__fin__place_order", "")]
    assert _ran(log) == ["place_order", "place_order"]
