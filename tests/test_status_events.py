"""The status stream (GET /status/events, control.py): the status at once,
then one event each time it changes, woken by the change itself."""

from __future__ import annotations

import http.client
import json
import os
import queue
import socket
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from edge_agent import control, core
from edge_agent.inference import InferenceBusy


@contextmanager
def control_server(manager):
    class Handler(control.Handler):
        core = manager

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class StatusEvents:
    """The stream as a client reads it, on a thread of its own."""

    def __init__(self, port):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.connect()
        self.socket = connection.sock
        connection.request("GET", "/status/events")
        response = connection.getresponse()
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")
        self.events = queue.Queue()
        threading.Thread(target=self._read, args=(response,), daemon=True).start()

    def _read(self, response):
        try:
            for line in response:
                if line.startswith(b"data: "):
                    self.events.put(json.loads(line[6:]))
        except OSError:
            pass
        self.events.put(None)

    def next(self, timeout=1.0):
        return self.events.get(timeout=timeout)

    def close(self):
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    value = core.EdgeAgent(tmp_path)
    value.mode = "character"
    value.owner_pid = os.getpid()
    value.active_model = "fixture.gguf"
    value.router.process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: None, wait=lambda *args, **kwargs: 0
    )
    monkeypatch.setattr(value, "_model_ready", lambda: True)
    monkeypatch.setattr(value.router, "request", lambda *args, **kwargs: {"success": True})
    value.inference.access.reset(65536)
    return value


def test_the_stream_sends_the_status_at_once_then_each_change_and_nothing_between(manager):
    computed = []
    status = manager.status

    def counted():
        computed.append(time.monotonic())
        return status()

    manager.status = counted
    access = manager.inference.access
    with control_server(manager) as port:
        events = StatusEvents(port)
        first = events.next()
        assert first["mode"] == "character" and first["owner_pid"] == os.getpid()
        assert first["ready"] is True
        assert first["character_admitted"] is True and first["character_refused"] == 0
        # Nothing changes: nothing is sent, nothing is even looked at again.
        before = len(computed)
        with pytest.raises(queue.Empty):
            events.next(timeout=1.2)
        assert len(computed) == before
        # An Agent task starts: told at once.
        with access.agent_turn():
            changed = time.monotonic()
            assert events.next()["inference_reserved"] is True
            assert time.monotonic() - changed < 0.2
            with access.generation(agent=True, need=60000):
                # The character is turned away: its refusal is an event,
                # and the gateway would not take it now.
                with pytest.raises(InferenceBusy), access.generation(agent=False, need=8000):
                    pass
                seen = events.next()
                while seen["character_refused"] == 0:
                    seen = events.next()
                assert seen["character_refused"] == 1
                assert seen["character_admitted"] is False
        # The task ends: the character is admitted again, told at once.
        freed = time.monotonic()
        seen = events.next()
        while not seen["character_admitted"]:
            seen = events.next()
        assert time.monotonic() - freed < 0.2
        assert seen["inference_reserved"] is False
        # A core change (here the service stopping) is told too.
        manager.mode = "base"
        manager.stop_service()
        seen = events.next()
        while seen["mode"] != "stopped":
            seen = events.next()
        assert seen["ready"] is False
        events.close()


def test_a_client_that_leaves_ends_its_stream(manager):
    def streams():
        return [t for t in threading.enumerate() if t.name == "status-events-client"]

    with control_server(manager) as port:
        events = StatusEvents(port)
        assert events.next()["mode"] == "character"
        assert len(streams()) == 1
        events.close()
        for thread in streams():
            thread.join(2)
        assert streams() == []
