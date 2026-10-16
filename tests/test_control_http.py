"""The control server's HTTP boundary (control.py)."""

from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from edge_agent import control
from edge_agent.config import DEFAULT_CONFIG
from edge_agent.inference import InferenceBusy


@pytest.mark.parametrize(
    "exception", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError]
)
def test_http_disconnect_ends_connection_without_server_traceback(
    monkeypatch, exception
):
    def disconnected(handler):
        raise exception("fixture")

    monkeypatch.setattr(control.BaseHTTPRequestHandler, "handle", disconnected)
    handler = object.__new__(control.Handler)
    handler.handle()
    assert handler.close_connection is True


def test_http_handler_keeps_unexpected_failures_visible(monkeypatch):
    def failed(handler):
        raise RuntimeError("fixture")

    monkeypatch.setattr(control.BaseHTTPRequestHandler, "handle", failed)
    with pytest.raises(RuntimeError, match="fixture"):
        object.__new__(control.Handler).handle()


def _chat_error(core) -> dict[str, object]:
    """The error event /chat streams to a client when the Agent cannot take it."""
    handler = type("Handler", (control.Handler,), {"core": core})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/chat",
            data=json.dumps({"session_id": "s" * 8, "text": "查一下", "agent": False}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            lines = response.read().decode().splitlines()
    finally:
        server.shutdown()
        server.server_close()
    assert lines[0] == "event: error"
    return json.loads(lines[1].removeprefix("data: "))


def _core(**overrides):
    @contextmanager
    def agent_turn():
        yield

    core = SimpleNamespace(
        config=dict(DEFAULT_CONFIG),
        capabilities={"audio": False, "vision": False},
        lock=threading.Lock(),
        status=lambda: {"ready": True},
        agent_slot=object(),
        chat_in_progress=False,
        changes=SimpleNamespace(publish=lambda: None),
        inference=SimpleNamespace(access=SimpleNamespace(agent_turn=agent_turn)),
    )
    for name, value in overrides.items():
        setattr(core, name, value)
    return core


def test_a_chat_the_agent_cannot_take_ends_with_a_code_a_client_can_keep():
    """SilentTalk keeps an error's code, never its words (they can name this
    computer's paths): each way /chat turns a task away carries one."""
    assert _chat_error(_core(agent_slot=None)) == {
        "message": "EdgeAgent is currently leased by SilentTalk", "reason": "agent_unavailable"}
    assert _chat_error(_core(chat_in_progress=True)) == {
        "message": "Agent 工作正在進行；完成或停止後再派工", "reason": "agent_busy"}

    @contextmanager
    def busy():
        raise InferenceBusy("模型正在載入")
        yield

    core = _core()
    core.inference.access.agent_turn = busy
    assert _chat_error(core) == {"message": "模型正在載入", "reason": "inference_busy"}
    assert core.chat_in_progress is False
