"""A terminal command waits for its output, the output's end, or close() (a
wake item in the output queue), with one deadline; it never looks again at
an interval. A fake shell stands in for PowerShell, so this runs everywhere
(tests/test_terminal.py runs the real one on Windows)."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time

import pytest

from edge_agent import terminal
from edge_agent.terminal import TerminalSession

# Answers each command the way TerminalSession's script makes PowerShell
# answer: its output, then the marker line with the exit code and the
# folder. A command "silent" gets nothing back.
FAKE_SHELL = r"""
import base64, os, re, sys
for line in sys.stdin:
    marker = re.search(r"EDGE_AGENT_DONE_[0-9a-f]+", line)
    command = re.search(r"FromBase64String\('([^']*)'\)", line)
    if not marker or not command:
        continue
    command = base64.b64decode(command.group(1)).decode()
    if command == "silent":
        continue
    print("output of " + command, flush=True)
    print(marker.group(0) + ":0:" + base64.b64encode(os.getcwd().encode()).decode(), flush=True)
"""


class Counted(queue.Queue):
    def __init__(self) -> None:
        super().__init__()
        self.gets = 0

    def get(self, *args, **kwargs):
        self.gets += 1
        return super().get(*args, **kwargs)


@pytest.fixture
def session(tmp_path, monkeypatch):
    real = subprocess.Popen

    def popen(args, *rest, **options):
        if args and args[0] == "powershell.exe":
            args = [sys.executable, "-c", FAKE_SHELL]
        return real(args, *rest, **options)

    monkeypatch.setattr(terminal.subprocess, "Popen", popen)
    value = TerminalSession(tmp_path)
    value.lines = Counted()
    yield value
    value.close()


def test_a_command_ends_on_its_own_output(session, tmp_path):
    events = list(session.run("Get-Thing"))
    assert events[0] == ("delta", {"text": "output of Get-Thing\n"})
    assert events[-1] == ("done", {"exit_code": 0, "cwd": str(tmp_path)})


def test_close_wakes_a_waiting_command_at_once(session):
    events = []
    worker = threading.Thread(target=lambda: events.extend(session.run("silent")))
    worker.start()
    time.sleep(0.5)
    closed = time.monotonic()
    session.close()
    worker.join(10)
    assert not worker.is_alive()
    assert time.monotonic() - closed < 5
    assert events[-1][0] == "cancelled"
    # One wait, woken by close(): looking every 0.1 s would be five or more.
    assert session.lines.gets <= 2


def test_a_silent_command_ends_at_its_deadline(session):
    begun = time.monotonic()
    events = list(session.run("silent", timeout=1))
    assert 0.9 <= time.monotonic() - begun < 10
    assert events == [("error", {"message": "terminal timed out after 1s"})]
    assert session.lines.gets == 1
