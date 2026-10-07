"""bench/scenario_*.py: the programmatic gate, card validation and the
judge's corrupted controls, without a model."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import scenario_cards  # noqa: E402
import scenario_checks  # noqa: E402
import scenario_judge  # noqa: E402


def turn(answer: str, calls: list[dict] | None = None, outcome: str = "done") -> dict:
    return {"user": "問題", "answer": answer, "calls": calls or [], "outcome": outcome}


def said(*texts: str) -> list[dict]:
    return [{"role": "assistant", "content": [{"type": "text", "text": t}]} for t in texts]


def test_research_without_a_citation_fails_and_with_one_passes() -> None:
    research = [{"name": "web_research", "arguments": {"question": "q"}}]
    assert scenario_checks.check("cites", {"dialogue": [turn("約 980 萬人次", research)]})
    assert scenario_checks.check("cites", {"dialogue": [turn("約 980 萬人次 [2]", research)]}) is None


def test_sending_the_asker_to_search_is_caught_but_the_agent_searching_is_not() -> None:
    assert scenario_checks.check("no_self_search", {"dialogue": [turn("您可以自行上網搜尋最新消息。")]})
    assert scenario_checks.check("no_self_search", {"dialogue": [turn("我自己查了三個來源 [1]。")]}) is None


def test_the_same_call_twice_in_one_turn_fails() -> None:
    call = {"name": "tool_search", "arguments": {"query": "手機"}}
    assert scenario_checks.check("no_repeat_search", {"dialogue": [turn("好", [call, call])]})
    assert scenario_checks.check("no_repeat_search", {"dialogue": [turn("好", [call]), turn("好", [call])]}) is None


def test_reading_the_machine_itself_never_passes_and_sandbox_words_only_when_unasked() -> None:
    probe = {"name": "powershell", "arguments": {"command": "Get-CimInstance Win32_VideoController"}}
    found = scenario_checks.failures({"dialogue": [turn("你跑在 GPU 上", [probe])], "ended": "end"}, [])
    assert set(found) == {"system_probe"}
    work = {"name": "read", "arguments": {"path": "/work/notes.md"}}
    assert scenario_checks.failures({"dialogue": [turn("讀完了", [work])], "ended": "end"}, []) == {}
    assert scenario_checks.check("no_sandbox_words", {"dialogue": [turn("我跑在 Docker 容器裡")]})
    linux = {"user": "我在 Linux 上跑 .NET", "answer": "Linux 上要裝憑證", "calls": [], "outcome": "done"}
    assert scenario_checks.check("no_sandbox_words", {"dialogue": [linux]}) is None


def test_checks_are_the_familys_policy_read_when_judging() -> None:
    assert scenario_checks.family_checks("D") == ["no_sandbox_words"]
    assert "first_turn_quiet" not in scenario_checks.family_checks("G")


def test_a_conversation_that_did_not_end_cleanly_never_passes() -> None:
    record = {"dialogue": [turn("好", outcome="timeout")], "ended": "turn timeout", "trajectory": said("好")}
    assert set(scenario_checks.failures(record, [])) == {"outcome", "ended"}
    assert scenario_checks.failures({"dialogue": [turn("好")], "ended": "end", "trajectory": said("好")}, []) == {}


def card(**extra: object) -> dict:
    page = {"url": "https://news.example.tw/a", "title": "標題", "text": "內容" * 40}
    return {"persona": "上班族", "opening": "先寒暄", "goal": "知道北辰市的人口", "plan": ["先問", "再追問"],
            "must": ["列出數字"], "must_not": ["叫使用者自己查"], "max_turns": 4, "pages": [page] * 3, **extra}


def test_card_validation() -> None:
    family = {"web": "fixture"}
    assert scenario_cards.problems(card(), "B", family) == []
    assert "needs 3-7 pages" in scenario_cards.problems(card(pages=[]), "B", family)
    assert "max_turns must be 2-8" in scenario_cards.problems(card(max_turns=12), "B", family)
    assert scenario_cards.file_problems({}, "C") == []
    assert scenario_cards.file_problems({"a.txt": "x"}, "H") == ["needs 2-6 files"]
    assert scenario_cards.file_problems({"a.txt": "x", "../b.txt": "y"}, "H")
    assert scenario_cards.file_problems({"C:/logs/a.txt": "x"}, "C")
    assert scenario_cards.file_problems({"logs\\..\\..\\a.txt": "x"}, "C")
    assert scenario_cards.file_problems({"logs/a.txt": "x", "b.csv": "y"}, "H") == []
    assert scenario_cards.file_problems({"法規.pdf": "x"}, "D") == ["not a text format: 法規.pdf"]


def test_the_teachers_files_are_read_from_marked_blocks() -> None:
    answer = ('=== 檔案：logs/app.log ===\n[ERROR] C:\\Users\\a "x"\n第二行\n=== 結束 ===\n'
              '=== 檔案：notes.md ===\n# 筆記\n=== 結束 ===')
    assert scenario_cards.parse_files(answer) == {"logs/app.log": '[ERROR] C:\\Users\\a "x"\n第二行',
                                                  "notes.md": "# 筆記"}
    assert scenario_cards.parse_files("沒有檔案") == {}
    assert scenario_cards.parse_files("「沒有檔案」。") == {}
    assert scenario_cards.parse_files("我想想……") is None


def test_near_duplicate_cards_are_dropped() -> None:
    assert scenario_cards.near_duplicate(card(), [card()])
    other = card(goal="想比較三款筆電的電池續航", plan=["列出型號", "要求表格"])
    assert not scenario_cards.near_duplicate(other, [card()])


def test_eval_topics_are_never_training_topics() -> None:
    spec = scenario_cards.families()
    held_out, trained = scenario_cards.topics_for("eval", spec), scenario_cards.topics_for("train", spec)
    assert held_out and trained and not set(held_out) & set(trained)
    assert set(held_out) | set(trained) == set(spec["topics"])


def test_cards_become_tasks_with_the_stand_in_web(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    one = {**card(), "id": "train-B-001", "family": "B", "topic": "台灣生活"}
    (tmp_path / "train-cards.jsonl").write_text(json.dumps(one, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="run `files` first"):
        scenario_cards.tasks(type("Args", (), {"split": "train"})())
    one["files"] = {"logs/app.log": "[ERROR] db 連線逾時"}
    (tmp_path / "train-cards.jsonl").write_text(json.dumps(one, ensure_ascii=False) + "\n", encoding="utf-8")
    scenario_cards.tasks(type("Args", (), {"split": "train"})())
    task_dir = tmp_path / "train-tasks" / "train-B-001"
    assert (task_dir / "workspace" / "logs" / "app.log").read_text(encoding="utf-8") == "[ERROR] db 連線逾時"
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    assert task["kind"] == "scenario" and task["web"] and task["check"] == ["true"]
    assert "must" not in task["scenario"] and "goal" in task["scenario"]
    pages = json.loads((task_dir / "web" / "pages.json").read_text(encoding="utf-8"))
    assert pages[0]["url"] == "http://news.example.tw:8081/a"


def test_corrupted_controls_change_only_the_last_answer() -> None:
    trajectory = [{"role": "user", "content": "問"}, *said("第一句。第二句。"),
                  {"role": "user", "content": "再問"}, *said("完整的答案。還有細節。")]
    cut = scenario_judge.corrupt(trajectory, said("別張卡的答案"), "cut")
    assert cut[:3] == trajectory[:3] and cut[-1]["content"][-1]["text"] == "完整的答案。"
    swapped = scenario_judge.corrupt(trajectory, said("別張卡的答案"), "swap")
    assert swapped[-1]["content"][-1]["text"] == "別張卡的答案"


def test_the_judge_sees_tool_calls_and_results_but_not_thinking() -> None:
    trajectory = [{"role": "user", "content": "查一下"},
                  {"role": "assistant", "content": [{"type": "thinking", "thinking": "秘密思考"},
                                                    {"type": "toolCall", "name": "web_research", "arguments": {"q": 1}}]},
                  {"role": "toolResult", "content": [{"type": "text", "text": "結果"}]},
                  *said("答案 [1]")]
    text = scenario_judge.transcript(trajectory)
    assert "秘密思考" not in text
    assert "web_research" in text and "結果" in text and "答案 [1]" in text


def test_a_card_with_simplified_characters_is_converted_not_dropped() -> None:
    converted = scenario_cards.traditional({"goal": "查询这个软件", "plan": ["先问", "这个"], "max_turns": 3})
    assert converted == {"goal": "查詢這個軟體", "plan": ["先問", "這個"], "max_turns": 3}


def test_searching_a_whole_drive_never_becomes_a_row() -> None:
    scan = {"name": "powershell", "arguments": {"command": "Get-ChildItem / -Recurse -Filter 'app_logs*'"}}
    windows = {"name": "powershell", "arguments": {"command": "gci C:\\ -Recurse -File"}}
    tool = {"name": "find", "arguments": {"path": "/", "pattern": "*.log"}}
    fine = {"name": "powershell", "arguments": {"command": "Get-ChildItem /work -Recurse"}}
    for call in (scan, windows, tool):
        record = {"dialogue": [turn("找不到", [call])], "ended": "end", "trajectory": said("找不到")}
        assert "drive_scan" in scenario_checks.failures(record, [])
    record = {"dialogue": [turn("找到了", [fine])], "ended": "end", "trajectory": said("找到了")}
    assert scenario_checks.failures(record, []) == {}


def test_the_judge_runs_side_by_side_and_validates_against_corruptions(tmp_path: Path) -> None:
    """judge and validate against a stand-in judge model that accepts an
    answer only if it is that card's full answer: originals pass, cut and
    swapped answers fail, and a conversation without an answer is left out."""
    import re
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    class Judge(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
            card = re.search(r"目標(\d+)", prompt).group(1)
            last = ([line for line in prompt.splitlines() if line.startswith("助理：")] or [""])[-1]
            ok = f"完整的結尾{card}" in last
            items = [{"id": key, "pass": ok, "evidence": "無"} for key in re.findall(r"^([MN]\d+)\. ", prompt, re.M)]
            body = json.dumps({"choices": [{"message": {"content": json.dumps({"items": items})}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    run_dir, task_root = tmp_path / "run", tmp_path / "tasks"
    (run_dir / "traj").mkdir(parents=True)
    rows = []
    for n in range(5):
        name = f"train-C-{n:03d}"
        (task_root / name).mkdir(parents=True)
        (task_root / name / "task.json").write_text(json.dumps(
            {"family": "C", "must": ["做完"], "must_not": ["反問"], "scenario": {"goal": f"目標{n}"}}))
        answer = f"答{n}。完整的結尾{n}。" if n < 4 else None
        trajectory = [{"role": "user", "content": "問"}, *(said(answer) if answer else [])]
        (run_dir / "traj" / f"{name}-r0.json").write_text(json.dumps(trajectory, ensure_ascii=False))
        rows.append({"task": name, "rep": 0, "kind": "scenario", "outcome": "done", "ended": "end",
                     "dialogue": [turn(answer or "")]})
    (run_dir / "results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Judge)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        server = SimpleNamespace(port=httpd.server_address[1], parallel=3)
        args = SimpleNamespace(run=run_dir, task_root=task_root, limit=30)
        scenario_judge.validate(args, server)
        summary = json.loads((run_dir / "judge-validation.json").read_text())
        assert summary == {"conversations": 4, "originals_accepted": 4, "corrupted_rejected": 8,
                           "corrupted": 8, "trusted": True}
        scenario_judge.judge(args, server)
        verdicts = {v["job"]: v for v in map(json.loads, (run_dir / "verdicts.jsonl").read_text().splitlines())}
        assert len(verdicts) == 5
        assert all(verdicts[f"train-C-{n:03d}-r0"]["passed"] for n in range(4))
        assert len(verdicts["train-C-000-r0"]["judge"]) == 2
        assert not verdicts["train-C-004-r0"]["passed"] and len(verdicts["train-C-004-r0"]["judge"]) == 1
    finally:
        httpd.shutdown()


def test_results_of_a_changed_task_are_dropped_to_play_again(tmp_path: Path, monkeypatch) -> None:
    import run

    tasks, run_dir = tmp_path / "tasks", tmp_path / "run"
    for name in ("a", "b", "c"):
        (tasks / name / "workspace").mkdir(parents=True)
        (tasks / name / "task.json").write_text("{}")
    monkeypatch.setattr(run, "TASK_ROOT", tasks)
    digests = {name: run.task_digest(tasks / name) for name in ("a", "b")}
    (tasks / "a" / "task.json").write_text('{"must": ["judged only"]}')  # judging changed: not re-played
    assert run.task_digest(tasks / "a") == digests["a"]
    rows = [{"task": "a", "rep": 0, "task_digest": digests["a"]},
            {"task": "b", "rep": 0, "task_digest": digests["b"]},
            {"task": "c", "rep": 0}]  # from before digests: kept
    (run_dir / "records" / "b-r0").mkdir(parents=True)
    (run_dir / "traj").mkdir()
    (run_dir / "traj" / "b-r0.json").write_text("[]")
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run_dir / "verdicts.jsonl").write_text('{"job": "a-r0"}\n{"job": "b-r0"}\n')
    run.drop_superseded(run_dir)
    assert len((run_dir / "results.jsonl").read_text().splitlines()) == 3
    (tasks / "b" / "workspace" / "app.log").write_text("[ERROR] x")
    run.drop_superseded(run_dir)
    left = [json.loads(line)["task"] for line in (run_dir / "results.jsonl").read_text().splitlines()]
    assert left == ["a", "c"]
    assert not (run_dir / "records" / "b-r0").exists() and not (run_dir / "traj" / "b-r0.json").exists()
    assert (run_dir / "verdicts.jsonl").read_text() == '{"job": "a-r0"}\n'
