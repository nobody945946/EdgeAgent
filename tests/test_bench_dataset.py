"""bench/dataset.py: one product version per output, but a task's own
settings (its MCP servers, web denied) are not a version."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import dataset

from edge_agent import prompt

READ = {"name": "read", "description": "Read a file", "parameters": {"type": "object"}}
WEB = {"name": "web_fetch", "description": "Read web page", "parameters": {"type": "object"}}


def _row(run: str, tools: list[dict], extra: str = "") -> dict:
    system = prompt.system_prompt("change") + "\n<cwd>/work</cwd>\n" + extra
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "測試"}],
            "source": {"run": run, "first_tools": {t["name"]: dataset._digest(t) for t in tools}}}


def test_a_tasks_servers_and_tool_subset_are_not_a_version() -> None:
    servers = "<addendum>\n測試伺服器\n</addendum>\n\n<mcp_servers>\n- mcp__test: 測試\n</mcp_servers>\n"
    rows = [_row("tools", [READ, WEB]), _row("mcp", [READ], servers)]
    assert dataset.mixed_versions(rows) == {}


def test_a_tool_defined_two_ways_or_another_prompt_is_refused() -> None:
    changed = READ | {"description": "Read a file (new)"}
    mixed = dataset.mixed_versions([_row("a", [READ]), _row("b", [changed])])
    assert list(mixed) == ["tool read"]
    mixed = dataset.mixed_versions([_row("a", [READ]), _row("b", [READ], "多一段產品文字\n")])
    assert list(mixed) == ["system change"]


def test_the_final_answer_keeps_its_reasoning() -> None:
    trajectory = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "先算日期"},
                                                    {"type": "text", "text": "答案 [1]"}]}]
    assert dataset.final_answer(trajectory) == {"role": "assistant", "content": "答案 [1]",
                                                "reasoning_content": "先算日期"}


def test_both_thinking_modes_from_one_row() -> None:
    row = {"messages": [{"role": "user", "content": "問"},
                        {"role": "assistant", "content": "答", "reasoning_content": "想"}], "tools": []}
    on, off = dataset.with_thinking_modes(row, "both")
    assert on["thinking"] and on["messages"][1]["reasoning_content"] == "想"
    assert not off["thinking"] and "reasoning_content" not in off["messages"][1]
    assert dataset.with_thinking_modes({"messages": [{"role": "user", "content": "問"}]}, "both") == [
        {"messages": [{"role": "user", "content": "問"}], "thinking": False}]
    (only,) = dataset.with_thinking_modes(row, "none")
    assert only == off


def test_plain_conversation_is_answered_at_once_in_either_mode() -> None:
    row = {"messages": [{"role": "user", "content": "你是誰"},
                        {"role": "assistant", "content": "我是機魂。", "reasoning_content": "對方問我是誰"}],
           "tools": [], "source": {"kind": dataset.CHAT}}
    for mode in dataset.THINKING:
        on, off = dataset.with_thinking_modes(row, mode)
        assert on["thinking"] and not off["thinking"]
        assert not any("reasoning_content" in m for m in on["messages"] + off["messages"])


def test_a_thought_the_budget_cut_short_is_never_a_target() -> None:
    row = {"messages": [{"role": "user", "content": "問"},
                        {"role": "assistant", "content": "", "reasoning_content": "先讀檔"},
                        {"role": "assistant", "content": "答", "reasoning_content": f"想到一半{dataset.BUDGET_CUT}"}],
           "tools": []}
    for mode in dataset.THINKING:
        (only,) = dataset.with_thinking_modes(row, mode)
        assert not only["thinking"] and not any("reasoning_content" in m for m in only["messages"])


def test_a_new_user_turn_ends_a_stretch() -> None:
    first = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "一"}]}
    second = {"messages": [*first["messages"], {"role": "assistant", "content": "答"},
                           {"role": "user", "content": "二"}]}
    assert dataset.user_turns(second) > dataset.user_turns(first)


def test_rows_teach_taiwan_forms_but_leave_traditional_text_alone() -> None:
    assert dataset.taiwan_traditional("这个软件") == "這個軟體"
    assert dataset.taiwan_traditional("測試通過") == "測試通過"


def test_no_eval_split_ever_becomes_a_row() -> None:
    assert dataset.is_eval("eval-G-016") and dataset.is_eval("py-eval-002")
    assert all(map(dataset.is_eval, dataset.EVAL_TASKS))
    assert not any(map(dataset.is_eval, ("train-G-016", "py-train-002", "archive-00")))


def _takeover_run(tmp_path: Path, differs: int) -> Path:
    """A takeover job (run.py --takeover) over four model calls: the student's
    two answers replayed, then the teacher's two."""
    run_dir = tmp_path / "takeover"
    records = run_dir / "records" / "archive-00-r1t2"
    records.mkdir(parents=True)
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "整理檔案"}]
    for index in range(4):
        (records / f"{index:04d}.json").write_text(json.dumps({"messages": messages, "tools": [{"type": "function", "function": READ}]}), encoding="utf-8")
        messages = [*messages, {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{index}", "content": f"結果{index}"}]
    (run_dir / "traj").mkdir()
    (run_dir / "traj" / "archive-00-r1t2.json").write_text(json.dumps(
        [{"role": "assistant", "content": [{"type": "text", "text": "整理好了"}]}]), encoding="utf-8")
    (run_dir / "results.jsonl").write_text(json.dumps({
        "task": "archive-00", "rep": "1t2", "kind": "", "passed": True, "outcome": "done", "grade": {},
        "takeover": {"from": "archive-00-r1", "at": 2, "of": 4, "replay_differs": differs}}) + "\n", encoding="utf-8")
    (run_dir / "verdicts.jsonl").write_text(json.dumps({"job": "archive-00-r1t2", "passed": True}) + "\n", encoding="utf-8")
    return run_dir


def test_a_takeover_trains_only_what_the_teacher_wrote(tmp_path: Path) -> None:
    """Learning from its own mistakes: the student's replayed answers are the
    situation; only the teacher's answers from the takeover on are targets."""
    (row,), _ = dataset.rows(_takeover_run(tmp_path, 0))
    targets = [m for m in row["messages"][row["train_from"]:] if m["role"] == "assistant"]
    # Four calls answer c0, c1, c2 and the final answer: from call 2 on, c2 and the answer.
    assert row["train_from"] == 2 + 2 * 2 and [m.get("content") for m in targets] == [None, "整理好了"]
    assert row["source"]["takeover"] == {"from": "archive-00-r1", "at": 2, "of": 4}


def test_a_takeover_at_the_students_mistake_gives_a_wrong_right_pair(tmp_path: Path) -> None:
    """The student repeated a call where the teacher, in the same situation,
    called read again with c2: that request, the teacher's reply as chosen and
    the student's as rejected; a takeover kept no rejected reply at a fraction."""
    run_dir = _takeover_run(tmp_path, 0)
    wrong = {"role": "assistant", "content": None, "reasoning_content": "再讀一次", "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "read", "arguments": "{\"path\": \"a\"}"}}]}
    result = json.loads((run_dir / "results.jsonl").read_text(encoding="utf-8"))
    result["takeover"]["rejected"] = wrong
    (run_dir / "results.jsonl").write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    found: list[dict] = []
    rows, _ = dataset.rows(run_dir, found)
    assert len(rows) == 1 and len(found) == 1
    (pair,) = found
    assert len(pair["messages"]) == 2 + 2 * 2  # request 2: what the student had seen
    assert pair["chosen"]["tool_calls"][0]["id"] == "c2" and pair["rejected"] == wrong
    assert pair["source"]["takeover"] == {"from": "archive-00-r1", "at": 2, "of": 4}
    # With thinking both: as recorded, and once without the reasoning (the call stays).
    marked, plain = dataset.pair_modes(pair, "both")
    assert marked["thinking"] and not plain["thinking"] and "reasoning_content" not in plain["rejected"]
    # A runaway thought says nothing without its reasoning: no pair then.
    runaway = {**pair, "rejected": {"role": "assistant", "content": None, "reasoning_content": "繞"}}
    assert [p["thinking"] for p in dataset.pair_modes(runaway, "both")] == [True]
    assert dataset.pair_modes(runaway, "none") == []
    result["takeover"].pop("rejected")
    (run_dir / "results.jsonl").write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    found = []
    dataset.rows(run_dir, found)
    assert found == []


def test_a_takeover_whose_replay_drifted_is_not_used(tmp_path: Path) -> None:
    rows, why = dataset.rows(_takeover_run(tmp_path, 1))
    assert rows == [] and why == {"takeover replay drifted": 1}


def test_a_clean_job_whose_answers_the_judge_did_not_pass_gives_no_rows(tmp_path: Path) -> None:
    """A tool task's check grades only what it asked for: v3's public_ok-05-r0
    passed its check with a wrong aside on how a bill splits."""
    run_dir = _takeover_run(tmp_path, 0)
    (run_dir / "verdicts.jsonl").write_text(json.dumps({"job": "archive-00-r1t2", "passed": False}) + "\n", encoding="utf-8")
    assert dataset.rows(run_dir) == ([], {"answers not passed by the judge": 1})
    (run_dir / "verdicts.jsonl").unlink()  # a run never judged
    assert dataset.rows(run_dir) == ([], {"answers not passed by the judge": 1})


def test_a_job_that_met_the_sandboxs_missing_network_gives_no_rows(tmp_path: Path) -> None:
    """pip install Pillow and an exchange-rate API in the v3 recording
    (2026-10-14): the laptop's network works, so the job is not its situation."""
    run_dir = _takeover_run(tmp_path, 0)
    failed = "Failed to establish a new connection: [Errno -3] Temporary failure in name resolution"
    (run_dir / "traj" / "archive-00-r1t2.json").write_text(json.dumps(
        [{"role": "toolResult", "content": [{"type": "text", "text": failed}]},
         {"role": "assistant", "content": [{"type": "text", "text": "整理好了"}]}]), encoding="utf-8")
    rows, why = dataset.rows(run_dir)
    assert rows == [] and why == {"met the sandbox's missing network": 1}


def _calls(*commands: str) -> list[dict]:
    return [{"role": "assistant", "content": [{"type": "toolCall", "name": "powershell", "arguments": {"command": c}}
                                              for c in commands]}]


def test_a_job_that_searched_a_whole_drive_gives_no_rows() -> None:
    """The teacher's searches the v3 recording held (2026-10-14): minutes on the
    laptop, never how to find a file in the work folder."""
    for command in (
        "Get-ChildItem -Path C:\\ -Recurse -Filter trade_log.csv -ErrorAction SilentlyContinue | Select-Object FullName",
        'Get-ChildItem -Path "C:\\Users","C:\\" -Directory -Filter "家庭照片" -Recurse -ErrorAction SilentlyContinue',
        "$hits = Get-ChildItem C:\\ -Recurse -File -ErrorAction SilentlyContinue -Depth 5 -Include *.log",
        "Get-ChildItem -LiteralPath 'C:\\Users\\lin' -Recurse -Filter 'trade*.csv'",
        "gci $env:USERPROFILE -rec -Filter *.pdf",
    ):
        assert dataset.whole_drive_scan(_calls(command)), command
    for command in (
        "Get-ChildItem -Path /work -Recurse -Filter *.csv",
        "Get-ChildItem C:\\work\\project -Recurse",
        "Get-PSDrive -PSProvider FileSystem; Get-ChildItem C:\\",
        "Get-ChildItem . -Recurse | Measure-Object",
        'Get-ChildItem "C:\\Users" -Recurse -Depth 2 -Filter "Downloads" -Force -ErrorAction SilentlyContinue',
    ):
        assert not dataset.whole_drive_scan(_calls(command)), command
