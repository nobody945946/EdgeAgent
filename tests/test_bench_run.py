"""bench/run.py: a job's sandbox lets the product write what it writes on the
laptop, and a run whose sandbox failed to report does not pass for finished."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import run

from edge_agent import chat


def test_the_public_folder_is_mounted_where_the_product_keeps_it():
    # The sandbox's product lives at /opt/edgeagent (agent_runner's core_dir).
    assert run.PUBLIC_MOUNT == "/opt/edgeagent/" + chat.PUBLIC_DIR.relative_to(chat.CORE_ROOT).as_posix()


class _Server:
    parallel, port, sock_dir = 1, 0, Path("/nonexistent")

    def __init__(self, *_args, **_kwargs):
        self.process = SimpleNamespace(poll=lambda: None)

    def speed(self) -> float:
        return 1.0

    def close(self) -> None:
        pass


def test_a_run_with_a_crashed_job_does_not_finish_and_the_next_start_reruns_it(tmp_path, monkeypatch):
    for name in ("t-00", "t-01"):
        (tmp_path / "tasks" / name).mkdir(parents=True)
        (tmp_path / "tasks" / name / "task.json").write_text("{}")
    product = tmp_path / "product"
    product.mkdir()
    monkeypatch.setattr(run, "STATE", tmp_path / "state")
    monkeypatch.setattr(run, "TASK_ROOT", tmp_path / "tasks")
    monkeypatch.setattr(run, "containment", lambda _run_dir: None)
    monkeypatch.setattr(run, "bridge_variant", lambda *_args: None)
    monkeypatch.setattr(run, "Server", _Server)
    monkeypatch.setattr(run, "close_on_term", lambda *_servers: None)
    outcomes = {"t-00": "done", "t-01": "crash"}
    ran = []

    def job(task_dir, rep, *_args, **_kwargs):
        ran.append(task_dir.name)
        return {"task": task_dir.name, "rep": rep, "wall": 0.4, "outcome": outcomes[task_dir.name],
                "grade": {}, "passed": False}

    monkeypatch.setattr(run, "run_job", job)
    monkeypatch.setattr(sys, "argv", ["run.py", "--config", "e4b-laptop-base", "--tasks", "t-*",
                                      "--repeats", "1", "--parallel", "1", "--tag", "crashy",
                                      "--product", str(product)])
    with pytest.raises(SystemExit, match=r"1 job\(s\) crashed \(t-01 r0\)"):
        run.main()
    outcomes["t-01"] = "done"
    ran.clear()
    run.main()  # finishes: the crash was no result, so only it runs again
    assert ran == ["t-01"]


def test_every_config_is_named_once():
    # json keeps the last of two same-named keys and drops the first without a word.
    def unique(pairs):
        names = [k for k, _ in pairs]
        assert len(names) == len(set(names)), sorted({k for k in names if names.count(k) > 1})
        return dict(pairs)

    json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"), object_pairs_hook=unique)
