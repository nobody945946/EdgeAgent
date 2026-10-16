"""One turn as the UI sees it (turn.py, approvals.py): Pi's events and the
host's notices in UI events, approvals, modes and how a turn ends."""

from __future__ import annotations

import json

import pytest

from tests.test_agent_chat import RPCFixture, ending


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("length", "error"),
        ("error", "error"),
        ("aborted", "cancelled"),
        ("toolUse", "error"),
    ],
)
def test_incomplete_finish_retains_completed_messages(reason, expected, tmp_path):
    chat = RPCFixture(ending(reason))
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert events[-1][0] == expected
    assert not any(name == "done" for name, _ in events)
    assert len(chat.session("fixture").entries) == 2


def test_truncation_reports_actual_usage_and_keeps_it_distinct_from_context(tmp_path):
    events = ending("length")
    events[0]["message"]["usage"] = {"input": 2500, "output": 6000}
    events.insert(
        0,
        {
            "type": "extension_ui_request",
            "method": "notify",
            "message": 'edge-budget:{"max_output_tokens":6000}',
        },
    )
    # A summary after the assistant must not overwrite that reply's wire cap.
    events.insert(
        -1,
        {
            "type": "extension_ui_request",
            "method": "notify",
            "message": 'edge-budget:{"max_output_tokens":4000}',
        },
    )
    chat = RPCFixture(events)
    chat.configure_context(32768, max_output_tokens=8192)
    result = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1]
    assert result[0] == "error"
    assert result[1]["context_size"] == 32768
    assert result[1]["max_output_tokens"] == 8192
    assert result[1]["request_max_output_tokens"] == 6000
    assert result[1]["usage"]["output"] == 6000
    assert "32768" in result[1]["message"]
    assert "8192" in result[1]["message"]


def test_an_uncapped_agent_step_that_fills_the_context_says_so(tmp_path):
    # The Agent's requests go without a cap (agent_bridge.mjs drops it): the
    # context ended the step, and the output setting is not the advice.
    events = ending("length")
    events[0]["message"]["usage"] = {"input": 3534, "output": 61733}
    events.insert(
        0, {"type": "extension_ui_request", "method": "notify", "message": "edge-budget:{}"}
    )
    chat = RPCFixture(events)
    chat.configure_context(65536, max_output_tokens=8192)
    result = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1]
    assert result[0] == "error"
    assert result[1]["reason"] == "generation_truncated"
    assert result[1]["request_max_output_tokens"] is None
    assert "65536" in result[1]["message"] and "61733" in result[1]["message"]
    assert "8192" not in result[1]["message"]
    assert "單次輸出" not in result[1]["message"]


def test_mutating_tool_diff_is_retained_when_model_runs_out_of_tokens(tmp_path):
    path = tmp_path / "code.py"
    path.write_text("value=1\n")
    chat = RPCFixture(
        [
            {
                "type": "tool_execution_start",
                "toolName": "edit",
                "toolCallId": "call",
                "args": {"path": "code.py"},
            },
            {
                "type": "tool_execution_end",
                "toolName": "edit",
                "toolCallId": "call",
                "isError": False,
            },
            *ending("length"),
        ]
    )
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path)
    assert next(reply)[0] == "tool"
    path.write_text("value=2\n")
    events = list(reply)
    assert any(name == "change" and "+value=2" in data["diff"] for name, data in events)
    assert events[-1][0] == "error"
    assert chat.session("fixture").changes[str(path)] == ("value=1\n", "value=2\n")


def test_cancel_resolves_pending_approval_before_aborting(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "extension_ui_request",
                "method": "select",
                "id": "approval",
                "title": json.dumps(
                    {"tool": "powershell", "arguments": {"command": "fixture"}}
                ),
            },
            *ending("aborted"),
        ]
    )
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    chat.cancel("fixture")
    deny = next(i for i, r in enumerate(chat.records) if r.get("value") == "Deny")
    abort = next(i for i, r in enumerate(chat.records) if r.get("type") == "abort")
    assert deny < abort
    assert not chat.resolve_approval("fixture", "approval", True)
    assert list(reply)[-1][0] == "cancelled"


def test_turn_approval_is_explicit_and_sent_to_upstream(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "extension_ui_request",
                "method": "select",
                "id": "approval",
                "title": json.dumps(
                    {"tool": "write", "arguments": {"path": "code.py"}}
                ),
            },
            *ending(),
        ]
    )
    # The owner is asked (permission "ask"); under "reversible" the host would answer a write itself.
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path,
                              permissions={"edit": "ask", "shell": "ask", "web": "allow"})
    assert next(reply)[0] == "approval"
    assert not chat.resolve_approval("other", "approval", True, "turn")
    assert chat.resolve_approval("fixture", "approval", True, "turn")
    assert chat.records[-1]["value"] == "Allow this turn"
    assert list(reply)[-1][0] == "done"


def test_handled_command_finishes_without_agent_settled(tmp_path):
    chat = RPCFixture(
        [{"type": "response", "success": True, "data": {"disposition": "handled"}}]
    )
    assert (
        list(chat.stream_reply("fixture", "/fixture", working_dir=tmp_path))[-1][0]
        == "done"
    )
    with pytest.raises(ValueError, match="reserved"):
        list(chat.stream_reply("fixture", "/edge-snapshot", working_dir=tmp_path))


def test_compaction_events_precede_prompt_response_and_preserve_messages(tmp_path):
    chat = RPCFixture(
        [
            {"type": "compaction_end", "result": {"summary": "fixture"}},
            {"type": "response", "success": True, "data": {"disposition": "started"}},
            *ending(),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert [name for name, _ in events] == ["compacted", "done"]


def test_successful_retry_replaces_temporary_failure(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": "temporary fixture failure",
                },
            },
            {"type": "auto_retry_start"},
            *ending(),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert events[-1][0] == "done"


def test_progress_stop_survives_aborted_assistant(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "extension_ui_request",
                "method": "notify",
                "message": 'edge-stop:{"code": "tool_limit", "message": "fixture limit"}',
            },
            *ending("aborted"),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert events[-1] == ("error", {"message": "fixture limit", "reason": "tool_limit"})


def test_approval_explains_a_powershell_command(tmp_path, monkeypatch):
    """The approval carries PowerShell's own reading of the command; a
    translator failure leaves only a note and the approval still goes on."""
    seen = {}

    class Fake:
        def text(self):
            return "影響：會刪除：x"

    def translate(command, cwd=None, timeout=0):
        seen.update(command=command, cwd=cwd)
        return Fake()

    monkeypatch.setattr("edge_agent.approvals.lingua_powershell.translate", translate)
    request = {"type": "extension_ui_request", "method": "select", "id": "approval",
               "title": json.dumps({"tool": "powershell", "arguments": {"command": "Remove-Item x"}})}
    chat = RPCFixture([request, *ending()])
    name, data = next(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert name == "approval" and data["explain"] == "影響：會刪除：x"
    assert data["explain_title"].startswith("這個指令在做什麼")
    assert seen == {"command": "Remove-Item x", "cwd": str(tmp_path.resolve())}

    def broken(command, cwd=None, timeout=0):
        raise RuntimeError("找不到 PowerShell")

    monkeypatch.setattr("edge_agent.approvals.lingua_powershell.translate", broken)
    chat = RPCFixture([request, *ending()])
    name, data = next(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert name == "approval" and "explain" not in data and "找不到 PowerShell" in data["explain_error"]
    write = {**request, "title": json.dumps({"tool": "write", "arguments": {"path": "a"}})}
    chat = RPCFixture([write, *ending()])
    name, data = next(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert "explain" not in data and "explain_error" not in data


def test_discussion_opens_its_skill_once_and_keeps_work_tools(tmp_path):
    """推演儀式: the first message of the stretch opens the discussion skill;
    the next ones go as they are; an unknown mode is refused."""
    chat = RPCFixture([*ending(), *ending()])
    list(chat.stream_reply("talk", "主管說大家都因為錢離職", working_dir=tmp_path, mode="discussion"))
    list(chat.stream_reply("talk", "那你覺得呢", working_dir=tmp_path, mode="discussion"))
    prompts = [r["message"] for r in chat.records
               if r.get("type") == "prompt" and not r["message"].startswith("/edge-snapshot")]
    assert prompts[0].startswith('<skill name="discussion">') and "討論守則" in prompts[0]
    assert prompts[0].endswith("主管說大家都因為錢離職")
    assert prompts[1] == "那你覺得呢"
    with pytest.raises(ValueError, match="invalid mode"):
        list(chat.stream_reply("talk", "x", working_dir=tmp_path, mode="plan"))


def test_simplified_that_slips_through_is_shown_in_taiwan_traditional():
    """The output's last gate: Simplified text is converted, Traditional
    text is left exactly as it is (s2twp would rewrite 通過 as 透過)."""
    from edge_agent.turn import taiwan_traditional

    pytest.importorskip("opencc")
    assert taiwan_traditional("这个软件已经安装好了") == "這個軟體已經安裝好了"
    assert taiwan_traditional("測試通過，檔案已存好") == "測試通過，檔案已存好"


def test_failed_turns_carry_a_stable_code_a_client_can_log() -> None:
    from edge_agent.chat import transport_failure
    from edge_agent.turn import model_failure

    assert model_failure("423 inference_busy") == "inference_busy"
    assert model_failure("the request exceeds the available context size") == "context_overflow"
    assert model_failure("fetch failed: ECONNREFUSED") == "model_unreachable"
    assert model_failure("HTTP 500 from server") == "model_http_error"
    assert model_failure("") == "model_error"
    assert transport_failure(TimeoutError()) == "bridge_timeout"
    assert transport_failure(RuntimeError("Pi rejected this task")) == "bridge_error"
    assert transport_failure(ValueError("bad")) == "invalid_request"
