"""bench/run.py --takeover: learning from its own mistakes. A student's failed
job is replayed word for word up to a point, then the teacher continues."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import run

TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]


def _student(tmp_path: Path, *, passed: bool, kind: str = "", verdict: bool | None = None,
             rewritten: bool = False) -> Path:
    """A student's recorded run of one job: four model calls (and one
    simulated-user request without tools between them)."""
    (tmp_path / "tasks" / "archive-00").mkdir(parents=True)
    (tmp_path / "tasks" / "archive-00" / "task.json").write_text("{}")
    student = tmp_path / "student"
    records = student / "records" / "archive-00-r0"
    records.mkdir(parents=True)
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "一"}]
    names = iter(range(100))
    for index in range(4):
        (records / f"{next(names):04d}.json").write_text(json.dumps({"messages": messages, "tools": TOOLS}))
        if index == 1:
            (records / f"{next(names):04d}.json").write_text(json.dumps({"messages": [{"role": "user", "content": "sim"}]}))
            messages = [*messages, {"role": "assistant", "content": "答一"}, {"role": "user", "content": "二"}]
            continue
        messages = [*messages, {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{index}", "content": f"結果{index}"}]
        if rewritten and index == 2:
            messages[2] = {"role": "assistant", "content": "（摘要）"}
    (student / "results.jsonl").write_text(json.dumps({
        "task": "archive-00", "rep": 0, "kind": kind, "passed": passed, "outcome": "done", "grade": {},
        "dialogue": [{"user": "一"}, {"user": "二"}]}) + "\n")
    if verdict is not None:
        (student / "verdicts.jsonl").write_text(json.dumps({"job": "archive-00-r0", "passed": verdict}) + "\n")
    return student


@pytest.fixture(autouse=True)
def _tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "TASK_ROOT", tmp_path / "tasks")


def test_a_failed_job_is_taken_over_with_the_students_own_answers_and_lines(tmp_path):
    plans = run.takeover_plans(_student(tmp_path, passed=False), [0.25, 0.75], "*")
    assert [(task.name, rep) for task, rep, _ in plans] == [("archive-00", "0t1"), ("archive-00", "0t3")]
    _, _, late = plans[1]
    assert late["from"] == "archive-00-r0" and (late["at"], late["of"]) == (3, 4)
    # Word for word: the replies the next requests show, a turn's answer included.
    assert [r.get("content") for r in late["replies"]] == [None, "答一", None]
    assert late["script"] == ["一", "二"]  # the user had spoken twice by then
    assert late["seen"] == ['"結果0"', '"結果2"']  # what the teacher's first request shows


def test_passed_jobs_unjudged_scenarios_and_rewritten_histories_are_not_taken_over(tmp_path):
    assert run.takeover_plans(_student(tmp_path, passed=True), [0.5], "*") == []
    with pytest.raises(SystemExit, match="judge it first"):
        run.takeover_plans(_student(tmp_path / "s", passed=True, kind="scenario"), [0.5], "*")
    assert run.takeover_plans(_student(tmp_path / "j", passed=True, kind="scenario", verdict=True), [0.5], "*") == []
    # A compaction rewrote the history: no word-for-word replay past it.
    plans = run.takeover_plans(_student(tmp_path / "r", passed=False, rewritten=True), [0.25, 0.75], "*")
    assert [rep for _, rep, _ in plans] == ["0t1"]


def test_a_replay_that_drifted_is_counted(tmp_path):
    folder = tmp_path / "live"
    folder.mkdir()
    request = {"tools": TOOLS, "messages": [{"role": "tool", "content": "結果0"}, {"role": "tool", "content": "結果2"}]}
    for index in range(4):
        (folder / f"{index:04d}.json").write_text(json.dumps(request, ensure_ascii=False))
    assert run.replay_differs(folder, 3, ['"結果0"', '"結果2"']) == 0
    assert run.replay_differs(folder, 3, ['"結果0"', '"結果9"']) == 1
    assert run.replay_differs(folder, 5, ['"結果0"']) == 1  # it never got that far


def test_the_bench_samples_an_adapter_as_the_product_does(tmp_path, monkeypatch):
    """The product samples an Agent adapter at the temperatures its file
    carries (load_plan, models.SAMPLING); the bench reads the same, and a
    sweep sets them on purpose."""
    from test_models import gguf

    stamped = tmp_path / "stamped.q8_0.gguf"
    stamped.write_bytes(gguf(adapter=True, extra={"edgeagent.sampling.off.temp": 0.6,
                                                  "edgeagent.sampling.thinking.temp": 1.0}))
    (tmp_path / "plain.q8_0.gguf").write_bytes(gguf(adapter=True))
    monkeypatch.setattr(run, "MODELS", tmp_path)
    assert run.run_sampling({"lora": "stamped.q8_0.gguf"}, None) == {"off": 0.6, "thinking": 1.0}
    assert run.run_sampling({"lora": "plain.q8_0.gguf"}, None) == {}
    assert run.run_sampling({}, None) == {}
    assert run.run_sampling({"lora": "stamped.q8_0.gguf"}, "0.2") == {"off": 0.2, "thinking": 0.2}
    assert run.run_sampling({}, "off=0.2,thinking=1.0") == {"off": 0.2, "thinking": 1.0}
