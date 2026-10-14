"""The service's launcher and stop (api.py) are woken by the service itself:
its "listening" line when launched here, its status stream for readiness,
and its process's exit for a stop. Nothing asks /status or the port at an
interval. A fake service stands in (tests/fixtures/fake_service.py)."""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from edge_agent import api, processes
from edge_agent.runtime import EdgeAgentError

FAKE = Path(__file__).parent / "fixtures" / "fake_service.py"
ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def service(monkeypatch):
    """The launcher starts the fake service on a free port; any it left
    running is ended by its exact PID afterwards."""
    port = free_port()
    monkeypatch.setattr(api, "CONTROL_PORT", port)
    monkeypatch.setattr(api, "service_command", lambda: [sys.executable, str(FAKE), str(port), str(ROOT)])
    yield port
    status = api.service_status()
    if status is not None and processes.pid_alive(int(status["pid"])):
        os.kill(int(status["pid"]), signal.SIGTERM)


def counts(port: int) -> dict[str, int]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/test/counts", timeout=5) as r:
        return json.load(r)


def test_a_launched_service_is_waited_for_on_its_own_line_and_stream(service, monkeypatch):
    monkeypatch.setenv("FAKE_READY_AFTER", "1.5")
    begun = time.monotonic()
    status = api.ensure_service(timeout=60)
    assert status["ready"] is True and status["mode"] == "base"
    assert 1.5 <= time.monotonic() - begun < 30
    asked = counts(service)
    # Ready came on the stream: /status was never asked (asking every
    # 0.25 s through 1.5 s would be six times or more).
    assert asked.get("/status", 0) == 0
    assert asked["/status/events"] == 1

    pid = int(status["pid"])
    stopping = time.monotonic()
    assert api.shutdown_service() == {"mode": "stopped", "ready": False}
    # Back once the process has exited, woken by the exit itself.
    assert not processes.pid_alive(pid)
    assert time.monotonic() - stopping < 5


def test_without_ready_the_first_status_is_enough(service):
    status = api.ensure_service(timeout=60, require_ready=False)
    assert status["ready"] is False and status["mode"] == "starting"


def test_a_service_that_exits_before_listening_is_told_at_once(service, monkeypatch):
    monkeypatch.setenv("FAKE_SERVICE", "dies")
    begun = time.monotonic()
    with pytest.raises(EdgeAgentError, match=r"exited \(2\) before it was listening"):
        api.ensure_service(timeout=60)
    assert time.monotonic() - begun < 20


def test_a_status_error_before_ready_is_raised(service, monkeypatch):
    monkeypatch.setenv("FAKE_SERVICE", "errors")
    with pytest.raises(EdgeAgentError, match="missing model"):
        api.ensure_service(timeout=60)
