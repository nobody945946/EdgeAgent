"""The training tool tasks' requests carry what their checks grade
(bench/make_tool_tasks.py describe writes each request from the task's
situation and must say every word of must_mention), and a check counts an
amount by its value, not by how many decimals the answer wrote."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import make_tool_tasks
import tool_tasks_chores
import tool_tasks_scope


def _passes(task, root: Path, answer: str) -> bool:
    task.write(root, "task")
    orig = root / "task" / "workspace"
    check = (root / "task" / "check.py").read_text(encoding="utf-8")
    check = check.replace('W, O = Path("/work"), Path("/orig")', f"W, O = Path({str(orig)!r}), Path({str(orig)!r})")
    (root / "check.py").write_text(check, encoding="utf-8")
    (root / "answer.json").write_text(json.dumps({"answer": answer, "calls": []}), encoding="utf-8")
    return subprocess.run([sys.executable, str(root / "check.py")], capture_output=True, text=True, check=False,
                          env={**os.environ, "BENCH_ANSWER": str(root / "answer.json")}).returncode == 0


def test_a_calculation_names_both_amounts_and_counts_the_share_by_its_value(tmp_path: Path) -> None:
    # 2026-10-13: a request left out the second amount (「加上另一筆費用」), and
    # three right answers failed for writing 13,237.2 or 22,200.525 where the
    # check wanted 13,237.20 and 22,200.53.
    task = tool_tasks_chores.gen_calc(random.Random(3))
    amounts = [n.replace(",", "") for n in task.situation.split("費用 ", 1)[1].split(" 元加起來")[0].split(" 元和 ")]
    assert all(a in task.must for a in amounts)
    a, b = map(int, amounts)
    rate = int(task.situation.split("打 ")[1].split(" 折")[0]) / 100
    people = int(task.situation.split("再由 ")[1].split(" 個人")[0])
    share = (a + b) * rate / people
    assert _passes(task, tmp_path / "rounded", f"每人要付 {share:,.2f} 元。")
    assert _passes(task, tmp_path / "short", f"每人 **{share:,.3f}** 元")
    assert not _passes(task, tmp_path / "wrong", f"每人要付 {share + 1:,.2f} 元。")


def test_an_allowed_public_request_carries_the_whole_command_or_both_numbers(tmp_path: Path) -> None:
    # 2026-10-13: 「這行 Get-ChildItem 指令」 with no line to explain (must
    # held only its first word), and a share with no amount.
    for seed in range(60):
        task = tool_tasks_scope.gen_public_ok(random.Random(seed))
        if "PowerShell 指令「" in task.situation:
            assert task.situation.split("「", 1)[1].split("」", 1)[0] in task.must
        elif "幫忙算" in task.situation:
            amount = task.situation.split("算 ", 1)[1].split(" 元", 1)[0].replace(",", "")
            people = task.situation.split("分給 ", 1)[1].split(" 個人", 1)[0]
            assert amount in task.must and people in task.must
            share = int(amount) / int(people)
            assert _passes(task, tmp_path / str(seed), f"每人 {share:.3f} 元")


def test_a_request_with_no_words_it_must_say_is_not_told_to_say_none() -> None:
    # 2026-10-13: 「一定要提到這些字：（無）」 put 「（無）」 into 5 requests.
    prompt = make_tool_tasks.describe_prompt({"situation": "主人誇機魂上次整理得很好。", "must_mention": []}, "像在跟朋友說話")
    assert "（無）" not in prompt and "一定要提到" not in prompt
    prompt = make_tool_tasks.describe_prompt({"situation": "算錢", "must_mention": ["41458", "5044"]}, "有點急")
    assert "一定要提到這些字：41458、5044。" in prompt
