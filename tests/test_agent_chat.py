from __future__ import annotations

import base64
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from edge_agent.chat import NativeChat, attachment_context, decode_attachments
from edge_agent.web import NATIVE_CHAT_HTML


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


def test_native_web_ui_handles_compaction_notice() -> None:
    assert "event: 'compacted'" not in NATIVE_CHAT_HTML
    assert "type==='compacted'" in NATIVE_CHAT_HTML
    assert 'id="active-model-label"' in NATIVE_CHAT_HTML
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


class RPCFixture(NativeChat):
    """Recorded protocol boundary; it performs no inference or agent execution."""

    def __init__(self, events):
        super().__init__(model_port=18089)
        self.records = []
        self.fixture_events = events

    def _start(self, session, cwd, effort, agent, plan):
        self._active = session
        self._process = SimpleNamespace(poll=lambda: None)

    def _send(self, record):
        self.records.append(record)
        if record.get("message") == "/edge-snapshot":
            entries = [{"type": "session", "id": "fixture"}] + [
                {"type": "message", "message": message}
                for message in self._active.messages
            ]
            self._events.put(
                {
                    "type": "extension_ui_request",
                    "method": "notify",
                    "message": "edge-snapshot:" + json.dumps(entries),
                }
            )
            self._events.put({"type": "response", "id": record["id"], "success": True})
        elif record.get("type") == "prompt":
            for event in self.fixture_events:
                self._events.put(
                    {
                        **event,
                        **(
                            {"id": record["id"]}
                            if event.get("type") == "response"
                            else {}
                        ),
                    }
                )

    def _close(self):
        self._process = None
        self._active = None


def ending(reason="stop"):
    return [
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [], "stopReason": reason},
        },
        {"type": "agent_settled"},
    ]


def test_success_is_reported_only_after_settlement_and_ram_checkpoint(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "fixture"},
            },
            {"type": "agent_end"},
            *ending(),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert [name for name, _ in events] == ["delta", "done"]
    assert chat.session("fixture").entries[1]["message"]["stopReason"] == "stop"
    assert chat.session("fixture").messages == []
    assert list(tmp_path.iterdir()) == []


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
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    assert not chat.resolve_approval("other", "approval", True, "turn")
    assert chat.resolve_approval("fixture", "approval", True, "turn")
    assert chat.records[-1]["value"] == "Allow this turn"
    assert list(reply)[-1][0] == "done"


def test_control_response_does_not_drop_early_agent_events(monkeypatch):
    chat = NativeChat(model_port=1)

    def send(record):
        chat._events.put(
            {"type": "message_end", "message": {"role": "user", "content": []}}
        )
        chat._events.put(
            {
                "type": "response",
                "id": record["id"],
                "success": False,
                "error": "fixture",
            }
        )

    monkeypatch.setattr(chat, "_send", send)
    with pytest.raises(RuntimeError, match="fixture"):
        chat._request({"type": "get_state"})
    assert chat._receive()["type"] == "message_end"


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
            {"type": "auto_compaction_end", "result": {"summary": "fixture"}},
            {"type": "response", "success": True, "data": {"disposition": "started"}},
            *ending(),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert [name for name, _ in events] == ["compacted", "done"]


def test_rpc_failure_closes_agent_without_deleting_ram_history(tmp_path):
    chat = RPCFixture(
        [
            {"type": "message_end", "message": {"role": "toolResult", "content": []}},
            {"type": "protocol_error"},
        ]
    )
    assert (
        list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
        == "error"
    )
    assert chat._process is None
    assert chat.session("fixture").messages[0]["role"] == "toolResult"


def test_memory_reset_discards_only_target_thread():
    chat = NativeChat(model_port=1)
    chat.session("one").entries.append({"type": "fixture"})
    chat.session("two").entries.append({"type": "fixture"})
    chat.reset("one")
    assert chat.session("one").entries == []
    assert chat.session("two").entries == [{"type": "fixture"}]


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
                "message": "edge-stop:fixture limit",
            },
            *ending("aborted"),
        ]
    )
    events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    assert events[-1] == ("error", {"message": "fixture limit"})


def test_disconnected_ui_denies_pending_operation_and_closes_agent(tmp_path):
    chat = RPCFixture(
        [
            {
                "type": "extension_ui_request",
                "method": "select",
                "id": "approval",
                "title": json.dumps(
                    {"tool": "write", "arguments": {"path": "code.py"}}
                ),
            }
        ]
    )
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    reply.close()
    assert any(record.get("value") == "Deny" for record in chat.records)
    assert chat._process is None


def test_cancelled_silent_transport_cannot_hold_service_forever(tmp_path, monkeypatch):
    chat = RPCFixture(
        [
            {
                "type": "extension_ui_request",
                "method": "select",
                "id": "approval",
                "title": json.dumps(
                    {"tool": "write", "arguments": {"path": "code.py"}}
                ),
            }
        ]
    )
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path)
    assert next(reply)[0] == "approval"
    chat.cancel("fixture")
    chat.session("fixture").cancelled_at = 0
    monkeypatch.setattr(
        chat, "_receive", lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    )
    assert list(reply) == [("cancelled", {})]
    assert chat._process is None


@pytest.fixture
def repeating_model():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {
        "calls": 0,
        "tool": "edit",
        "input": {
            "path": "code.py",
            "edits": [{"oldText": "value=1", "newText": "value=1"}],
        },
    }

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.setdefault("requests", []).append(request)
            state["calls"] += 1
            call = {
                "index": 0,
                "id": str(state["calls"]),
                "type": "function",
                "function": {
                    "name": state["tool"],
                    "arguments": json.dumps(state["input"]),
                },
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = (
                [
                    ({"role": "assistant", "tool_calls": [call]}, None),
                    ({}, "tool_calls"),
                ]
                if not state.get("answer")
                else [({"role": "assistant", "content": "fixture"}, None), ({}, "stop")]
            )
            for delta, stop in chunks:
                record = {
                    "id": "fixture",
                    "model": "local",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": stop}],
                }
                self.wfile.write(("data: " + json.dumps(record) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, state
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def installed_pi():
    from edge_agent.runtime import CORE_ROOT, NODE_VERSION

    return (
        CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    ).is_file() and (
        CORE_ROOT
        / ".runtime/pi/node_modules/@earendil-works/pi-coding-agent/dist/index.js"
    ).is_file()


def media_fixture():
    import struct
    import wave
    import zlib

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 1600)
    return [
        {"name": name, "data": base64.b64encode(data).decode()}
        for name, data in [("fixture.png", png), ("fixture.wav", audio.getvalue())]
    ]


def test_media_validation_preserves_native_bytes_and_rejects_unsupported_inputs():
    files = media_fixture()
    context, media = decode_attachments(files, {"vision": True, "audio": True})
    assert context == ""
    assert [item["mimeType"] for item in media] == ["image/png", "audio/wav"]
    assert [item["data"] for item in media] == [item["data"] for item in files]
    for capability, index in [("vision", 0), ("audio", 1)]:
        with pytest.raises(ValueError, match="不支援"):
            decode_attachments([files[index]], {capability: False})
    with pytest.raises(ValueError, match="格式不符"):
        decode_attachments(
            [{"name": "invalid.wav", "data": base64.b64encode(b"invalid").decode()}],
            {"audio": True},
        )
    with pytest.raises(ValueError, match="20 MB"):
        decode_attachments(
            [{"name": "large.wav", "data": base64.b64encode(b"x" * 20000001).decode()}],
            {"audio": True},
        )


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
@pytest.mark.parametrize("indices", [(0, 1), (1,)])
def test_real_sdk_media_reaches_provider_and_ram_history(
    tmp_path, repeating_model, indices
):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    chat.configure_context(16384, "fixture", {"vision": True, "audio": True})
    files = media_fixture()
    _, media = decode_attachments(
        [files[index] for index in indices], chat.capabilities
    )
    try:
        assert (
            list(
                chat.stream_reply(
                    "fixture", "fixture", working_dir=tmp_path, media=media
                )
            )[-1][0]
            == "done"
        )
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        for request in state["requests"]:
            assert request["reasoning_effort"] == "none"
            assert request["chat_template_kwargs"]["enable_thinking"] is False
            assert any(
                isinstance(message.get("content"), list)
                and {"text", "input_audio"}.issubset(
                    {item["type"] for item in message["content"]}
                )
                for message in request["messages"]
            )
            content = [
                item
                for message in request["messages"]
                if isinstance(message.get("content"), list)
                for item in message["content"]
            ]
            assert any(
                item.get("type") == "image_url"
                and item["image_url"]["url"].startswith("data:image/png;base64,")
                for item in content
            ) == (0 in indices)
            assert any(
                item.get("type") == "input_audio"
                and item["input_audio"]["data"] == media[-1]["data"]
                for item in content
            )
            assert not any(
                item.get("type") == "image_url"
                and "data:audio/" in item["image_url"]["url"]
                for item in content
            )
        chat.configure_context(16384, "vision-only", {"vision": True, "audio": False})
        events = list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
        assert events[-1][0] == "error"
        assert "不支援" in events[-1][1]["message"]
        assert len(state["requests"]) == 2
        assert not list(tmp_path.iterdir())
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_refreshes_capabilities_for_same_base(tmp_path, repeating_model):
    port, state = repeating_model
    state["answer"] = True
    chat = NativeChat(model_port=port)
    chat.configure_context(16384, "fixture", {"vision": False, "audio": False})
    try:
        assert (
            list(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))[-1][0]
            == "done"
        )
        previous_pid = chat._process.pid
        chat.configure_context(16384, "fixture", {"vision": True, "audio": True})
        _, media = decode_attachments([media_fixture()[1]], chat.capabilities)
        assert (
            list(
                chat.stream_reply(
                    "fixture", "fixture", working_dir=tmp_path, media=media
                )
            )[-1][0]
            == "done"
        )
        assert chat._process.pid != previous_pid
        assert any(
            item.get("type") == "input_audio"
            for message in state["requests"][-1]["messages"]
            if isinstance(message.get("content"), list)
            for item in message["content"]
        )
    finally:
        chat.close()


@pytest.mark.skipif(not installed_pi(), reason="requires installed pinned Pi runtime")
def test_real_sdk_stops_repeated_noop_without_writing_or_disk_sessions(
    tmp_path, repeating_model
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
def test_real_sdk_cancel_while_waiting_for_approval_does_not_write(
    tmp_path, repeating_model
):
    import time

    port, state = repeating_model
    state.update(tool="write", input={"path": "code.py", "content": "value=2\n"})
    (tmp_path / "code.py").write_text("value=1\n")
    chat = NativeChat(model_port=port)
    try:
        reply = chat.stream_reply(
            "fixture", "fixture", agent=True, working_dir=tmp_path
        )
        for name, payload in reply:
            if name == "approval":
                break
        else:
            pytest.fail("upstream must request approval")
        started = time.monotonic()
        chat.cancel("fixture")
        events = list(reply)
        assert time.monotonic() - started < 10
        assert events[-1][0] == "cancelled"
        assert (tmp_path / "code.py").read_text() == "value=1\n"
    finally:
        chat.close()
