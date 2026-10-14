"""bench/thought_stats.py: each step's thought counted once, cut ones found."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import run
import thought_stats

TOOLS = [{"type": "function", "function": {"name": "read"}}]


def _recording(root: Path, cut: bool) -> Path:
    run_dir = root / "pilot"
    records = run_dir / "records" / "train-A-001-r0"
    records.mkdir(parents=True)
    first = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    step = {"role": "assistant", "content": "", "reasoning_content": "想" * 50 + (run.BUDGET_CUT if cut else "")}
    second = [*first, step, {"role": "tool", "content": "r"}]
    for n, messages in enumerate((first, second)):
        (records / f"{n:04d}.json").write_text(json.dumps({"messages": messages, "tools": TOOLS}), encoding="utf-8")
    (run_dir / "traj").mkdir()
    final = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "答" * 10}, {"type": "text", "text": "好"}]}]
    (run_dir / "traj" / "train-A-001-r0.json").write_text(json.dumps(final), encoding="utf-8")
    result = {"task": "train-A-001", "rep": 0, "kind": "scenario", "wall": 120}
    (run_dir / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
    return run_dir


def test_each_thought_once_and_the_cut_ones(tmp_path: Path) -> None:
    row = thought_stats.stats(_recording(tmp_path, cut=True))["scenario"]
    assert (row["steps"], row["cut"], row["cut_share"], row["median_minutes"]) == (2, 1, 0.5, 2.0)


def test_a_crash_rerun_under_the_same_name_counts_once(tmp_path: Path) -> None:
    run_dir = _recording(tmp_path, cut=True)
    crash = {"task": "train-A-001", "rep": 0, "kind": "scenario", "wall": 0.4, "outcome": "crash"}
    rerun = (run_dir / "results.jsonl").read_text(encoding="utf-8")
    (run_dir / "results.jsonl").write_text(json.dumps(crash) + "\n" + rerun, encoding="utf-8")
    row = thought_stats.stats(run_dir)["scenario"]
    assert (row["jobs"], row["steps"], row["median_minutes"]) == (1, 2, 2.0)


def test_too_many_cut_stops_the_queue(tmp_path: Path, monkeypatch) -> None:
    run_dir = _recording(tmp_path, cut=True)
    monkeypatch.setattr(sys, "argv", ["thought_stats", str(run_dir)])
    with pytest.raises(SystemExit, match="cut more than 2%"):
        thought_stats.main()


def test_nothing_cut_passes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["thought_stats", str(_recording(tmp_path, cut=False))])
    thought_stats.main()


def test_the_base_median_step(tmp_path: Path) -> None:
    (tmp_path / "traj").mkdir()
    steps = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "想" * n}]} for n in (100, 300, 900)]
    (tmp_path / "traj" / "tool-a-r0.json").write_text(json.dumps(steps), encoding="utf-8")
    assert thought_stats.base_median(tmp_path) == 300
