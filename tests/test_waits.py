"""The kernel waits (waits.py) wake on the thing they wait for: a process's
exit, a Wake given by another thread, a child's line, a stream's close. None
of them sleeps to look again (time.sleep is made to fail)."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from edge_agent import processes, waits
from edge_agent.waits import EventStream, OutputLines, ProcessExit, Wake

WAITING = [sys.executable, "-c", "import sys; sys.stdin.read()"]


@pytest.fixture
def no_sleeping(monkeypatch):
    def refuse(seconds):
        raise AssertionError(f"slept {seconds} s to look again")

    monkeypatch.setattr(waits.time, "sleep", refuse)


@pytest.fixture
def child():
    process = subprocess.Popen(WAITING, stdin=subprocess.PIPE)
    yield process
    if process.poll() is None:
        process.kill()
    process.wait()


def end_later(process, seconds=0.3):
    """The child exits `seconds` from now (its stdin closes)."""
    timer = threading.Timer(seconds, process.stdin.close)
    timer.start()
    return time.monotonic() + seconds


def test_a_process_exit_ends_the_wait_when_it_happens(child, no_sleeping):
    exit_ = ProcessExit.open(child.pid)
    assert exit_ is not None
    with exit_:
        assert not exit_.exited()
        assert not exit_.wait(0.2)  # alive: the timeout ends it
        at = end_later(child)
        assert exit_.wait(10)
        assert -0.05 < time.monotonic() - at < 2
        assert exit_.exited()


def test_pid_alive_is_the_same_exit_looked_at_once(child):
    assert processes.pid_alive(child.pid)
    child.stdin.close()
    with ProcessExit.open(child.pid) as exit_:
        assert exit_.wait(10)
        # Exited but not yet reaped by its parent: gone, as the monitor sees it.
        assert not processes.pid_alive(child.pid)
    child.wait()
    assert not processes.pid_alive(child.pid)
    # Gone (Linux, once reaped), or kept by a handle that still says it exited
    # (Windows: the Popen's own handle).
    after = ProcessExit.open(child.pid)
    assert after is None or after.exited()
    assert not processes.pid_alive(0)


def test_a_wake_ends_the_wait_from_another_thread(no_sleeping):
    wake = Wake()
    threading.Timer(0.3, wake.set).start()
    begun = time.monotonic()
    wake.wait(None, 10)
    assert 0.25 < time.monotonic() - begun < 2
    # Given while nobody waits: the next wait ends at once, and takes it.
    wake.set()
    begun = time.monotonic()
    wake.wait(None, 10)
    assert time.monotonic() - begun < 0.5
    begun = time.monotonic()
    wake.wait(None, 0.3)
    assert time.monotonic() - begun >= 0.25
    wake.close()


def test_a_wake_also_ends_on_the_process_it_watches(child, no_sleeping):
    wake = Wake()
    with ProcessExit.open(child.pid) as exit_:
        at = end_later(child)
        wake.wait(exit_, 10)
        assert time.monotonic() - at < 2
        assert exit_.exited()
    wake.close()


def test_output_lines_wake_at_the_wanted_line_and_at_the_end(no_sleeping):
    script = (
        "import sys, time\n"
        "print('starting', flush=True)\n"
        "time.sleep(0.3)\n"
        "print('listening on here', flush=True)\n"
        "for n in range(20000): print('chatter', n)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    lines = OutputLines(process.stdout, lambda line: b"listening on" in line, name="test-lines")
    begun = time.monotonic()
    assert lines.wait(10)
    assert 0.25 < time.monotonic() - begun < 5
    assert process.wait() == 0  # drained past the line: never stalls on a full pipe
    lines._settled.wait(10)
    assert lines.ended

    process = subprocess.Popen([sys.executable, "-c", "print('no')"], stdout=subprocess.PIPE)
    lines = OutputLines(process.stdout, lambda line: b"listening on" in line, name="test-lines")
    assert not lines.wait(10)
    assert lines.ended
    process.wait()


class Streaming(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: status\ndata: {\"n\": 1}\n\n")
        self.wfile.flush()
        self.server.release.wait(10)


@pytest.fixture
def streaming():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Streaming)
    server.daemon_threads = True
    server.release = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.release.set()
    server.shutdown()
    server.server_close()


def test_an_event_stream_gives_data_and_close_ends_a_blocked_read(streaming):
    stream = EventStream(streaming.server_port, "/status/events", timeout=5)
    events = stream.events()
    assert next(events) == b'{"n": 1}'
    ended = threading.Event()

    def read_on():
        try:
            for _ in events:
                pass
        except OSError:
            pass
        ended.set()

    threading.Thread(target=read_on, daemon=True).start()
    assert not ended.wait(0.3)  # blocked: nothing more comes
    stream.close()
    assert ended.wait(5)


def test_an_event_stream_gives_up_at_its_deadline(streaming):
    stream = EventStream(streaming.server_port, "/status/events", timeout=5)
    events = stream.events(time.monotonic() + 0.3)
    assert next(events) == b'{"n": 1}'
    with pytest.raises(TimeoutError):
        next(events)
    stream.close()


def test_an_event_stream_refuses_an_error_answer():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)  # 501 to GET
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ConnectionError, match="501"):
            EventStream(server.server_port, "/status/events", timeout=5)
    finally:
        server.shutdown()
        server.server_close()
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with pytest.raises(OSError):
        EventStream(port, "/status/events", timeout=5)
