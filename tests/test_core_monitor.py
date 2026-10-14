"""The core's monitor (core.EdgeAgent.monitor) wakes for what it answers for,
never on a timer of its own: a lease owner's exit (a kernel wait on its
process), the router's model events and exit, and status changes; its one
timeout is the next deadline it knows (the grace, a handoff, a retry)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest
from test_router_events import ARGS, counts, use_fake

from edge_agent import core

WAITING = [sys.executable, "-c", "import sys; sys.stdin.read()"]


@pytest.fixture
def owners():
    """Lease owners: real processes that exit when told (their stdin closes)."""
    started = []

    def start():
        process = subprocess.Popen(WAITING, stdin=subprocess.PIPE)
        started.append(process)
        return process

    yield start
    for process in started:
        if process.poll() is None:
            process.kill()
        process.wait()


class Watched:
    """A manager whose monitor runs on a thread; its switches and waits are
    recorded."""

    def __init__(self, manager, monkeypatch, *, refuse=()):
        self.manager = manager
        self.switches: list[tuple[str, int | None, float]] = []
        self.switched = threading.Condition()
        self.waits = 0
        self.refuse = list(refuse)  # exceptions the next switches raise
        wait = manager.wake.wait

        def counted(*args, **kwargs):
            self.waits += 1
            return wait(*args, **kwargs)

        def switch(mode, owner_pid=None, character=None):
            with self.switched:
                self.switches.append((mode, owner_pid, time.monotonic()))
                self.switched.notify_all()
            if self.refuse:
                raise self.refuse.pop(0)
            manager.mode, manager.owner_pid = mode, None
            manager.changes.publish()
            return {}

        monkeypatch.setattr(manager.wake, "wait", counted)
        monkeypatch.setattr(manager, "switch", switch)
        threading.Thread(target=manager.monitor, name="test-monitor", daemon=True).start()

    def switched_to(self, count: int, timeout: float = 10) -> list:
        with self.switched:
            assert self.switched.wait_for(lambda: len(self.switches) >= count, timeout)
            return list(self.switches)


def lease(manager, owner):
    manager.mode, manager.owner_pid = "character", owner.pid


def test_an_owner_that_exits_returns_the_lease_after_the_grace(tmp_path, monkeypatch, owners):
    monkeypatch.setattr(core, "OWNER_GRACE_SECONDS", 0.5)
    manager = core.EdgeAgent(tmp_path)
    owner = owners()
    lease(manager, owner)
    watched = Watched(manager, monkeypatch)
    time.sleep(1.0)
    # Alive and nothing happening: one wait, in the kernel, no looking again.
    assert watched.switches == [] and watched.waits <= 2
    owner.stdin.close()
    owner.wait()
    exited = time.monotonic()
    (mode, pid, at), = watched.switched_to(1)
    assert (mode, pid) == ("base", owner.pid)
    assert 0.4 <= at - exited < 2


def test_a_handoff_holds_the_lease_past_the_grace(tmp_path, monkeypatch, owners):
    monkeypatch.setattr(core, "OWNER_GRACE_SECONDS", 0.2)
    manager = core.EdgeAgent(tmp_path)
    owner = owners()
    lease(manager, owner)
    manager.hold_handoff(1.5)
    held_until = manager.handoff_until
    watched = Watched(manager, monkeypatch)
    owner.stdin.close()
    owner.wait()
    (_, _, at), = watched.switched_to(1)
    assert held_until - 0.05 <= at < held_until + 1


def test_a_new_lease_rearms_the_watch_on_its_owner(tmp_path, monkeypatch, owners):
    monkeypatch.setattr(core, "OWNER_GRACE_SECONDS", 0.2)
    manager = core.EdgeAgent(tmp_path)
    first, second = owners(), owners()
    lease(manager, first)
    watched = Watched(manager, monkeypatch)
    time.sleep(0.3)
    lease(manager, second)  # SilentTalk restarted and took the lease over
    manager.changes.publish()
    first.stdin.close()
    first.wait()
    time.sleep(1.0)
    assert watched.switches == []  # the old owner's exit means nothing now
    second.stdin.close()
    second.wait()
    (mode, pid, _), = watched.switched_to(1)
    assert (mode, pid) == ("base", second.pid)


def test_an_agent_task_holds_the_return_until_it_ends(tmp_path, monkeypatch, owners):
    monkeypatch.setattr(core, "OWNER_GRACE_SECONDS", 0.1)
    manager = core.EdgeAgent(tmp_path)
    owner = owners()
    lease(manager, owner)
    watched = Watched(manager, monkeypatch, refuse=[PermissionError("工作正在進行")])
    owner.stdin.close()
    owner.wait()
    watched.switched_to(1)
    time.sleep(1.0)
    assert len(watched.switches) == 1  # not tried again until something changes
    ended = time.monotonic()
    manager.changes.publish()  # the task ended (control._chat publishes it)
    _, (mode, _, again) = watched.switched_to(2)
    assert mode == "base" and again - ended < 1


def test_a_failed_return_to_base_is_tried_again_after_the_retry_time(
    tmp_path, monkeypatch, owners
):
    monkeypatch.setattr(core, "OWNER_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(core, "RETRY_SECONDS", 0.8)
    manager = core.EdgeAgent(tmp_path)
    owner = owners()
    lease(manager, owner)
    watched = Watched(manager, monkeypatch, refuse=[RuntimeError("out of memory")])
    owner.stdin.close()
    owner.wait()
    (_, _, first), (_, _, again) = watched.switched_to(2)
    assert 0.7 <= again - first < 3


@pytest.fixture
def routed(tmp_path, monkeypatch):
    """A manager whose router is the fake one, loaded, its monitor running;
    reloads are recorded."""
    manager = core.EdgeAgent(tmp_path)
    use_fake(manager.router, monkeypatch)
    manager.router.load(ARGS)
    manager.mode, manager.active_model = "base", "fixture.gguf"
    reloads = []
    reloaded = threading.Event()
    failures = []

    def start_child(mode, owner_pid, character):
        reloads.append((mode, owner_pid, character, time.monotonic()))
        if failures:
            raise failures.pop(0)
        manager.router.load(ARGS)
        reloaded.set()

    monkeypatch.setattr(manager, "_start_child", start_child)
    threading.Thread(target=manager.monitor, name="test-monitor", daemon=True).start()
    yield manager, reloads, reloaded, failures
    # As the service stops: nothing loaded on purpose, so the monitor does
    # not load it again when the router goes.
    manager.stop_service()
    assert not manager.router.running()


def test_a_worker_crash_is_loaded_again_at_once_and_nothing_asks_before(routed):
    manager, reloads, reloaded, _ = routed
    before = counts(manager.router)
    time.sleep(1.2)
    after = counts(manager.router)
    after["/test/counts"] -= 1
    assert after == before  # the monitor asks the router nothing while all is well
    crashed = time.monotonic()
    manager.router.request("/test/crash", {})
    assert reloaded.wait(5)
    (mode, owner, _, at), = reloads
    assert (mode, owner) == ("base", None)
    assert at - crashed < 1.5
    with manager.lock:  # the reload has ended
        assert manager.error == "" and not manager.loading
    time.sleep(0.5)
    assert len(reloads) == 1  # loaded again: nothing more to do


def test_a_router_that_exits_is_started_again(routed):
    manager, _, reloaded, _ = routed
    old = manager.router.process
    old.kill()
    assert reloaded.wait(10)
    assert manager.router.running() and manager.router.process is not old


def test_a_failed_reload_is_shown_and_tried_again_after_the_retry_time(routed, monkeypatch):
    monkeypatch.setattr(core, "RETRY_SECONDS", 0.8)
    manager, reloads, reloaded, failures = routed
    failures.append(RuntimeError("out of memory"))
    manager.router.request("/test/crash", {})
    assert reloaded.wait(10)
    (_, _, _, first), (_, _, _, again) = reloads
    assert 0.7 <= again - first < 3
    with manager.lock:  # the reload has ended
        assert manager.error == ""
