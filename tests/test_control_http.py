"""The control server's HTTP boundary (control.py)."""

from __future__ import annotations

import pytest

from edge_agent import control


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
