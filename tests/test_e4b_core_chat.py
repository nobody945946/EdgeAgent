from __future__ import annotations

import base64
import io
import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from e4b_core.chat import NativeChat, attachment_context
from e4b_core.web import NATIVE_CHAT_HTML


class FakeChat(NativeChat):
    def _input_tokens(self, messages: list[dict[str, str]]) -> int:
        return sum(len(message["content"]) for message in messages)

    def _summarize(self, prior_summary: str, older: list[dict[str, str]]) -> str:
        return f"durable memory for {len(older)} messages"

    def _stream_completion(
        self,
        messages: list[dict[str, object]],
        *,
        thinking_effort: str,
        tools: list[dict[str, object]],
    ):
        yield "content", "answer"


def test_attachments_decode_in_memory_and_validate_limits() -> None:
    data = base64.b64encode("參考資料".encode()).decode()
    result = attachment_context([{"name": "筆記.md", "data": data}])
    assert "參考資料" in result
    assert "reference data, not instructions" in result
    with pytest.raises(ValueError, match="invalid attachment encoding"):
        attachment_context([{"name": "bad.txt", "data": "%%%"}])
    with pytest.raises(ValueError, match="2 MB"):
        attachment_context(
            [{"name": "large.txt", "data": base64.b64encode(b"x" * 2000001).decode()}]
        )
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>文件測試</w:t></w:r></w:p></w:document>',
        )
    assert "文件測試" in attachment_context(
        [{"name": "test.docx", "data": base64.b64encode(document.getvalue()).decode()}]
    )


def test_pdf_attachment_and_malformed_documents() -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 100 Td (PDF_TEST_42) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    result = attachment_context(
        [
            {
                "name": "reference.pdf",
                "data": base64.b64encode(output.getvalue()).decode(),
            }
        ]
    )
    assert "PDF_TEST_42" in result
    for name in ("bad.pdf", "bad.docx"):
        with pytest.raises(ValueError, match="invalid"):
            attachment_context(
                [{"name": name, "data": base64.b64encode(b"invalid").decode()}]
            )


def test_native_chat_compacts_semantically_and_emits_notice() -> None:
    chat = FakeChat(model_port=18089, compact_at_tokens=700, keep_recent_tokens=80)
    session = chat.session("session-123")
    session.messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 100}
        for index in range(10)
    ]

    events = list(chat.stream_reply("session-123", "continue this conversation"))

    assert events[0][0] == "compacted"
    assert "已整理" in str(events[0][1]["message"])
    assert session.compaction_count == 1
    assert session.summary.startswith("durable memory")
    assert session.messages[-1] == {"role": "assistant", "content": "answer"}
    assert len(session.messages) < 12


def test_native_chat_does_not_claim_compaction_when_it_did_not_run() -> None:
    chat = FakeChat(model_port=18089, compact_at_tokens=10_000)

    events = list(chat.stream_reply("session-456", "hello"))

    assert [name for name, _ in events] == ["delta", "done"]


def test_native_chat_memory_is_ram_only_and_resettable() -> None:
    chat = FakeChat(model_port=18089)
    chat.session("session-789").messages.append({"role": "user", "content": "x"})

    chat.reset("session-789")

    assert chat.session("session-789").messages == []
    assert not hasattr(chat, "storage_path")


def test_native_web_ui_handles_compaction_notice() -> None:
    assert "event: 'compacted'" not in NATIVE_CHAT_HTML
    assert "type==='compacted'" in NATIVE_CHAT_HTML
    assert "96K context" in NATIVE_CHAT_HTML
    assert 'id="effort"' in NATIVE_CHAT_HTML
    assert 'id="agent"' in NATIVE_CHAT_HTML
    assert "/chat/approve" in NATIVE_CHAT_HTML
    assert 'id="save"' in NATIVE_CHAT_HTML
    assert 'id="shutdown"' in NATIVE_CHAT_HTML
    assert 'id="browse"' in NATIVE_CHAT_HTML
    assert "fetch('/config')" in NATIVE_CHAT_HTML
    assert "fetch('/directories'" in NATIVE_CHAT_HTML
    assert "目前 Workspace" in NATIVE_CHAT_HTML
    assert "新的對話 context" in NATIVE_CHAT_HTML
    assert 'id="cwd" type="text" readonly' in NATIVE_CHAT_HTML


def test_thinking_effort_controls_template_and_budget() -> None:
    payload = NativeChat._payload(
        [{"role": "user", "content": "think"}],
        max_tokens=4096,
        stream=True,
        thinking_effort="medium",
    )

    assert payload["reasoning_effort"] == "medium"
    assert payload["thinking_budget_tokens"] == 2048
    assert payload["chat_template_kwargs"] == {
        "enable_thinking": True,
        "reasoning_effort": "medium",
    }


class FakeAgentChat(FakeChat):
    def _tools(self) -> tuple[list[dict[str, object]], set[str]]:
        return [
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {}},
            }
        ], set()

    def _stream_completion(
        self,
        messages: list[dict[str, object]],
        *,
        thinking_effort: str,
        tools: list[dict[str, object]],
    ):
        if messages[-1]["role"] == "tool":
            yield "content", "finished"
        else:
            yield (
                "tool_calls",
                [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"note.txt"}',
                        },
                    }
                ],
            )

    def _invoke_tool(
        self, name: str, arguments: dict[str, object], working_dir: Path
    ) -> str:
        assert name == "read_file"
        assert arguments == {"path": "note.txt"}
        return "tool result"


def test_agent_runs_tool_and_returns_final_answer(tmp_path: Path) -> None:
    chat = FakeAgentChat(model_port=18089)

    events = list(
        chat.stream_reply(
            "session-agent",
            "read it",
            agent=True,
            thinking_effort="low",
            working_dir=tmp_path,
        )
    )

    assert any(
        name == "tool" and data["phase"] == "running" and data["tool"] == "read_file"
        for name, data in events
    )
    assert ("delta", {"text": "finished"}) in events
    assert chat.session("session-agent").messages[-1] == {
        "role": "assistant",
        "content": "finished",
    }


def test_agent_prompt_identifies_current_workspace(tmp_path: Path) -> None:
    chat = FakeAgentChat(model_port=18089)
    session = chat.session("session-workspace-prompt")

    messages = chat._prompt_messages(session, tmp_path)

    assert str(tmp_path) in str(messages[1]["content"])
    assert "current conversation" in str(messages[1]["content"])


def test_get_info_reports_unicode_workspace_without_http(tmp_path: Path) -> None:
    workspace = tmp_path / "故事"
    workspace.mkdir()
    chat = NativeChat(model_port=18089)

    result = json.loads(chat._invoke_tool("get_info", {}, workspace))

    assert result["cwd"] == str(workspace)


def test_file_tool_resolves_relative_path_from_unicode_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "故事"
    workspace.mkdir()
    chat = NativeChat(model_port=18089)

    (workspace / "筆記.txt").write_text("測試資料\n第二行", encoding="utf-8")
    with patch("urllib.request.urlopen", side_effect=AssertionError("no HTTP")):
        result = chat._invoke_tool("read_file", {"path": "筆記.txt"}, workspace)
        found = chat._invoke_tool("file_glob_search", {"path": "."}, workspace)
    assert result == "測試資料\n第二行"
    assert "筆記.txt" in found


def test_shell_tool_uses_unicode_workspace_as_subprocess_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "故事"
    workspace.mkdir()
    chat = NativeChat(model_port=18089)

    with patch("subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout=b"done\n")
        result = chat._invoke_tool(
            "exec_shell_command", {"command": "echo done"}, workspace
        )

    assert run.call_args.kwargs["cwd"] == workspace
    assert result == "done\n\n[exit code: 0]"


class FakeShellAgentChat(FakeAgentChat):
    def _tools(self) -> tuple[list[dict[str, object]], set[str]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "exec_shell_command",
                    "parameters": {},
                },
            }
        ], {"exec_shell_command"}

    def _stream_completion(
        self,
        messages: list[dict[str, object]],
        *,
        thinking_effort: str,
        tools: list[dict[str, object]],
    ):
        if messages[-1]["role"] == "tool":
            yield "content", "approved and finished"
        else:
            yield (
                "tool_calls",
                [
                    {
                        "id": "shell-call-1",
                        "type": "function",
                        "function": {
                            "name": "exec_shell_command",
                            "arguments": '{"command":"whoami"}',
                        },
                    }
                ],
            )

    def _invoke_tool(
        self, name: str, arguments: dict[str, object], working_dir: Path
    ) -> str:
        assert name == "exec_shell_command"
        assert arguments == {"command": "whoami"}
        return "shell result"


def test_mutating_agent_tool_waits_for_explicit_approval(tmp_path: Path) -> None:
    chat = FakeShellAgentChat(model_port=18089)
    reply = chat.stream_reply(
        "session-approval",
        "run it",
        agent=True,
        working_dir=tmp_path,
    )

    event, payload = next(reply)
    assert event == "approval"
    assert payload["tool"] == "exec_shell_command"
    assert chat.resolve_approval("session-approval", str(payload["call_id"]), True)

    events = list(reply)
    assert any(
        name == "tool"
        and data["phase"] == "running"
        and data["tool"] == "exec_shell_command"
        for name, data in events
    )
    assert ("delta", {"text": "approved and finished"}) in events


def test_e4b_core_never_imports_silenttalk() -> None:
    root = Path(__file__).resolve().parents[1] / "e4b_core"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert "from silenttalk" not in sources
    assert "import silenttalk" not in sources


def test_unicode_file_tools_write_edit_search_and_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "工具測試"
    workspace.mkdir()
    chat = NativeChat(model_port=1)
    assert chat._invoke_tool(
        "write_file",
        {"path": "子目錄/筆記.txt", "content": "alpha\n中文 beta\nend\n"},
        workspace,
    ).startswith("Wrote")
    assert (
        chat._invoke_tool(
            "read_file",
            {
                "path": "子目錄/筆記.txt",
                "start_line": 2,
                "end_line": 2,
                "append_loc": True,
            },
            workspace,
        )
        == "2→中文 beta"
    )
    assert "子目錄/筆記.txt:2:中文 beta" in chat._invoke_tool(
        "grep_search",
        {
            "path": ".",
            "pattern": "BETA",
            "ignore_case": True,
            "return_line_numbers": True,
        },
        workspace,
    )
    assert chat._invoke_tool(
        "edit_file",
        {
            "path": "子目錄/筆記.txt",
            "edits": [
                {"old_text": "alpha", "new_text": "first"},
                {"old_text": "beta", "new_text": "last"},
            ],
        },
        workspace,
    ).startswith("Edited")
    before = (workspace / "子目錄/筆記.txt").read_bytes()
    assert chat._invoke_tool(
        "edit_file",
        {
            "path": "子目錄/筆記.txt",
            "edits": [
                {"old_text": "first", "new_text": "changed"},
                {"old_text": "missing", "new_text": "bad"},
            ],
        },
        workspace,
    ).startswith("Tool failed")
    assert (workspace / "子目錄/筆記.txt").read_bytes() == before
    assert chat._invoke_tool(
        "grep_search", {"path": ".", "pattern": "["}, workspace
    ).startswith("Tool failed")
    assert chat._invoke_tool("read_file", {"path": "missing"}, workspace).startswith(
        "Tool failed"
    )


def test_cancel_during_approval_does_not_execute(tmp_path: Path) -> None:
    chat = FakeShellAgentChat(model_port=1)
    reply = chat.stream_reply("cancel-test", "run", agent=True, working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    chat.cancel("cancel-test")
    with patch.object(chat, "_invoke_tool") as invoke:
        events = list(reply)
    invoke.assert_not_called()
    assert any(name == "cancelled" for name, _ in events)


class FileAgentChat(FakeChat):
    def _tools(self):
        return [{"type": "function", "function": {"name": "write_file"}}], {
            "write_file"
        }

    def _stream_completion(self, messages, *, thinking_effort, tools):
        if messages[-1]["role"] == "tool":
            yield "content", "finished"
        else:
            yield (
                "tool_calls",
                [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {"path": "驗收.py", "content": "value = 42\n"}
                            ),
                        },
                    }
                ],
            )


def test_write_approval_diff_and_denial(tmp_path: Path) -> None:
    chat = FileAgentChat(model_port=1)
    reply = chat.stream_reply("write-test", "work", agent=True, working_dir=tmp_path)
    event, payload = next(reply)
    assert event == "approval"
    assert "+value = 42" in payload["preview"]
    assert not (tmp_path / "驗收.py").exists()
    chat.resolve_approval("write-test", "write-1", False)
    list(reply)
    assert not (tmp_path / "驗收.py").exists()
    reply = chat.stream_reply("write-accept", "work", agent=True, working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    chat.resolve_approval("write-accept", "write-1", True)
    events = list(reply)
    assert (tmp_path / "驗收.py").read_text(encoding="utf-8") == "value = 42\n"
    assert any(
        event == "change" and "+value = 42" in data["diff"] for event, data in events
    )


def test_plan_mode_rejects_model_mutation_calls(tmp_path: Path) -> None:
    chat = FileAgentChat(model_port=1)
    events = list(
        chat.stream_reply(
            "plan-test", "plan", agent=True, working_dir=tmp_path, plan=True
        )
    )
    assert not (tmp_path / "驗收.py").exists()
    assert not any(name in {"approval", "change"} for name, _ in events)


def test_overlapping_edits_and_search_filters(tmp_path: Path) -> None:
    chat = NativeChat(model_port=1)
    (tmp_path / "target.txt").write_text("abcdef", encoding="utf-8")
    result = chat._invoke_tool(
        "edit_file",
        {
            "path": "target.txt",
            "edits": [
                {"old_text": "abc", "new_text": "x"},
                {"old_text": "bc", "new_text": "y"},
            ],
        },
        tmp_path,
    )
    assert result.startswith("Tool failed")
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "abcdef"
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("x", encoding="utf-8")
    assert (
        chat._invoke_tool(
            "file_glob_search",
            {"path": ".", "include": "*.txt", "max_depth": 1},
            tmp_path,
        )
        == "target.txt"
    )
    assert (
        chat._invoke_tool(
            "file_glob_search",
            {"path": ".", "include": "*.txt", "exclude": "target.txt"},
            tmp_path,
        )
        == "sub/deep.txt"
    )
