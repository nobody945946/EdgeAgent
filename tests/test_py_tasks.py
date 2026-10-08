"""bench/py_tasks.py: the teacher's Python tasks parsed and written in the bench's task format."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import py_tasks

ANSWER = """=== 標題 ===
移動平均算錯
=== 需求 ===
ma.py 算 3 日均線怎麼第一天就有值？前兩天應該是 None，幫我修
=== 檔案：ma.py ===
def moving_average(prices, n=3):
    return [sum(prices[max(0, i - n + 1):i + 1]) / n for i in range(len(prices))]
=== 測試：test_ma.py ===
from ma import moving_average


def test_first_days_are_none():
    assert moving_average([3, 6, 9, 12]) == [None, None, 6.0, 9.0]
=== 答案：ma.py ===
def moving_average(prices, n=3):
    return [None if i < n - 1 else sum(prices[i - n + 1:i + 1]) / n for i in range(len(prices))]
=== 結束 ===
"""


def test_a_task_is_read_from_its_sections_and_written_as_a_bench_task(tmp_path: Path) -> None:
    task = py_tasks.parse_task(ANSWER)
    assert task is not None and task["title"] == "移動平均算錯" and list(task["tests"]) == ["test_ma.py"]
    assert py_tasks.problems(task) == []
    task.update(kind="fix", topic="股價", teacher="t", prompt="p")
    task_dir = py_tasks.write_task(tmp_path, "py-train-001", task)
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    assert spec["kind"] == "code" and spec["check"][-1] == "bench_hidden" and "ma.py" in spec["may_change"]
    assert (task_dir / "hidden" / "bench_hidden" / "test_ma.py").is_file()
    assert "None if i < n - 1" in (task_dir / "solution" / "ma.py").read_text(encoding="utf-8")


def test_a_cut_answer_or_a_path_outside_the_project_is_refused() -> None:
    assert py_tasks.parse_task(ANSWER.replace("=== 結束 ===\n", "")) is None
    task = py_tasks.parse_task(ANSWER.replace("=== 答案：ma.py ===", "=== 答案：../ma.py ==="))
    assert task is not None and py_tasks.problems(task) == ["paths must stay in the project"]


def test_eval_topics_are_never_training_topics() -> None:
    assert not set(py_tasks.topics_for("eval")) & set(py_tasks.topics_for("train"))
