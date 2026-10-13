"""bench/serve_wait.py: a llama-server is ready when it says it is listening
(no /health asked at an interval); one that exits while loading is reported
at once with its last lines, and its whole output reaches server.log. A fake
server stands in (no model, no GPU)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import serve_wait

FAKE = """
import sys, time
mode = sys.argv[1]
say = lambda line: print(line, file=sys.stderr, flush=True)
say("0.00.000.355 I srv  llama_server: initializing ...")
say("0.00.055.014 I srv    load_model: loading model 'fake.gguf'")
if mode == "dies":
    say("0.00.100.000 E srv    load_model: failed to load model 'fake.gguf'")
    sys.exit(3)
if mode == "hangs":
    time.sleep(600)
time.sleep(0.2)
say("0.02.486.788 I srv  llama_server: model loaded")
say("0.02.486.790 I srv  llama_server: listening on http://127.0.0.1:18189")
if mode == "chatty":
    for task in range(20000):  # far more than a pipe holds
        print(f"0.03.000.000 I slot print_timing: id  1 | task {task} | total time = 1.0 ms")
    sys.exit(0)
time.sleep(600)
"""


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    path = tmp_path / "fake_server.py"
    path.write_text(FAKE, encoding="utf-8")
    return path


def test_ready_once_the_server_says_it_is_listening_and_the_log_keeps_its_lines(fake, tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"an earlier run\n")
    server = serve_wait.LlamaServer([sys.executable, str(fake), "listens"], log, timeout=60)
    assert server.process.poll() is None
    server.stop()
    text = log.read_text(encoding="utf-8")
    assert text.startswith("an earlier run\n")  # appended, as before
    assert "llama_server: listening on http://127.0.0.1:18189" in text


def test_the_output_after_ready_keeps_being_read(fake, tmp_path):
    log = tmp_path / "server.log"
    server = serve_wait.LlamaServer([sys.executable, str(fake), "chatty"], log, timeout=60)
    # Undrained, the fake would block on a full pipe and never exit.
    assert server.process.wait(timeout=60) == 0
    server.stop()
    assert "task 19999 |" in log.read_text(encoding="utf-8")


def test_a_server_that_exits_while_loading_is_reported_at_once_with_its_last_lines(fake, tmp_path):
    started = time.monotonic()
    with pytest.raises(serve_wait.NotListening) as failed:
        serve_wait.LlamaServer([sys.executable, str(fake), "dies"], tmp_path / "server.log", timeout=300)
    assert time.monotonic() - started < 60
    assert failed.value.exited
    assert "exited (3) before it was listening" in str(failed.value)
    assert "failed to load model 'fake.gguf'" in str(failed.value)


def _bench_server(monkeypatch, fake: Path, free_mib: list[int]):
    import run

    free = iter(free_mib)
    monkeypatch.setattr(run, "free_vram_mib", lambda: next(free))
    monkeypatch.setattr(run, "build_server_args", lambda *_a, **_k: [
        sys.executable, str(fake), "dies", "--parallel", "1", "--ctx-size", "1"])
    return run


def test_the_bench_hands_the_queue_back_when_the_gpu_filled_during_the_load(fake, tmp_path, monkeypatch):
    run = _bench_server(monkeypatch, fake, [30000, 100])  # room at the check, gone at the exit
    config = {"vram_base_mib": 1000, "vram_slot_mib": 100, "model": "x.gguf", "context_size": 1}
    with pytest.raises(SystemExit) as stopped:
        run.Server(config, tmp_path, 1, 18999)
    assert stopped.value.code == run.VRAM_EXIT


def test_the_bench_stops_with_the_reason_when_the_server_exits_with_room_left(fake, tmp_path, monkeypatch):
    run = _bench_server(monkeypatch, fake, [30000, 30000])
    config = {"vram_base_mib": 1000, "vram_slot_mib": 100, "model": "x.gguf", "context_size": 1}
    with pytest.raises(serve_wait.NotListening) as stopped:
        run.Server(config, tmp_path, 1, 18999)
    assert "failed to load model 'fake.gguf'" in str(stopped.value)
    assert "failed to load model 'fake.gguf'" in (tmp_path / "server.log").read_text(encoding="utf-8")


def test_a_server_not_listening_by_the_deadline_is_stopped(fake, tmp_path):
    with pytest.raises(serve_wait.NotListening) as failed:
        serve_wait.LlamaServer([sys.executable, str(fake), "hangs"], tmp_path / "server.log", timeout=1)
    assert not failed.value.exited
    assert "did not become ready in 1 s (stopped it)" in str(failed.value)
