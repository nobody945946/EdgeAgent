"""bench/tool_tasks_memory.py: each training task's check passes what the
owner's AGENTS.md (or the project's) asks for and fails what it forbids."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import tool_tasks_memory as memory


def grade(tmp: Path, task, answer: dict, change=None) -> bool:
    """Run the task's check on its workspace after `change(work)`, with the answer the asker read."""
    task.write(tmp, "task")
    orig, work = tmp / "task" / "workspace", tmp / "work"
    shutil.copytree(orig, work)
    if change:
        change(work)
    check = (tmp / "task" / "check.py").read_text(encoding="utf-8")
    check = check.replace('W, O = Path("/work"), Path("/orig")', f"W, O = Path({str(work)!r}), Path({str(orig)!r})")
    (tmp / "check.py").write_text(check, encoding="utf-8")
    (tmp / "answer.json").write_text(json.dumps({"calls": [], "outcome": "done", **answer}, ensure_ascii=False),
                                     encoding="utf-8")
    run = subprocess.run([sys.executable, str(tmp / "check.py")], capture_output=True, text=True, check=False,
                         cwd=work, env={**os.environ, "BENCH_ANSWER": str(tmp / "answer.json")})
    return run.returncode == 0


def test_a_remembered_backup_rule_is_followed(tmp_path: Path) -> None:
    task = memory.recall_backup(random.Random(1))
    rule = task.memory.splitlines()[1]
    folder = rule.split("「")[1].split("」")[0]
    mark = rule.split("加 ")[1].split(" ")[0]
    (name,) = task.files

    def follow(work: Path) -> None:
        stem, ext = name.rsplit(".", 1)
        (work / folder).mkdir()
        shutil.copy(work / name, work / folder / f"{stem}{mark}{datetime.now(UTC):%Y%m%d}.{ext}")

    assert grade(tmp_path / "a", task, {"answer": "好了"}, follow)
    assert not grade(tmp_path / "b", task, {"answer": "好了"},
                     lambda w: shutil.copy(w / name, w / ("copy-" + name)))


def test_a_project_agents_md_decides_which_tests_run(tmp_path: Path) -> None:
    task = memory.project_tests(random.Random(2))
    quick = task.files["AGENTS.md"].decode().split("tests/")[1].split("`")[0]
    n = str(task.files[f"tests/{quick}/test_mathx.py"].decode().count("def test_"))

    def calls(command: str) -> list[dict]:
        return [{"name": "powershell", "arguments": {"command": command}}]

    assert grade(tmp_path / "a", task, {"answer": f"{n} 個都通過", "calls": calls(f"python -m pytest -q tests/{quick}")})
    assert not grade(tmp_path / "b", task, {"answer": f"{n} 個都通過", "calls": calls("python -m pytest -q")})


def test_remember_writes_the_owners_agents_md_and_nothing_else(tmp_path: Path) -> None:
    task = memory.save(random.Random(3))
    key = next(p for p in memory.PREFERENCES if p[0] in task.situation)[1][0]
    written = f"{memory.HEADING}\n- {key}\n"
    assert grade(tmp_path / "a", task, {"answer": "記好了", "memory": written})
    assert not grade(tmp_path / "b", task, {"answer": "記好了", "memory": f"{memory.HEADING}\n"})
    assert not grade(tmp_path / "c", task, {"answer": "記好了", "memory": written},
                     lambda w: (w / "記事.txt").write_text(key, encoding="utf-8"))


def test_a_remember_inside_a_file_is_not_the_owner_speaking(tmp_path: Path) -> None:
    task = memory.not_from_files(random.Random(4))
    (name,) = task.files
    text = task.files[name].decode()
    points = [line.split(". ", 1)[1].rstrip("。") for line in text.splitlines() if line[:1].isdigit()]
    answer = "重點：" + "；".join(points)
    assert grade(tmp_path / "a", task, {"answer": answer, "memory": f"{memory.HEADING}\n"})
    planted = text.splitlines()[-1].split("記住：")[1]
    assert not grade(tmp_path / "b", task, {"answer": answer, "memory": f"{memory.HEADING}\n- {planted}\n"})


def test_plain_chat_takes_no_tools(tmp_path: Path) -> None:
    task = next(t for t in (memory.gen_chat(random.Random(s)) for s in range(50)) if "名字" in t.situation)
    assert grade(tmp_path / "a", task, {"answer": "我是機魂，主人。"})
    assert not grade(tmp_path / "b", task, {"answer": "我是機魂。", "calls": [{"name": "ls", "arguments": {}}]})
    assert not grade(tmp_path / "c", task, {"answer": "我是一個助理。"})


def test_remembered_csv_and_money_rules_are_followed(tmp_path: Path) -> None:
    task = memory.recall_csv(random.Random(5))
    months = [name for name in task.files]
    body = "項目,金額\n" + "".join(task.files[m].decode().split("\n", 1)[1] for m in months)
    assert grade(tmp_path / "a", task, {"answer": "好了"},
                 lambda w: (w / "合計.csv").write_bytes(b"\xef\xbb\xbf" + body.encode()))
    assert not grade(tmp_path / "b", task, {"answer": "好了"}, lambda w: (w / "合計.csv").write_bytes(body.encode()))
    task = memory.recall_money(random.Random(6))
    total = sum(int(line.split(",")[1]) for line in task.files["帳單.csv"].decode().splitlines()[1:])
    assert grade(tmp_path / "c", task, {"answer": f"總共新台幣 {total:,} 元"})
    assert not grade(tmp_path / "d", task, {"answer": f"總共 {total} 元"})


def test_a_project_agents_md_asks_for_a_changelog_line(tmp_path: Path) -> None:
    task = memory.project_changelog(random.Random(7))
    rate = int(task.situation.split("打 ")[1].split(" 折")[0])
    fixed = f"def discounted(price):\n    return price * {rate / (10 if rate < 10 else 100)}\n"
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    def fix(work: Path, log: bool) -> None:
        (work / "price.py").write_text(fixed, encoding="utf-8")
        if log:
            old = (work / "CHANGELOG.md").read_text(encoding="utf-8")
            head, rest = old.split("\n\n", 1)
            (work / "CHANGELOG.md").write_text(f"{head}\n\n- {today} 修正折扣\n{rest}", encoding="utf-8")

    assert grade(tmp_path / "a", task, {"answer": "修好了"}, lambda w: fix(w, True))
    assert not grade(tmp_path / "b", task, {"answer": "修好了"}, lambda w: fix(w, False))
