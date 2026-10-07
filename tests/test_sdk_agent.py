"""The real Pi SDK as the Agent: its own prompt, the date, the modes, the
tool catalog, the host's checks on its answers, and the browser UI."""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from edge_agent.chat import NativeChat
from tests.test_agent_chat import (  # noqa: F401 - fixture
    installed_pi,
    permission_run,
    repeating_model,
)


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_stops_repeated_noop_without_writing_or_disk_sessions(
    tmp_path, repeating_model  # noqa: F811 - imported fixture
):
    port, state = repeating_model
    (tmp_path / "code.py").write_text("value=1\n")
    chat = NativeChat(model_port=port)
    try:
        events = list(
            chat.stream_reply("fixture", "fixture", agent=True, working_dir=tmp_path)
        )
        assert events[-1][0] == "error"
        assert "3" in events[-1][1]["message"]
        assert state["calls"] <= 3
        assert (tmp_path / "code.py").read_text() == "value=1\n"
        assert chat.session("fixture").entries
        assert not list(tmp_path.rglob("*.jsonl"))
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_tells_the_model_todays_date(tmp_path, repeating_model):  # noqa: F811
    """The host states the date and time zone, so the model never assumes one
    from its training data."""
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    try:
        permission_run(chat, tmp_path, {"edit": "ask", "shell": "ask", "web": "deny"}, prompt="今天幾號？")
    finally:
        chat.close()
    now = datetime.now().astimezone()
    system = json.dumps(state["requests"][0]["messages"][0], ensure_ascii=False)
    assert state["requests"][0]["messages"][0]["role"] == "system"
    assert f"Today is {now:%Y-%m-%d} ({now:%A}), time zone " in system


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_interview_opens_grill_me_once_and_cannot_change_files(
    tmp_path, repeating_model  # noqa: F811
):
    """求知儀式: the first message opens the bundled grill-me skill, the answers
    that follow go as they are, the tools are read-only, and the skill never
    enters the system prompt (it would change every request's cached prefix)."""
    port, state = repeating_model
    state["sequence"] = []  # every request is answered at once
    chat = NativeChat(model_port=port)
    try:
        for text, mode in [
            ("我想做一個記帳 app", {"mode": "interview"}),
            ("1 用網頁", {"mode": "interview"}),
            ("照規格做", {}),
            ("再來一個新點子", {"mode": "interview"}),
        ]:
            events = list(chat.stream_reply(
                "spec", text, agent=True, working_dir=tmp_path, **mode
            ))
            assert events[-1][0] == "done"
    finally:
        chat.close()
    requests = state["requests"]
    assert len(requests) == 4
    SKILL_MARK = "你現在要先訪談使用者"  # once in the skill

    def last_user(request):
        user = [m for m in request["messages"] if m["role"] == "user"][-1]
        return json.dumps(user["content"], ensure_ascii=False)

    def tools(request):
        return {tool["function"]["name"] for tool in request.get("tools", [])}

    opened = last_user(requests[0])
    assert SKILL_MARK in opened and "我想做一個記帳 app" in opened
    assert "1 用網頁" in last_user(requests[1])
    assert SKILL_MARK not in last_user(requests[1])
    assert json.dumps(requests[1]["messages"], ensure_ascii=False).count(SKILL_MARK) == 1
    assert SKILL_MARK in last_user(requests[3])
    for request in (requests[0], requests[1], requests[3]):
        assert {"read", "grep"} <= tools(request)
        assert not tools(request) & {"edit", "write", "powershell"}
    assert "write" in tools(requests[2])
    for request in requests:
        system = json.dumps(request["messages"][0], ensure_ascii=False)
        assert "grill-me" not in system and "available_skills" not in system


@pytest.mark.skipif(
    os.name != "nt" or not installed_pi() or os.environ.get("EDGE_AGENT_UI_REGRESSION") != "1",
    reason="opt-in real Edge and pinned Pi UI regression",
)
def test_browser_exports_conversations_and_displays_native_tool_errors(
    tmp_path, repeating_model, monkeypatch  # noqa: F811
):
    import subprocess
    import threading

    from edge_agent.control import Handler
    from edge_agent.core import EdgeAgent
    from edge_agent.runtime import CORE_ROOT, NODE_VERSION
    from edge_agent.service import ExclusiveHTTPServer
    from tests.windows_check import launch_edge

    port, state = repeating_model
    state.update(
        sequence=[
            ("write", {"path": "created.txt", "content": "UI_FILE_42\n"}),
            ("read", {"path": "not-here.txt"}),
        ],
        text="# UI_MARKDOWN\n繁體第一段\n\n```text\nline_a\nline_b\n```",
        finish_reasons={4: "length"},
        delays={4: 1.5},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for args in [
        ["init", "-b", "copilot-story-mode"],
        ["config", "user.name", "UI Fixture"],
        ["config", "user.email", "fixture@example.invalid"],
    ]:
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    (workspace / "probe.py").write_text("fixture_value = 42\n")
    subprocess.run(["git", "-C", str(workspace), "add", "probe.py"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-m", "UI fixture"], check=True, capture_output=True)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    core = EdgeAgent(core_dir)
    core.chat.close()
    core.chat = NativeChat(model_port=port)
    core.active_model = "synthetic UI fixture"
    core.agent_slot = 0
    # Only model readiness is synthetic; HTTP, SDK, tools and files are real.
    monkeypatch.setattr(core, "status", lambda: {"ready": True})
    handler = type("UIFixtureHandler", (Handler,), {"core": core})
    server = ExclusiveHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    edge = None
    try:
        edge = launch_edge(tmp_path / "edge-profile")
        environment = {
            **os.environ,
            "EDGE_AGENT_BROWSER_URL": f"http://127.0.0.1:{server.server_port}",
            "EDGE_AGENT_TEST_WORKSPACE": str(workspace),
            "EDGE_AGENT_TEST_DOWNLOADS": str(downloads),
        }
        subprocess.run(
            [
                str(CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"),
                "tests/browser_check.mjs",
                "--ui-regression",
            ],
            cwd=CORE_ROOT,
            env=environment,
            check=True,
            timeout=120,
        )
        failed_tools = [
            message
            for request in state["requests"]
            for message in request["messages"]
            if message["role"] == "tool" and "not-here.txt" in json.dumps(message)
        ]
        assert failed_tools
        assert len(list(downloads.glob("*.txt"))) == 3
        assert not list(workspace.glob("*.jsonl"))
        assert not (workspace / "created.txt").exists()
    finally:
        core.chat.close()
        core.terminal.close_all()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        if edge is not None:
            subprocess.run(
                ["taskkill", "/PID", str(edge.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_the_agent_speaks_as_itself_per_scope(tmp_path, repeating_model):  # noqa: F811
    """The Agent's own prompt (edge_agent/prompt.py) replaces Pi's coding
    assistant, and says who asked: the owner's task or a public one."""
    from edge_agent import prompt

    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    try:
        for scope in ("change", "public"):
            state["requests"] = []
            list(chat.stream_reply(scope, "你好", agent=True, working_dir=tmp_path, scope=scope))
            system = state["requests"][0]["messages"][0]["content"]
            assert system.startswith(prompt.system_prompt(scope))
            assert "expert coding assistant" not in system and "Pi documentation" not in system
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_tool_search_finds_the_toolbox_in_chinese(tmp_path, repeating_model):  # noqa: F811
    """A catalogued tool is not declared until tool_search finds it; a Chinese
    request finds it (Pi's own search keeps only ASCII words), and it then runs."""
    port, state = repeating_model
    (tmp_path / "下載").mkdir()
    (tmp_path / "下載" / "a.mp4").write_bytes(bytes(5000))
    (tmp_path / "下載" / "b.txt").write_bytes(bytes(10))
    state["sequence"] = [
        ("tool_search", {"query": "找出 最大 的 檔案"}),
        ("find_large_files", {"folder": "下載", "top": 1}),
    ]
    chat = NativeChat(model_port=port)
    try:
        list(chat.stream_reply("search", "下載資料夾最大的檔案是哪個", agent=True,
                               working_dir=tmp_path, scope="read"))
    finally:
        chat.close()
    declared = [[t["function"]["name"] for t in r.get("tools", [])] for r in state["requests"]]
    assert "find_large_files" not in declared[0] and "find_large_files" in declared[1]
    results = json.dumps([m for m in state["requests"][-1]["messages"] if m["role"] == "tool"],
                         ensure_ascii=False)
    assert "find_large_files" in results and "a.mp4" in results


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_changed_code_without_a_passing_check_is_flagged(tmp_path, repeating_model):  # noqa: F811
    """Two layers for "done means verified": the prompt asks for run_checks,
    and the host itself says so when code changed and no check passed after."""
    port, state = repeating_model
    state["sequence"] = [("write", {"path": "calc.py", "content": "def f():\n    return 1\n"})]
    state["text"] = "完成了"
    chat = NativeChat(model_port=port)
    try:
        events = permission_run(chat, tmp_path, {"edit": "allow", "shell": "allow", "web": "deny"})
        text = "".join(d["text"] for name, d in events if name == "delta")
        assert "未經驗證" in text and "calc.py" in text
        assert events[-1] == ("done", {**events[-1][1], "unverified": ["calc.py"]})
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_calc.py").write_text("from calc import f\n\ndef test_f():\n    assert f() == 1\n")
        state["calls"], state["requests"], state.pop("answer", None)
        state["sequence"] = [("write", {"path": "calc.py", "content": "def f():\n    return 1\n"}),
                             ("run_checks", {})]
        events = permission_run(chat, tmp_path, {"edit": "allow", "shell": "allow", "web": "deny"})
        text = "".join(d["text"] for name, d in events if name == "delta")
        assert "未經驗證" not in text and events[-1][1]["unverified"] == []
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_the_host_checks_cited_numbers_against_the_sources(tmp_path, repeating_model, monkeypatch):  # noqa: F811
    """The program, not the model, checks the numbers an answer cites from
    web_research (the owner's research rules): one that is not in its passage
    is pointed out to whoever reads the answer."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Web(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            body = (f'<div class="result"><a class="result__a" href="{base}/rule">規則</a></div>'
                    if self.path.startswith("/html/") else
                    "<title>管理規則</title><p>無人機重量 250 公克以上要註冊，罰鍰 6000 元。</p>")
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    web = ThreadingHTTPServer(("127.0.0.1", 0), Web)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    monkeypatch.setenv("EDGE_AGENT_SEARCH_URL", f"http://127.0.0.1:{web.server_address[1]}/html/")
    monkeypatch.setenv("EDGE_AGENT_WEB_ALLOW_LOOPBACK", "1")
    port, state = repeating_model
    state["sequence"] = [("web_research", {"question": "無人機罰鍰多少", "queries": ["無人機 罰鍰"]})]
    state["text"] = "沒註冊會罰 9000 元 [1]。"
    chat = NativeChat(model_port=port)
    try:
        events = list(chat.stream_reply("web", "無人機沒註冊罰多少", agent=True, working_dir=tmp_path,
                                        permissions={"edit": "deny", "shell": "deny", "web": "allow"}))
    finally:
        chat.close()
        web.shutdown()
    text = "".join(d["text"] for name, d in events if name == "delta")
    assert "主機核對來源" in text and "9000" in text
    assert events[-1][0] == "done" and events[-1][1]["source_problems"]


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_searching_the_catalog_for_a_job_no_tool_does_stops(tmp_path, repeating_model):  # noqa: F811
    """A model that learned to search first rewords a search for a job no
    tool does (measured: 60 searches for a retry decorator). The answer says
    there is none, and after four fruitless searches the fifth is refused."""
    port, state = repeating_model
    state["sequence"] = [("tool_search", {"query": "retry decorator implementation"})] * 7
    chat = NativeChat(model_port=port)
    try:
        list(chat.stream_reply("search", "寫一個 retry decorator", agent=True, working_dir=tmp_path,
                               permissions={"edit": "allow", "shell": "ask", "web": "deny"}))
    finally:
        chat.close()
    results = [m for m in state["requests"][-1]["messages"] if m["role"] == "tool"]
    texts = [json.dumps(m, ensure_ascii=False) for m in results]
    assert all("has no tool for this" in text or "Nothing new" in text for text in texts[:4])
    assert all("searched 4 times" in text for text in texts[4:])
