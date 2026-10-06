"""MCP servers: configuration (edge_agent/mcp.py) and the bridge's gate on
every call, with the pinned Pi SDK and a fixture server (fixtures/mcp_fixture.mjs)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from edge_agent.chat import NativeChat
from edge_agent.mcp import servers_for, validate_mcp_servers
from edge_agent.runtime import CORE_ROOT, NODE_VERSION
from tests.test_agent_chat import installed_pi, repeating_model  # noqa: F401 - fixture

FIXTURE = Path(__file__).with_name("fixtures") / "mcp_fixture.mjs"


@pytest.mark.skipif(
    os.name != "nt" or not installed_pi(), reason="requires Windows pinned Node"
)
def test_probe_cleans_children_after_launcher_exits(tmp_path):
    node = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    pid_file = tmp_path / "pid.txt"
    result = subprocess.run(
        [
            str(node),
            str(CORE_ROOT / "scripts/mcp_probe.mjs"),
            "--",
            str(node),
            str(FIXTURE),
        ],
        env={
            **os.environ,
            "MCP_FIXTURE_LAUNCHER_EXIT": "1",
            "MCP_FIXTURE_PID_FILE": str(pid_file),
        },
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    child_pid = int(pid_file.read_text())
    check = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"if(Get-Process -Id {child_pid} -ErrorAction SilentlyContinue){{exit 1}}",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert check.returncode == 0, "orphaned MCP child remains alive"


@pytest.mark.skipif(
    os.name != "nt" or not installed_pi(), reason="requires Windows pinned Node"
)
def test_probe_resolves_bare_cmd_and_calls_in_one_session(tmp_path):
    node = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    shim = tmp_path / "mcp-probe-fixture.cmd"
    shim.write_text(f'@"{node}" "{FIXTURE}"\n', encoding="utf-8")
    result = subprocess.run(
        [
            str(node),
            str(CORE_ROOT / "scripts/mcp_probe.mjs"),
            "--call",
            "quote",
            "{}",
            "--call",
            "quote",
            "{}",
            "--",
            shim.stem,
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    probe = json.loads(result.stdout)
    assert probe["count"] == 3
    assert [call["name"] for call in probe["calls"]] == ["quote", "quote"]
    assert not any(call["isError"] for call in probe["calls"])


def test_servers_are_validated_and_filled_with_safe_defaults() -> None:
    servers = validate_mcp_servers({"fin": {"command": "node", "args": ["x.mjs"]}})
    assert servers["fin"] | {} == {
        "command": "node", "args": ["x.mjs"], "env": {}, "description": "",
        "enabled": True, "permission": "ask", "hardware": False, "clients": ["ui"], "tools": [],
        "confirm": "each", "read_only_tools": [],
    }
    local = validate_mcp_servers({"office": {"url": "http://127.0.0.1:8765/mcp"}})["office"]
    assert local["url"] == "http://127.0.0.1:8765/mcp" and "command" not in local
    assert validate_mcp_servers({"o": {"url": "http://localhost:9/mcp"}})["o"]["url"] == "http://localhost:9/mcp"
    for bad, message in [
        ({"a b": {"command": "x"}}, "letters"),
        ({"fin": {"url": "https://example.com/mcp"}}, "127.0.0.1 or localhost"),
        ({"fin": {"url": "http://user:pw@127.0.0.1:1/mcp"}}, "without credentials"),
        ({"fin": {"url": "file:///etc/passwd"}}, "127.0.0.1 or localhost"),
        ({"fin": {"command": "x", "url": "http://127.0.0.1:1/"}}, "either command"),
        ({"fin": {}}, "either command"),
        ({"fin": {"command": "x", "read_only_tools": ["get_*"]}}, "exact tool names"),
        ({"fin": {"command": "x", "headers": {"Authorization": "x"}}}, "unknown fields"),
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
def test_first_search_waits_for_a_connecting_deferred_server(tmp_path, repeating_model):  # noqa: F811 - imported fixture
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", hardware=False)
    server["env"]["MCP_FIXTURE_INITIALIZE_DELAY_MS"] = "750"
    state["sequence"] = [
        ("tool_search", {"query": "stock quote"}),
        ("mcp__fin__quote", {"symbol": "2330"}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: False)
    finally:
        chat.close()
    searches = [d for n, d in events if n == "tool" and d["phase"] == "finished" and d["tool"] == "tool_search"]
    assert len(searches) == 1
    assert "mcp__fin__quote" in searches[0]["result"]["details"]["found"]
    assert _ran(log) == ["quote"]
    assert not any(n == "approval" for n, _ in events)
    declarations = [[t["function"]["name"] for t in r.get("tools", [])] for r in state["requests"]]
    assert "mcp__fin__quote" not in declarations[0]
    assert "mcp__fin__quote" in declarations[1]


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
                                            mode="interview", mcp_servers={"fin": server}):
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


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_the_owner_can_vouch_for_unmarked_tools_but_not_against_the_server(tmp_path, repeating_model):  # noqa: F811
    port, state = repeating_model
    server, log = _server(tmp_path, permission="allow", read_only_tools=["note", "place_order"])
    state["sequence"] = [
        ("tool_search", {"query": "journal note"}),
        ("mcp__fin__note", {"text": "watch 2330"}),
        ("tool_search", {"query": "place stock order"}),
        ("mcp__fin__place_order", {"symbol": "2330", "shares": 1}),
    ]
    chat = NativeChat(model_port=port)
    try:
        events = _run(chat, tmp_path, {"fin": server}, approve=lambda data: False)
    finally:
        chat.close()
    approvals = [data["tool"] for name, data in events if name == "approval"]
    # note has no hints: vouched, it runs freely; place_order says it is not read-only: still asks
    assert approvals == ["mcp__fin__place_order"]
    assert _ran(log) == ["note"]
