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
             rewritten: bool = False, reason: str = "", trajectory: list | None = None) -> Path:
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
        "task": "archive-00", "rep": 0, "kind": kind, "passed": passed, "outcome": "error" if reason else "done",
        "reason": reason, "grade": {},
        "dialogue": [{"user": "一"}, {"user": "二"}]}) + "\n")
    if trajectory is not None:
        (student / "traj").mkdir()
        (student / "traj" / "archive-00-r0.json").write_text(json.dumps(trajectory, ensure_ascii=False))
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


def test_where_a_guard_stopped_the_student_the_teacher_answers_what_its_mistake_answered(tmp_path):
    """A runaway reply answered the last request; a repeated call (read {} in
    the replies to requests 0 and 2) was first repeated in the reply to 2."""
    plans = run.takeover_plans(_student(tmp_path, passed=False, reason="runaway"), [], "*")
    assert [rep for _, rep, _ in plans] == ["0t3"]
    plans = run.takeover_plans(_student(tmp_path / "b", passed=False, reason="repeat_limit"), [0.25], "*")
    assert [rep for _, rep, _ in plans] == ["0t1", "0t2"]
    _, _, plan = plans[1]
    assert plan["seen"] == ['"結果0"']  # the teacher has seen the call's one result
    # Other failures keep the fractions only.
    plans = run.takeover_plans(_student(tmp_path / "c", passed=False, reason="tool_limit"), [0.25], "*")
    assert [rep for _, rep, _ in plans] == ["0t1"]


def test_the_students_reply_at_its_mistake_is_kept_as_the_wrong_side_of_a_pair(tmp_path):
    """The repeated call (the reply to request 2, which request 3 shows); a
    finished job's wrong answer and a runaway thought from its trajectory, the
    runaway one even at the first request; fractions keep no pair."""
    plans = run.takeover_plans(_student(tmp_path, passed=False, reason="repeat_limit"), [0.25], "*")
    assert "rejected" not in plans[0][2]
    assert plans[1][2]["rejected"] == {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c2", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    answer = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "想"}, {"type": "text", "text": "錯的答案"}]}]
    plans = run.takeover_plans(_student(tmp_path / "d", passed=False, trajectory=answer), [], "*")
    assert [(rep, plan["rejected"]) for _, rep, plan in plans] == [
        ("0t3", {"role": "assistant", "content": "錯的答案", "reasoning_content": "想"})]
    student = tmp_path / "first"
    (student / "records" / "archive-00-r1").mkdir(parents=True)
    (student / "records" / "archive-00-r1" / "0000.json").write_text(json.dumps(
        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "你覺得呢"}], "tools": TOOLS}))
    (student / "traj").mkdir()
    (student / "traj" / "archive-00-r1.json").write_text(json.dumps([{"role": "assistant", "stopReason": "aborted",
                                                                    "content": [{"type": "thinking", "thinking": "繞"}]}]))
    (student / "results.jsonl").write_text(json.dumps({"task": "archive-00", "rep": 1, "kind": "", "passed": False,
                                                       "outcome": "error", "reason": "runaway", "grade": {}}) + "\n")
    plans = run.takeover_plans(student, [0.4, 0.75], "*")
    assert [(rep, plan["at"], plan["replies"], plan["rejected"]) for _, rep, plan in plans] == [
        ("1t0", 0, [], {"role": "assistant", "content": None, "reasoning_content": "繞"})]


def test_passed_jobs_unjudged_runs_and_rewritten_histories_are_not_taken_over(tmp_path):
    assert run.takeover_plans(_student(tmp_path, passed=True, verdict=True), [0.5], "*") == []
    # The judge sees every scenario and every job that passed its check: one without a verdict is not judged yet.
    for name, kind in (("s", "scenario"), ("t", "")):
        with pytest.raises(SystemExit, match="judge it first"):
            run.takeover_plans(_student(tmp_path / name, passed=True, kind=kind), [0.5], "*")
    assert run.takeover_plans(_student(tmp_path / "j", passed=True, kind="scenario", verdict=True), [0.5], "*") == []
    # A compaction rewrote the history: no word-for-word replay past it.
    plans = run.takeover_plans(_student(tmp_path / "r", passed=False, rewritten=True), [0.25, 0.75], "*")
    assert [rep for _, rep, _ in plans] == ["0t1"]


def test_a_job_that_passed_its_check_but_not_the_judge_is_taken_over(tmp_path):
    """Its check graded only what it asked for; the judge found its answers wrong."""
    plans = run.takeover_plans(_student(tmp_path, passed=True, verdict=False), [0.75], "*")
    assert [rep for _, rep, _ in plans] == ["0t3"]


def test_a_crash_rerun_that_passed_is_not_taken_over(tmp_path):
    """run.py reruns a crashed job under the same name: the crash's row stays in
    results.jsonl, but the sandbox failed there, not the student."""
    student = _student(tmp_path, passed=True, verdict=True)
    rerun = (student / "results.jsonl").read_text()
    crash = {"task": "archive-00", "rep": 0, "kind": "", "passed": False, "outcome": "crash", "grade": {}}
    (student / "results.jsonl").write_text(json.dumps(crash) + "\n" + rerun)
    assert run.takeover_plans(student, [0.5], "*") == []


def test_a_replay_that_drifted_is_counted(tmp_path):
    folder = tmp_path / "live"
    folder.mkdir()
    request = {"tools": TOOLS, "messages": [{"role": "tool", "content": "結果0"}, {"role": "tool", "content": "結果2"}]}
    for index in range(4):
        (folder / f"{index:04d}.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
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
