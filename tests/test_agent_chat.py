"""NativeChat's RAM sessions and its Pi bridge: checkpoints, records,
cancellation and closing. Also the helpers the chat tests share: a recorded
bridge (RPCFixture), a scripted model server (repeating_model) and the
pinned Pi runtime check."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from edge_agent.bridge import PiBridge
from edge_agent.chat import NativeChat


class RecordedBridge(PiBridge):
    """Recorded protocol boundary; it performs no inference or agent execution."""

    def __init__(self, core_dir, events):
        super().__init__(core_dir)
        self.records = []
        self.fixture_events = events

    def send(self, record):
        self.records.append(record)
        if record.get("message") == "/edge-snapshot":
            entries = [{"type": "session", "id": "fixture"}] + [
                {"type": "message", "message": message}
                for message in self.active.messages
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

    def close(self):
        self.process = None
        self.active = None


class RPCFixture(NativeChat):
    """NativeChat over the recorded bridge."""

    def __init__(self, events):
        super().__init__(model_port=18089)
        self.bridge = RecordedBridge(self.bridge.core_dir, events)
        self.records = self.bridge.records

    def _start(self, session, cwd, effort, agent, read_only, public, permissions, mcp_servers):
        self.started_permissions = permissions
        self.bridge.active = session
        self.bridge.process = SimpleNamespace(poll=lambda: None)


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


def test_sessions_are_kept_only_in_the_selected_records_folder(tmp_path):
    """A finished turn is written whole to the selected folder, and only when
    the folder holds the setup marker (records.py)."""
    from edge_agent import records
    work, drive = tmp_path / "work", tmp_path / "drive"
    work.mkdir()
    drive.mkdir()
    reply = [{"type": "message_update",
              "assistantMessageEvent": {"type": "text_delta", "delta": "fixture"}},
             {"type": "agent_end"}, *ending()]
    chat = RPCFixture(reply)
    list(chat.stream_reply("kept-1", "fixture", working_dir=work, record_folder=str(drive)))
    assert not (drive / "edgeagent").exists()  # no marker: not selected for records
    (drive / ".agent-records").write_text("")
    list(chat.stream_reply("kept-1", "fixture", working_dir=work, record_folder=str(drive)))
    saved = (drive / "edgeagent" / "kept-1.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["type"] for line in saved] == [e["type"] for e in chat.session("kept-1").entries]
    list(chat.stream_reply("kept-2", "fixture", working_dir=work))  # recording off
    assert not (drive / "edgeagent" / "kept-2.jsonl").exists()
    assert list(work.iterdir()) == []
    (drive / records.MARKER).unlink()
    list(chat.stream_reply("kept-3", "fixture", working_dir=work, record_folder=str(drive)))
    assert not (drive / "edgeagent" / "kept-3.jsonl").exists()


def test_control_response_does_not_drop_early_agent_events(monkeypatch):
    chat = NativeChat(model_port=1)

    def send(record):
        chat.bridge._events.put(
            {"type": "message_end", "message": {"role": "user", "content": []}}
        )
        chat.bridge._events.put(
            {
                "type": "response",
                "id": record["id"],
                "success": False,
                "error": "fixture",
            }
        )

    monkeypatch.setattr(chat.bridge, "send", send)
    with pytest.raises(RuntimeError, match="fixture"):
        chat.bridge.request({"type": "get_state"})
    assert chat.bridge.receive()["type"] == "message_end"


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
    assert chat.bridge.process is None
    assert chat.session("fixture").messages[0]["role"] == "toolResult"


def test_memory_reset_discards_only_target_thread():
    chat = NativeChat(model_port=1)
    chat.session("one").entries.append({"type": "fixture"})
    chat.session("two").entries.append({"type": "fixture"})
    chat.reset("one")
    assert chat.session("one").entries == []
    assert chat.session("two").entries == [{"type": "fixture"}]


ASK = {"edit": "ask", "shell": "ask", "web": "allow"}


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
    # The owner is asked (permission "ask"); under "reversible" the host would answer a write itself.
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path, permissions=ASK)
    assert next(reply)[0] == "approval"
    reply.close()
    assert any(record.get("value") == "Deny" for record in chat.records)
    assert chat.bridge.process is None


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
    # The owner is asked (permission "ask"); under "reversible" the host would answer a write itself.
    reply = chat.stream_reply("fixture", "fixture", working_dir=tmp_path, permissions=ASK)
    assert next(reply)[0] == "approval"
    chat.cancel("fixture")
    chat.session("fixture").cancelled_at = 0
    monkeypatch.setattr(
        chat.bridge, "receive", lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    )
    assert list(reply) == [("cancelled", {})]
    assert chat.bridge.process is None


@pytest.fixture
def repeating_model():
    import threading
    import time
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
            if self.path.startswith("/slots/0?"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"n_erased":1}')
                return
            state.setdefault("requests", []).append(request)
            state["calls"] += 1
            time.sleep(state.get("delays", {}).get(state["calls"], 0))
            override = state.get("response_for", lambda *_: {})(request, state["calls"])
            if override.get("status"):
                self.send_response(override["status"])
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(override["body"]).encode())
                return
            # A "sequence" of (tool, input) pairs is played once, then answered.
            sequence = state.get("sequence")
            if sequence is not None and state["calls"] > len(sequence):
                state["answer"] = True
            tool, arguments = (
                sequence[min(state["calls"], len(sequence)) - 1]
                if sequence
                else (state["tool"], state["input"])
            )
            tool = override.get("tool", tool)
            arguments = override.get("input", arguments)
            call = {
                "index": 0,
                "id": str(state["calls"]),
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments)},
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = (
                [
                    ({"role": "assistant", "tool_calls": [call]}, None),
                    ({}, "tool_calls"),
                ]
                if "text" not in override and (override or not state.get("answer"))
                and state["calls"] < state.get("answer_after", float("inf"))
                else [({"role": "assistant", "content": override.get("text", state.get("text", "fixture"))}, None), ({}, state.get("finish_reasons", {}).get(state["calls"], "stop"))]
            )
            if state.get("thought"):
                chunks.insert(0, ({"role": "assistant", "reasoning_content": state["thought"]}, None))
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


def permission_run(chat, tmp_path, rules, approve=None, prompt="make the file"):
    events = []
    for name, data in chat.stream_reply(
        "rules", prompt, agent=True, working_dir=tmp_path, permissions=rules
    ):
        events.append((name, data))
        if name == "approval":
            chat.resolve_approval("rules", data["call_id"], approve is True, "once")
    return events


def test_ram_retrieval_contract():
    import shutil
    import subprocess

    from edge_agent.runtime import CORE_ROOT, NODE_VERSION

    pinned = CORE_ROOT / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
    node = str(pinned) if pinned.is_file() else shutil.which("node")
    if node is None:
        pytest.skip("requires Node")
    subprocess.run([node, str(CORE_ROOT / "tests/memory_check.mjs")], check=True)


def test_a_cancel_wakes_a_turn_waiting_on_a_silent_agent(tmp_path, monkeypatch):
    """A turn waits on Pi's next event with no timer; cancel() wakes it, and a
    Pi that stays silent then has CANCEL_GRACE_SECONDS to settle."""
    import threading
    import time

    from edge_agent import chat as chat_module

    monkeypatch.setattr(chat_module, "CANCEL_GRACE_SECONDS", 0.5)
    chat = RPCFixture([])  # Pi says nothing after the prompt
    timeouts = []
    receive = chat.bridge.receive

    def counted(timeout=120):
        timeouts.append(timeout)
        return receive(timeout)

    monkeypatch.setattr(chat.bridge, "receive", counted)
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(chat.stream_reply("fixture", "fixture", working_dir=tmp_path))
    )
    worker.start()
    time.sleep(0.5)
    cancelled = time.monotonic()
    chat.cancel("fixture")
    worker.join(10)
    assert events[-1] == ("cancelled", {})
    assert 0.4 <= time.monotonic() - cancelled < 3
    # Waited without a timeout until the cancel, then for the grace: asking
    # every second would be a timeout of 1 each time.
    assert timeouts[0] is None and len(timeouts) <= 3
