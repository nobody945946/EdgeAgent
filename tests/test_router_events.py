"""The model router (router.py) is never asked at an interval: it is ready
when it says it is listening, a load ends on the router's own model event,
and a worker that dies or a router that exits wakes the watcher at once. A
fake router stands in (tests/fixtures/fake_router.py: no model, no GPU)."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from edge_agent import router as router_module
from edge_agent.router import ModelRouter

FAKE = Path(__file__).parent / "fixtures" / "fake_router.py"
ARGS = ["llama-server", "--model", "x.gguf", "--ctx-size", "8192"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Events:
    """The router's on_event calls, waited for by the test."""

    def __init__(self) -> None:
        self.count = 0
        self.changed = threading.Condition()

    def __call__(self) -> None:
        with self.changed:
            self.count += 1
            self.changed.notify_all()

    def after(self, seen: int, timeout: float = 5) -> bool:
        with self.changed:
            return self.changed.wait_for(lambda: self.count > seen, timeout)


def use_fake(router: ModelRouter, monkeypatch, mode: str = "listens",
             load_seconds: float = 0.3) -> ModelRouter:
    """`router` starts the fake router (on a free port) instead of llama-server."""
    monkeypatch.setenv("FAKE_ROUTER", mode)
    monkeypatch.setenv("FAKE_LOAD_SECONDS", str(load_seconds))
    router.port = free_port()
    router._command = lambda preset: [
        sys.executable, str(FAKE), "--port", str(router.port), "--models-preset", str(preset)
    ]
    return router


def fake_router(tmp_path: Path, monkeypatch, **options):
    events = Events()
    return use_fake(ModelRouter(tmp_path, events), monkeypatch, **options), events


def counts(router: ModelRouter) -> dict[str, int]:
    with urllib.request.urlopen(f"http://127.0.0.1:{router.port}/test/counts", timeout=5) as r:
        return json.load(r)


def post(router: ModelRouter, path: str) -> None:
    router.request(path, {})


@pytest.fixture
def started(tmp_path, monkeypatch):
    routers = []

    def start(**options):
        value, events = fake_router(tmp_path, monkeypatch, **options)
        routers.append(value)
        return value, events

    yield start
    for value in routers:
        value.stop()


def test_a_load_waits_for_the_routers_events_not_an_interval(started):
    router, events = started(load_seconds=1.5)
    begun = time.monotonic()
    router.load(ARGS)
    elapsed = time.monotonic() - begun
    assert 1.5 <= elapsed < 10
    asked = counts(router)
    assert "/health" not in asked  # ready from its own "listening on" line
    # Read once to decide, once per event (loading, loaded): asking every
    # 0.1 s through a 1.5 s load would be some 15 reads.
    assert asked["/models"] <= 5
    assert asked["/models/load"] == 1
    assert events.count >= 2
    assert router.capabilities(8192) == {"vision": False, "audio": False}


def test_nothing_is_asked_while_nothing_happens(started):
    router, _ = started()
    router.load(ARGS)
    before = counts(router)
    time.sleep(1.5)
    after = counts(router)
    after["/test/counts"] -= 1
    assert after == before


def test_a_router_that_exits_before_listening_is_told_at_once(started):
    router, _ = started(mode="dies")
    begun = time.monotonic()
    with pytest.raises(RuntimeError, match="model router exited with 3"):
        router.load(ARGS)
    assert time.monotonic() - begun < 10
    assert not router.running()


def test_a_router_that_never_listens_is_stopped_at_the_deadline(started, monkeypatch):
    monkeypatch.setattr(router_module, "ROUTER_START_SECONDS", 1)
    router, _ = started(mode="hangs")
    with pytest.raises(TimeoutError, match="did not become ready"):
        router.load(ARGS)
    assert router.process.poll() is not None  # stopped, not left behind


def test_a_model_that_fails_to_load_is_told_with_its_exit(started):
    router, _ = started(mode="fails")
    with pytest.raises(RuntimeError, match=r"model failed to load \(exit 1\)"):
        router.load(ARGS)


def test_a_worker_that_dies_wakes_the_watcher_at_once(started):
    """2026-10-12: a llama-server worker crashed and left the router up with
    the model unloaded. The router's own event says so."""
    router, events = started()
    router.load(ARGS)
    seen = events.count
    crashed = time.monotonic()
    post(router, "/test/crash")
    assert events.after(seen)
    assert time.monotonic() - crashed < 1
    assert router.state() == {"value": "unloaded", "failed": True, "exit_code": 139}


def test_the_routers_exit_wakes_the_watcher_at_once(started):
    router, events = started()
    router.load(ARGS)
    seen = events.count
    killed = time.monotonic()
    router.process.kill()
    assert events.after(seen)
    assert time.monotonic() - killed < 2
    assert router.process.exited.wait(5)
    assert not router.running()


def test_a_lost_event_stream_is_followed_again(started, monkeypatch):
    monkeypatch.setattr(router_module, "STREAM_GONE_SECONDS", 0.5)
    router, events = started()
    router.load(ARGS)
    post(router, "/test/drop")  # the router keeps running
    seen = events.count
    assert events.after(seen)  # followed again, and told so
    seen = events.count
    post(router, "/test/crash")
    assert events.after(seen)


def test_stop_ends_the_router_and_its_threads(started):
    router, _ = started()
    before = set(threading.enumerate())
    router.load(ARGS)
    process = router.process
    its_threads = [thread for thread in threading.enumerate()
                   if thread not in before and thread.name.startswith("model-router-")]
    assert {thread.name for thread in its_threads} == {
        "model-router-events", "model-router-exit", "model-router-output"}
    router.stop()
    assert process.exited.is_set() and process.poll() is not None
    for thread in its_threads:
        thread.join(10)
        assert not thread.is_alive(), thread.name
