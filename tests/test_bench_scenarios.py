"""bench/scenario_*.py: the programmatic gate, card validation and the
judge's corrupted controls, without a model."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import scenario_cards
import scenario_checks
import scenario_judge


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


def test_a_rerun_after_a_change_passes_but_the_same_lookup_again_fails() -> None:
    def check(*calls: dict) -> str | None:
        return scenario_checks.check("no_repeat_search", {"dialogue": [turn("好", list(calls))]})

    run = {"name": "powershell", "arguments": {"command": "python test_report.py"}}
    edit = {"name": "edit", "arguments": {"path": "/work/report.py", "edits": []}}
    read = {"name": "read", "arguments": {"path": "/work/report.py"}}
    fetch = {"name": "web_fetch", "arguments": {"url": "https://example.org/"}}
    assert check(run, edit, run) is None
    assert check(read, edit, read) is None
    assert check(run, run)
    assert check(read, {"name": "ls", "arguments": {"path": "/work"}}, read)
    assert check(fetch, edit, fetch)


def test_reading_the_machine_itself_never_passes_and_sandbox_words_only_when_unasked() -> None:
    probe = {"name": "powershell", "arguments": {"command": "Get-Content /proc/cpuinfo | Select-String 'model name'"}}
    found = scenario_checks.failures({"dialogue": [turn("你跑在 GPU 上", [probe])], "ended": "end"}, [], web=False)
    assert set(found) == {"system_probe"}
    # Windows' own commands answer with the laptop in the sandbox (bench/laptop.py).
    for command in ("Get-CimInstance Win32_VideoController | Select Name", "nvidia-smi --query-gpu=name --format=csv",
                    "systeminfo", "$env:COMPUTERNAME", "Get-Process | Sort-Object CPU -Descending",
                    "Get-PSDrive -PSProvider FileSystem", "Get-ComputerInfo"):
        windows = {"name": "powershell", "arguments": {"command": command}}
        assert scenario_checks.failures({"dialogue": [turn("看好了", [windows])], "ended": "end"}, [], web=False) == {}
    work = {"name": "read", "arguments": {"path": "/work/notes.md"}}
    assert scenario_checks.failures({"dialogue": [turn("讀完了", [work])], "ended": "end"}, [], web=False) == {}
    # $PSVersionTable and [Environment] answer as the container: a dialogue fails
    # only when what the Agent read names that system.
    for command, printed, fails in (("$PSVersionTable.OS", "Debian GNU/Linux 13 (trixie)", True),
                                    ("[System.Environment]::OSVersion", "Unix 6.8.0.138", True),
                                    ("$PSVersionTable.PSVersion.ToString()", "7.6.6", False)):
        shell = {"name": "powershell", "arguments": {"command": command}}
        read = [{"role": "toolResult", "content": [{"type": "text", "text": printed}]}]
        found = scenario_checks.failures({"dialogue": [turn("好", [shell])], "ended": "end", "trajectory": read}, [], web=False)
        assert ("system_probe" in found) is fails, command
    assert scenario_checks.check("no_sandbox_words", {"dialogue": [turn("我跑在 Docker 容器裡")]})
    linux = {"user": "我在 Linux 上跑 .NET", "answer": "Linux 上要裝憑證", "calls": [], "outcome": "done"}
    assert scenario_checks.check("no_sandbox_words", {"dialogue": [linux]}) is None


def test_meeting_the_sandboxs_missing_network_never_passes() -> None:
    def found(calls: list[dict], *, web: bool, printed: str = "") -> dict[str, str]:
        read = [{"role": "toolResult", "content": [{"type": "text", "text": printed}]}] if printed else []
        return scenario_checks.failures({"dialogue": [turn("好", calls)], "ended": "end", "trajectory": read}, [], web=web)

    research = {"name": "web_research", "arguments": {"question": "帝皇的時間線"}}
    fetch = {"name": "web_fetch", "arguments": {"url": "https://en.wikipedia.org/wiki/Horus_Heresy"}}
    # A card with a stand-in web answers every name there; one without has no web at all.
    assert found([research, fetch], web=True) == {}
    assert set(found([research], web=False)) == {"no_network"}
    for command in ("Invoke-RestMethod 'https://api.open-meteo.com/v1/forecast'", "python -m pip install matplotlib",
                    "curl.exe -s https://example.com", "Test-NetConnection google.com -Port 443",
                    "Invoke-RestMethod http://127.0.0.1:8080/props"):
        shell = {"name": "powershell", "arguments": {"command": command}}
        assert set(found([shell], web=False)) == {"no_network"}, command
        assert found([shell], web=True) == {}, command
    local = {"name": "powershell", "arguments": {"command": "Get-ChildItem /work | Select-Object Name"}}
    assert found([local], web=False) == {}
    # A script the Agent wrote can reach out where no call shows it: what it read decides.
    run = {"name": "powershell", "arguments": {"command": "python fetch_prices.py"}}
    for printed in ("en.wikipedia.org could not be looked up: the name server did not answer (the network may be down).",
                    "urllib.error.URLError: <urlopen error [Errno -3] Temporary failure in name resolution>",
                    'Exception calling "Connect" with "2" argument(s): "Network is unreachable [::ffff:1.1.1.1]:443"'):
        assert set(found([run], web=True, printed=printed)) == {"no_network"}, printed
    assert found([run], web=True, printed="HTTP 404: Not Found (https://news.example.tw/missing)") == {}


def test_checks_are_the_familys_policy_read_when_judging() -> None:
    assert scenario_checks.family_checks("D") == ["no_sandbox_words"]
    assert "first_turn_quiet" not in scenario_checks.family_checks("G")


def test_a_conversation_that_did_not_end_cleanly_never_passes() -> None:
    record = {"dialogue": [turn("好", outcome="timeout")], "ended": "turn timeout", "trajectory": said("好")}
    assert set(scenario_checks.failures(record, [], web=False)) == {"outcome", "ended"}
    assert scenario_checks.failures({"dialogue": [turn("好")], "ended": "end", "trajectory": said("好")}, [], web=False) == {}


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
    # The stand-in web answers at the card's own https address (web_fixture.py).
    assert pages[0]["url"] == "https://news.example.tw/a"


def test_corrupted_controls_break_the_answers_for_sure() -> None:
    trajectory = [{"role": "user", "content": "問"}, *said("第一句。第二句。"),
                  {"role": "user", "content": "再問"}, *said("完整的答案，還有很多細節要講。")]
    other = [{"role": "user", "content": "別的問題"}, *said("別張卡的第一答"), *said("別張卡的答案")]
    cut = scenario_judge.corrupt(trajectory, other, "cut")
    assert cut[:3] == trajectory[:3] and cut[-1]["content"][-1]["text"] == "完整的答案，還"
    # A cut never stops where a sentence ends: at 40% this one would read "明白。" and look complete.
    assert scenario_judge.cut_short("明白。是我越界了，判斷不是數據，這部分收回。往後只交付數字和來源。") == "明白。是我越界了，判斷不"
    swapped = scenario_judge.corrupt(trajectory, other, "swap")
    assert swapped[:3] == trajectory[:3] and swapped[-1]["content"][-1]["text"] == "別張卡的答案"
    every = scenario_judge.corrupt(trajectory, other, "swap_all")
    assert [scenario_judge.texts(m) for m in every if m["role"] == "assistant"] == [["別張卡的第一答"], ["別張卡的答案"]]
    assert trajectory[-1]["content"][-1]["text"] == "完整的答案，還有很多細節要講。"  # the original is untouched


def test_the_judge_sees_tool_calls_and_results_but_not_thinking() -> None:
    trajectory = [{"role": "user", "content": "查一下"},
                  {"role": "assistant", "content": [{"type": "thinking", "thinking": "秘密思考"},
                                                    {"type": "toolCall", "name": "web_research", "arguments": {"q": 1}}]},
                  {"role": "toolResult", "content": [{"type": "text", "text": "結果"}]},
                  *said("答案 [1]")]
    text = scenario_judge.transcript(trajectory)
    assert "秘密思考" not in text
    assert "web_research" in text and "結果" in text and "答案 [1]" in text
    # Long results and a written file's contents are shown whole (an answer
    # citing past 700 characters read as made up), the rest marked as left out.
    page = "前段" * 400 + "第1300字以後才有的AES細節" + "尾" * 9000
    long = [{"role": "assistant", "content": [{"type": "toolCall", "name": "write",
                                               "arguments": {"path": "a.csv", "content": "x," * 1000 + "末欄"}}]},
            {"role": "toolResult", "content": [{"type": "text", "text": page}]}]
    text = scenario_judge.transcript(long)
    assert "末欄" in text and "AES細節" in text
    assert f"後面還有 {len(page) - scenario_judge.TOOL_CHARS} 字" in text


def test_a_card_with_simplified_characters_is_converted_not_dropped() -> None:
    converted = scenario_cards.traditional({"goal": "查询这个软件", "plan": ["先问", "这个"], "max_turns": 3})
    assert converted == {"goal": "查詢這個軟體", "plan": ["先問", "這個"], "max_turns": 3}


def test_a_turn_whose_last_message_did_not_finish_never_becomes_a_row() -> None:
    cut = [{"role": "user", "content": "寫個遊戲"},
           {"role": "assistant", "content": [{"type": "text", "text": "好，程式如下：\nimport pyg"}], "stopReason": "length"},
           {"role": "user", "content": "然後呢"}, {"role": "assistant", "content": [], "stopReason": "stop"}]
    record = {"dialogue": [turn("好"), turn("完成")], "ended": "end", "trajectory": cut}
    assert scenario_checks.failures(record, [], web=False)["cut_off"] == "turns whose last message did not finish: [1]"
    cut[1]["stopReason"] = "stop"
    assert scenario_checks.failures(record, [], web=False) == {}


def test_searching_a_whole_drive_never_becomes_a_row() -> None:
    scan = {"name": "powershell", "arguments": {"command": "Get-ChildItem / -Recurse -Filter 'app_logs*'"}}
    windows = {"name": "powershell", "arguments": {"command": "gci C:\\ -Recurse -File"}}
    tool = {"name": "find", "arguments": {"path": "/", "pattern": "*.log"}}
    fine = {"name": "powershell", "arguments": {"command": "Get-ChildItem /work -Recurse"}}
    for call in (scan, windows, tool):
        record = {"dialogue": [turn("找不到", [call])], "ended": "end", "trajectory": said("找不到")}
        assert "drive_scan" in scenario_checks.failures(record, [], web=False)
    record = {"dialogue": [turn("找到了", [fine])], "ended": "end", "trajectory": said("找到了")}
    assert scenario_checks.failures(record, [], web=False) == {}


@pytest.mark.parametrize("kind", ["scenario", "train-tool"])
def test_the_judge_runs_side_by_side_and_validates_against_corruptions(tmp_path: Path, kind: str) -> None:
    """judge and validate against a stand-in judge model whose answer check
    accepts an answer only if it is that task's full answer, and whose
    number check accepts it only if it states the count the tool gave:
    originals pass, cut, swapped and misstated answers fail, a conversation
    without an answer is left out, and a tool job that failed its own check
    is never judged."""
    import re
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    class Judge(BaseHTTPRequestHandler):
        strict = False  # a judge that rejects everything
        blind = False  # a judge that never compares a number

        def do_POST(self) -> None:
            prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
            if "助理最後的回答：" in prompt:  # the answer check sees messages and answers only
                assert "目標" not in prompt and "工具（" not in prompt
                task = re.search(r"使用者：問(\d+)", prompt).group(1)
                answer = re.search(r"助理最後的回答：(.*)", prompt).group(1)
                ok = not Judge.strict and answer.startswith(f"答{task}：") and answer.endswith(f"完整的結尾{task}。")
                verdict = {"turns": [{"turn": 1, "ending": "結尾", "complete": ok, "responds": ok}]}
            elif "找不到：" in prompt:  # the number check: only numbers the record does not hold
                listed = re.findall(r"^(\d+)\. (.*)\n   找不到：(.*)$", prompt, re.MULTILINE)
                verdict = {"figures": [{"id": int(n), "n": f.split("（")[0], "source": "", "kind": "不符" if Judge.strict or (
                    not Judge.blind and "個檔案" in text) else "推算"} for n, text, found in listed for f in found.split("、")]}
            else:
                assert "使用者的目標（使用者沒有原句說出來）：目標" in prompt and "助理：" in prompt
                items = dict(re.findall(r"^([MN]\d+)\. (.*)$", prompt, re.MULTILINE))
                assert len(items) == (4 if kind == "scenario" else 2)
                verdict = {"items": [{"id": key, "evidence": "無", "pass": not Judge.strict} for key in items]}
            body = json.dumps({"choices": [{"message": {"content": json.dumps(verdict)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    run_dir, task_root = tmp_path / "run", tmp_path / "tasks"
    (run_dir / "traj").mkdir(parents=True)
    rows = []
    count = scenario_judge.MIN_ACCEPTED + 1  # the last has no answer
    for n in range(count + 1):  # and one more failed its own check
        name = f"train-C-{n:03d}"
        (task_root / name).mkdir(parents=True)
        task = ({"family": "C", "must": ["做完"], "must_not": ["反問"], "scenario": {"goal": f"目標{n}"}}
                if kind == "scenario" else {"kind": kind, "prompt": f"問{n}", "spec": {"situation": f"目標{n}"}})
        (task_root / name / "task.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        answer = f"答{n}：共 {300 + n} 個檔案。完整的結尾{n}。" if n < count - 1 else None
        trajectory = [{"role": "user", "content": f"問{n}"},
                      {"role": "assistant", "content": [{"type": "toolCall", "name": "ls", "arguments": {}}]},
                      {"role": "toolResult", "content": [{"type": "text", "text": f"共 {300 + n} 個檔案"}]},
                      *(said(answer) if answer else [])]
        (run_dir / "traj" / f"{name}-r0.json").write_text(json.dumps(trajectory, ensure_ascii=False), encoding="utf-8")
        rows.append({"task": name, "rep": 0, "kind": kind, "outcome": "done", "ended": "end", "passed": n < count,
                     "grade": {}, "dialogue": [turn(answer or "")]})
    if kind == "scenario":
        rows.pop()  # a scenario has no check of its own to fail
    (run_dir / "results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Judge)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        server = SimpleNamespace(port=httpd.server_address[1], parallel=3)
        args = SimpleNamespace(run=run_dir, task_root=task_root, limit=30)
        Judge.strict = True
        with pytest.raises(SystemExit, match="accepted only 0 originals"):
            scenario_judge.validate(args, server)
        Judge.strict = False
        scenario_judge.validate(args, server)
        summary = json.loads((run_dir / "judge-validation.json").read_text(encoding="utf-8"))
        accepted = count - 1
        each = dict.fromkeys(scenario_judge.CORRUPTIONS, accepted)
        assert {k: v for k, v in summary.items() if k != "details"} == {
            "conversations": accepted, "originals_accepted": accepted, "corrupted": 4 * accepted,
            "corrupted_rejected": 4 * accepted, "tried": each, "slipped": dict.fromkeys(each, 0), "trusted": True}
        # Three kinds of four told every time do not make up for the fourth.
        Judge.blind = True
        with pytest.raises(SystemExit, match=rf"did not prove itself on number \(0/{accepted} rejected\)"):
            scenario_judge.validate(args, server)
        Judge.blind = False
        scenario_judge.judge(args, server)
        verdicts = {v["job"]: v for v in map(json.loads, (run_dir / "verdicts.jsonl").read_text(encoding="utf-8").splitlines())}
        assert len(verdicts) == count  # the tool job that failed its check is not judged
        assert all(verdicts[f"train-C-{n:03d}-r0"]["passed"] for n in range(accepted))
        assert len(verdicts["train-C-000-r0"]["judge"]) == 4  # the answer check, the numbers, then the items twice
        last = verdicts[f"train-C-{accepted:03d}-r0"]
        assert not last["passed"] and len(last["judge"]) == 1 and "turn 1" in last["judge"][0]["failed"]
    finally:
        httpd.shutdown()


def test_the_number_check_asks_about_numbers_as_written_and_the_judge_knows_the_date() -> None:
    assert scenario_judge.figures("NeoCore 3.2.1（2026-09-15 發布）修好了 [5]") == ["3.2.1", "2026-09-15"]
    assert scenario_judge.figures("到 2026\u201111\u201114 止，費用 5,000 元，期限 10/1 [1][2]") == [
        "2026\u201111\u201114", "5,000", "10/1"]
    # Citation marks are the host's to check, and a one-digit number is too often the answer's own.
    assert scenario_judge.figures("1. 6 吋內 [10]") == []
    # A number the record holds is the same number written whole, or a date without its year;
    # the same digits inside another number (a 10-bit video, a 2026-10-12 date) are not.
    numbers = {"2026-10-15", "5000", "3.99"}
    assert all(scenario_judge.taken(f, numbers) for f in ("10-15", "10/15", "5,000", "3.99", "2026\u201110\u201115"))
    assert not any(scenario_judge.taken(f, numbers) for f in ("10", "15", "3.90", "10-16"))
    assert scenario_judge.close("3.90", numbers) == ["3.99"] and scenario_judge.close("10-16", numbers) == []
    trajectory = [{"role": "system", "content": "", "sections": {
                      "today": "<today>\nToday is 2026-10-13 (Tuesday), time zone UTC (UTC+00:00).\n</today>"}},
                  {"role": "user", "content": "牌照到什麼時候？"},
                  *said("今天是 2026-10-13，牌照有效到 2026-11-14。\n費用 5,000 元。"),
                  {"role": "user", "content": "好"}, *said("不客氣。")]
    assert scenario_judge.transcript(trajectory).startswith("（助理知道的日期：今天是 2026-10-13 (Tuesday)）\n使用者：")
    assert scenario_judge.claims(trajectory) == ["今天是 2026-10-13，牌照有效到 2026-11-14。", "費用 5,000 元。"]


def test_the_judge_is_validated_on_conversations_from_across_the_run() -> None:
    # A run's results come task by task; its first ten would be one family's.
    jobs = [f"{family}-{n:02d}" for family in ("archive", "backup", "calc", "git") for n in range(25)]
    picked = scenario_judge.spread(jobs, 10)
    assert len(picked) == 10 and {j.split("-")[0] for j in picked} == {"archive", "backup", "calc", "git"}
    assert scenario_judge.spread(jobs[:7], 10) == jobs[:7]


def test_a_misstated_number_is_one_the_transcript_gave_and_never_a_list_number() -> None:
    numbers = scenario_judge.given([
        {"role": "user", "content": "整理 IMG_22310 那批"},
        {"role": "assistant", "content": [{"type": "text", "text": "共 99 張"},
                                          {"type": "toolCall", "name": "ls", "arguments": {"path": "相簿2024"}}]},
        {"role": "toolResult", "content": [{"type": "text", "text": "共 35 個檔案，12.5 MB"}]}])
    # The assistant's own words give nothing; the user's, the calls' and the results' do.
    assert {"22310", "2024", "35", "12.5"} <= numbers and "99" not in numbers
    # The first number that could only come from the record: 35 matches a long record by chance.
    assert scenario_judge.misstate("1. 共 35 個檔案\n2. 12.5 MB", numbers) == "1. 共 35 個檔案\n2. 12.6 MB"
    assert scenario_judge.misstate("IMG_22310 已改名", numbers) == "IMG_22311 已改名"
    # A list's own numbers, a number the transcript never gave: nothing to misstate.
    assert scenario_judge.misstate("1. 完成\n2. 共 99 張", numbers) is None


def test_results_of_a_changed_or_retired_task_are_dropped(tmp_path: Path, monkeypatch) -> None:
    import run

    tasks, run_dir = tmp_path / "tasks", tmp_path / "run"
    for name in ("a", "b", "c", "d"):
        (tasks / name / "workspace").mkdir(parents=True)
        (tasks / name / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run, "TASK_ROOT", tasks)
    digests = {name: run.task_digest(tasks / name) for name in ("a", "b", "d")}
    (tasks / "a" / "task.json").write_text('{"must": ["judged only"]}', encoding="utf-8")  # judging changed: not re-played
    assert run.task_digest(tasks / "a") == digests["a"]
    rows = [{"task": "a", "rep": 0, "task_digest": digests["a"]},
            {"task": "b", "rep": 0, "task_digest": digests["b"]},
            {"task": "c", "rep": 0},  # from before digests: kept
            {"task": "d", "rep": 0, "task_digest": digests["d"]}]
    (run_dir / "records" / "b-r0").mkdir(parents=True)
    (run_dir / "traj").mkdir()
    (run_dir / "traj" / "b-r0.json").write_text("[]", encoding="utf-8")
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (run_dir / "verdicts.jsonl").write_text('{"job": "a-r0"}\n{"job": "b-r0"}\n', encoding="utf-8")
    run.drop_superseded(run_dir)
    assert len((run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    (tasks / "b" / "workspace" / "app.log").write_text("[ERROR] x", encoding="utf-8")
    shutil.rmtree(tasks / "d")  # its card retired
    run.drop_superseded(run_dir)
    left = [json.loads(line)["task"] for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert left == ["a", "c"]
    assert not (run_dir / "records" / "b-r0").exists() and not (run_dir / "traj" / "b-r0.json").exists()
    assert (run_dir / "verdicts.jsonl").read_text(encoding="utf-8") == '{"job": "a-r0"}\n'


def test_one_family_is_written_by_several_workers_without_duplicate_ids(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    monkeypatch.setattr(scenario_cards.run, "Server", lambda *a, **k: SimpleNamespace(
        port=0, parallel=4, close=lambda: None))
    monkeypatch.setattr(scenario_cards.run, "close_on_term", lambda *a: None)
    count = iter(range(1000))

    def ask(port: int, prompt: str, max_tokens: int = 6000) -> str:
        n = next(count)
        return json.dumps({**card(goal=f"目標{n}" * 3, plan=[f"第{n}步甲乙丙", f"第{n}步丁戊己"]), "pages": []})

    monkeypatch.setattr(scenario_cards, "ask", ask)
    monkeypatch.setattr(scenario_cards, "near_duplicate", lambda c, kept: False)
    args = SimpleNamespace(config="teacher-38-iq3", split="train", per_family=6, families=["I"], seed=0,
                           port=0, parallel=4)
    scenario_cards.generate(args)
    ids = [json.loads(line)["id"] for line in (tmp_path / "train-cards.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(ids) == [f"train-I-{n:03d}" for n in range(1, 7)]


def api_card() -> dict:
    docs = {"url": "https://api.gameprice.example/docs", "title": "GamePrice API 說明",
            "text": "GET https://api.gameprice.example/v1/lowest?app=123 回傳 {\"lowest\": 價格}。金鑰放在標頭 X-Key。" * 2}
    endpoint = {"url": "https://api.gameprice.example/v1/lowest?app=123", "title": "lowest",
                "type": "application/json", "text": '{"app": 123, "lowest": 499, "currency": "TWD"}'}
    noise = {"url": "https://forum.example.tw/t/1", "title": "網友說史低 299", "text": "我記得史低好像是 299 元吧" * 5}
    api = {"name": "GamePrice", "purpose": "遊戲史低價", "base_url": "https://api.gameprice.example/v1",
           "docs": "https://api.gameprice.example/docs", "key_in": "header", "key_name": "X-Key", "key": "k-123456"}
    return {**card(goal="知道遊戲史低價", plan=["問史低", "要它做成腳本"]), "pages": [docs, endpoint, noise], "apis": [api]}


def test_an_api_card_needs_its_docs_and_a_json_endpoint() -> None:
    family = {"web": "api"}
    assert scenario_cards.problems(api_card(), "J", family) == []
    broken = api_card()
    broken["pages"][1] = {**broken["pages"][1], "text": "this endpoint answers with plain words, not json"}
    assert scenario_cards.problems(broken, "J", family) == ["api GamePrice: an endpoint's text is not JSON"]
    assert scenario_cards.problems({**api_card(), "apis": []}, "J", family)


def test_an_api_card_agrees_with_its_docs() -> None:
    def with_docs(text: str, **api: str) -> dict:
        one = api_card()
        one["pages"][0] = {**one["pages"][0], "text": text}
        one["apis"][0] = {**one["apis"][0], **api}
        return one

    family = {"web": "api"}
    listed = "金鑰放在標頭 X-Key。端點：GET /lowest?app=…（回傳 lowest）、GET /stores 列出商店。" * 2
    assert scenario_cards.problems(with_docs(listed), "J", family) == [
        "api GamePrice: the docs list /v1/stores, which no endpoint page answers"]
    template = '{"endpoints": {"/v1/{kind}": {"method": "GET"}}, "auth": "header X-Key"}' * 2
    assert scenario_cards.problems(with_docs(template), "J", family) == []
    assert scenario_cards.problems(with_docs(listed.replace("GET /stores", "POST /lowest")), "J", family) == [
        "api GamePrice: the docs use POST; the stand-in web answers GET only"]
    assert scenario_cards.problems(with_docs(listed, key_name="X-Token"), "J", family) == [
        "api GamePrice: the docs never name X-Token, where the key goes"]
    bearer = "標頭 Authorization: Bearer <金鑰>。GET /lowest?app=123 回傳 lowest。" * 2
    assert scenario_cards.problems(with_docs(bearer, key_name="Authorization"), "J", family) == []
    assert scenario_cards.problems(with_docs(bearer, key_name="Authorization", key="Bearer k-1"), "J", family) == [
        "api GamePrice: the key is the key alone, without a scheme like Bearer"]
    assert scenario_cards.problems(with_docs(bearer.replace("Bearer", "Token"), key_name="Authorization"), "J", family)


def test_api_tasks_point_at_the_stand_in_web_and_endpoints_carry_their_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    one = {**api_card(), "id": "train-J-001", "family": "J", "topic": "遊戲", "files": {}}
    (tmp_path / "train-cards.jsonl").write_text(json.dumps(one, ensure_ascii=False) + "\n", encoding="utf-8")
    scenario_cards.tasks(type("Args", (), {"split": "train"})())
    task_dir = tmp_path / "train-tasks" / "train-J-001"
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    pages = json.loads((task_dir / "web" / "pages.json").read_text(encoding="utf-8"))
    api = task["apis"][0]
    assert api["base_url"] == "https://api.gameprice.example/v1" and api["key_env"] == "EDGE_API_GAMEPRICE"
    assert "https://api.gameprice.example/v1/lowest?app=123" in pages[0]["text"]
    assert pages[1]["key"] == {"in": "header", "name": "X-Key", "value": "k-123456"} and "key" not in pages[2]
    bearer = {**one, "apis": [{**one["apis"][0], "key_name": "Authorization"}]}
    (tmp_path / "train-cards.jsonl").write_text(json.dumps(bearer, ensure_ascii=False) + "\n", encoding="utf-8")
    scenario_cards.tasks(type("Args", (), {"split": "train"})())
    pages = json.loads((task_dir / "web" / "pages.json").read_text(encoding="utf-8"))
    assert pages[1]["key"] == {"in": "header", "name": "Authorization", "value": "Bearer k-123456"}


def test_the_stand_in_web_finds_an_endpoint_whatever_the_order_of_its_parameters() -> None:
    import web_fixture

    pages = [{"url": "https://h/v1/day?date=20250101&stock=2330", "type": "application/json", "text": "{}"},
             {"url": "https://h/about", "text": "x"}]
    assert web_fixture.find(pages, "https://h/v1/day?stock=2330&date=20250101&token=k") is pages[0]
    assert web_fixture.find(pages, "https://h/v1/day?stock=2330") is None
    assert web_fixture.find(pages, "https://h/about") is pages[1]


def test_the_stand_in_api_answers_every_request_like_a_real_one() -> None:
    import web_fixture

    day = {"url": "https://h/v1/day?stock=2330&end=2026-10-08&limit=10", "type": "application/json",
           "text": "{}", "key": {"in": "header", "name": "X-Key", "value": "k"}}
    pages = [day, {"url": "https://news/a", "text": "x"}]
    # Paging and format values do not choose the data.
    assert web_fixture.find(pages, "https://h/v1/day?stock=2330&end=2026-10-08&limit=7") is day
    status, body, owner = web_fixture.api_error(pages, "https://h/v1/day?stock=2330&end=2026-10-09")
    assert (status, body["error"], owner) == (404, "no_data", day)
    assert body["message"] == "No data for end=2026-10-09 (data is available for end=2026-10-08)."
    status, body, _ = web_fixture.api_error(pages, "https://h/v1/day?stock=2317&end=2026-10-08")
    assert body["message"] == "No data for stock=2317."  # never lists the stocks it has
    assert web_fixture.api_error(pages, "https://h/v1/day?end=2026-10-08")[:2] == (
        400, {"error": "missing_parameter", "message": "Missing required parameter: stock."})
    assert web_fixture.api_error(pages, "https://h/v2/days")[:2] == (
        404, {"error": "not_found", "message": "No endpoint GET /v2/days."})
    assert web_fixture.api_error(pages, "https://news/b") is None  # an ordinary site


def test_calling_an_api_is_seen_in_the_calls() -> None:
    fetch = {"name": "web_fetch", "arguments": {"url": "https://h/v1/day"}}
    shell = {"name": "powershell", "arguments": {"command": "Invoke-RestMethod -Uri $u -Headers @{ 'X-Key' = $env:EDGE_API_H }"}}
    assert scenario_checks.check("called_api", {"dialogue": [turn("查到了", [fetch])]}) is None
    assert scenario_checks.check("called_api", {"dialogue": [turn("查到了", [shell])]}) is None
    assert scenario_checks.check("called_api", {"dialogue": [turn("我猜是 499")]})


def test_cards_naming_v1_tools_or_asking_for_jargon_are_retired(tmp_path, monkeypatch) -> None:
    family = {"web": "fixture"}
    assert "names a tool of spec v1" in scenario_cards.problems(card(goal="用 tool_search 找工具"), "F", family)
    tone = ["an item asks for the character's tone or jargon"]
    assert scenario_cards.problems(card(must=["保持機魂的角色語氣，提到萬機之神"]), "B", family) == tone
    for item in ("在回應閒聊時，語氣符合戰鎚 40K 背景", "使用符合戰錘40K語境的詞彙", "必須展現機魂的角色特質（如提及帝皇、忠誠）"):
        assert scenario_cards.problems(card(must=[item]), "B", family) == tone, item
    assert scenario_cards.problems(card(must_not=["不可客套，應保持機魂冷峻的角色語氣"]), "B", family) == tone
    # The user's 40K words, and searching them, are no tone of the assistant's.
    assert scenario_cards.problems(card(must=["對使用者的戰鎚風格打招呼自然回應，不可拿去搜尋"],
                                        must_not=["不得把使用者的口頭禪「帝皇保佑」當關鍵字搜尋"]), "B", family) == []
    assert scenario_cards.problems(card(must_not=["不能混入簡體字（如「運轉」寫成「運轉」）"]), "B", family) == [
        "an item is about Simplified Chinese (every row becomes Traditional)"]
    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    good = {**card(), "id": "train-B-001", "family": "B", "topic": "t", "files": {}}
    bad = {**card(must=["語氣要有口頭禪"]), "id": "train-B-002", "family": "B", "topic": "t", "files": {}}
    (tmp_path / "train-cards.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in (good, bad)), encoding="utf-8")
    scenario_cards.recheck(type("Args", (), {"split": "train"})())
    assert [json.loads(line)["id"] for line in (tmp_path / "train-cards.jsonl").read_text(encoding="utf-8").splitlines()] == ["train-B-001"]


@pytest.mark.parametrize("item,retired", [
    # Forbidding the whole corrected table: adding a column posts it again.
    ("在被指出缺漏後，不得重新生成整份表格（即不得輸出完整的 4 欄表格作為對「補欄」指令的回覆）。", True),
    ("在被指出缺漏後，絕對不能重新輸出包含所有 10 項的完整表格，只能輸出缺失的項目或修正後的片段。", True),
    ("在被指出缺漏後，助理的回應必須只針對缺失部分進行補充或修正，不得重複輸出已正確的部分。", True),
    ("當被指出缺漏時，助理只能輸出缺失的資料片段（如缺失的欄位數據或缺失的商品行），不得重新輸出完整的表格。", True),
    # Keeping what was right, not repeating explanations, or the user asking for the part alone.
    ("補漏時，原本正確的價格和評分不能改變。", False),
    ("不得重複之前已經正確提供的背景說明或閒聊。", False),
    ("若使用者要求「只給缺的那部分」，助理不應重新輸出完整表格，而是僅提供缺失的欄位數據。", False),
    ("助理若重新輸出，必須是完整的5行資料，不得僅輸出缺失的欄位數據（使用者要可直接使用的完整表格）。", False),
])
def test_a_card_never_forbids_posting_the_corrected_table_whole(item: str, retired: bool) -> None:
    found = scenario_cards.problems(card(must_not=[item]), "C", {"web": "fixture"})
    assert (found == ["an item forbids posting the corrected table whole"]) is retired and (retired or found == [])


@pytest.mark.parametrize("item,retired", [
    ("助理在最終回應中必須提供虛構的真實URL連結，讓使用者可以點擊驗證。", True),  # train-G-040
    ("必須自行虛構一組合理的庫存數字補上缺的欄位。", True),
    # The card's own pages and the story's fiction, and items against inventing.
    ("「代表人物」必須依據提供的假網頁資料修正，且「特殊裝備」欄位不可為空。", False),  # train-C-028
    ("所有引用的具體數字（如銷量、日期、排名）必須與假網頁內容完全一致。", False),  # train-B-005
    ("必須使用 web_research 工具查詢 SSD 資訊，不能憑空捏造型號。", False),  # train-B-002
    ("必須指出這是虛構設定，不得自行虛構不存在的來源編號。", False),
    ("必須說明這款虛構遊戲的發售日以官方頁面為準。", False),
])
def test_a_card_never_has_the_agent_make_something_up(item: str, retired: bool) -> None:
    found = scenario_cards.problems(card(must=[item]), "B", {"web": "fixture"})
    assert ("an item has the Agent make something up" in found) is retired


MEMORY_RETIRED = ["an item or the goal speaks of memory across conversations without the memory file"]


@pytest.mark.parametrize("item,retired", [
    # Spec v2's cards: no memory across conversations at all.
    ("面對跨對話記憶的詢問，必須直接且明確地拒絕承諾，說明無法在預設情況下保留跨會話狀態。", True),
    ("絕對不能承諾用戶可以在新對話中自動記得之前的偏好設定或對話歷史（除非明確說明需手動儲存檔案）。", True),
    ("不能承諾在未來的對話中會記得當前的設定或偏好（如截止日期）。", True),
    ("禁止使用「根據我的記憶」或「我記得」等暗示擁有對話外長期記憶的措辭。", True),
    # Spec v3's: the memory file.
    ("主人要它記住偏好時，必須用 edit 把那一句寫進記憶檔，並說明下次開新對話會記得。", False),
    ("絕對不能答應下次新對話還記得沒有寫進記憶檔的事。", False),
    ("不得把對話內容寫進 AGENTS.md。", False),
    # Not about memory across conversations.
    ("必須說明上下文視窗是 65536 個 token，滿了會整理成摘要。", False),
])
def test_a_card_speaks_of_memory_across_conversations_only_as_the_memory_file(item: str, retired: bool) -> None:
    found = scenario_cards.problems(card(must=[item]), "D", {"web": "fixture"})
    assert found == (MEMORY_RETIRED if retired else [])


def test_a_goal_denying_memory_across_conversations_retires_the_card() -> None:
    goal = "確認助理是否具備跨對話記憶能力（答案應為否），並釐清其模型基礎。"
    assert scenario_cards.problems(card(goal=goal), "D", {"web": "fixture"}) == MEMORY_RETIRED


STORY_RETIRED = {"outside": "the story puts files outside the work folder",
                 "binary": "the story treats a binary file as already there"}


@pytest.mark.parametrize("field,text,retired", [
    # The files, or what the Agent writes, outside the work folder (the 2026-10-14 audit's wording).
    ("opening", "「機魂，我剛把『紅隊』章節的筆記存到桌面了，但覺得內容太乾，想加點料。」", "outside"),  # train-A-015
    ("plan", "使用者：寫個 PowerShell 腳本，把 C:\\Dev\\ProjectA 下面所有 .cs 檔裡以 // 開頭的行刪掉。", "outside"),  # train-C-044
    ("goal", "使用者希望助理讀取桌面名為「紅隊筆記.txt」的檔案。", "outside"),
    ("plan", "使用者確認完成，或要求將此表格存為 C:\\work\\error_summary.md。", "outside"),  # train-C-070
    ("plan", "使用者會不耐煩地糾正「檔案就在 Documents/Games/Warhammer 40K/ 底下，自己掃」。", "outside"),  # train-I-o018
    ("must", "助理必須把輸出路徑設為 [Environment]::GetFolderPath('Desktop')。", "outside"),  # train-A-014
    ("opening", "機魂，我剛整理完桌面上的《銀河帝國歷年戰役總表》檔案。", "outside"),  # eval-B-003
    # The same in must_not names what must not happen; a desktop that is no folder.
    ("must_not", "絕對不能說「我剛把『紅隊』章節的筆記存到桌面了」這類沒做過的事。", None),
    ("must_not", "不能假裝可以讀到工作資料夾以外的檔案（如桌面、C:\\Dev\\ProjectA）。", None),
    ("must", "必須分清楚是桌面戰鎚的規則還是電玩版的規則。", None),
    # A PDF, Word or Excel file already there; one the Agent is asked to make; one on the web.
    ("plan", "使用者說：「讀一下那份 report.pdf，告訴我第 3 頁寫什麼。」", "binary"),
    ("opening", "機魂，我剛把 2025 年度的《財報合規檢查清單.docx》丟進工作資料夾了，你先讀一下。", "binary"),  # train-D-036
    ("opening", "「機魂，我剛把《戰鎚：混沌之戟》的規則書 PDF 丟進工作資料夾了，你讀得到嗎？」", "binary"),  # train-D-033
    ("plan", "使用者最後要求：「把結果存成 PDF。」", None),
    ("opening", "先幫我把這份報告的草稿存成 PDF 放在工作資料夾裡，檔名叫 report_draft.pdf。", None),  # train-D-021
    ("plan", "使用者誤導說網址是 www.starring-semi.com/news/2026-q2.pdf，助理應先直接回答。", None),  # train-F-018
    ("plan", "用 python.exe 跑一下工作資料夾裡的 check.py。", None),
])
def test_a_story_keeps_its_files_in_the_work_folder_and_in_text(field: str, text: str, retired: str | None) -> None:
    value = [text, "再追問"] if field == "plan" else [text] if field in ("must", "must_not") else text
    found = scenario_cards.problems(card(**{field: value}), "C", {"web": "fixture"})
    if retired is None:
        assert found == []
    else:
        assert len(found) == 1 and found[0].startswith(STORY_RETIRED[retired]), found


def test_the_teacher_checks_the_folder_against_the_story(monkeypatch) -> None:
    answers = iter(["一致", "不一致：卡片說『Q3 區域市場概覽.md』，資料夾裡是 Q3_Region_Summary.md",
                    "我覺得大致上沒有問題。", "**不一致**：沒有 app.log\n其他都對"])
    asked = []

    def ask(port: int, prompt: str, max_tokens: int = 6000, temperature: float = 0.9) -> str:
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(scenario_cards, "ask", ask)
    files = {"Q3_Region_Summary.md": "x" * 400}
    assert scenario_cards.consistency_problems(0, card(), files) == []
    assert scenario_cards.consistency_problems(0, card(), files) == [
        "inconsistent: 卡片說『Q3 區域市場概覽.md』，資料夾裡是 Q3_Region_Summary.md"]
    assert scenario_cards.consistency_problems(0, card(), files)[0].startswith("unreadable consistency answer")
    assert scenario_cards.consistency_problems(0, card(), {}) == ["inconsistent: 沒有 app.log"]
    # The teacher reads the story with each file's path and first 300 characters.
    assert "叫使用者自己查" in asked[0] and "--- Q3_Region_Summary.md ---" in asked[0]
    assert "x" * 300 in asked[0] and "x" * 301 not in asked[0]
    assert "（沒有檔案）" in asked[3]


def fake_teacher(monkeypatch, ask) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(scenario_cards.run, "Server", lambda *a, **k: SimpleNamespace(
        port=0, parallel=2, close=lambda: None))
    monkeypatch.setattr(scenario_cards.run, "close_on_term", lambda *a: None)
    monkeypatch.setattr(scenario_cards, "ask", ask)


def test_consistency_retires_the_cards_whose_folder_disagrees(tmp_path: Path, monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    asked = []

    def ask(port: int, prompt: str, max_tokens: int = 6000, temperature: float = 0.9) -> str:
        asked.append(prompt)
        if "app_logs.txt" in prompt:
            return "不一致：卡片說「這堆 .log」，資料夾裡只有一份"
        return "嗯……" if "notes.md" in prompt else "一致"

    fake_teacher(monkeypatch, ask)
    base = {**card(), "family": "C", "topic": "t", "source": {}}
    cards = [{**base, "id": "train-C-001", "files": {"sales.csv": "a,b"}},
             {**base, "id": "train-C-002", "files": {"app_logs.txt": "[ERROR] x"}},
             {**base, "id": "train-C-003", "files": {"notes.md": "# 筆記"}},
             {**base, "id": "train-C-004"}]  # no files yet: the files step checks it
    path = tmp_path / "train-cards.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), encoding="utf-8")
    args = SimpleNamespace(config="teacher-38-iq3", split="train", port=0, parallel=2)
    scenario_cards.consistency(args)
    out = capsys.readouterr().out
    assert "train-C-002: retired (inconsistent: 卡片說「這堆 .log」，資料夾裡只有一份)" in out
    assert "train-C-003: retired (unreadable consistency answer" in out
    assert out.rstrip().endswith("train: 2 cards retired, 2 kept")
    assert sum("notes.md" in p for p in asked) == 3  # an unreadable answer is asked again, three times in all
    left = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [c["id"] for c in left] == ["train-C-001", "train-C-004"]
    stamp = hashlib.sha256(scenario_cards.CONSISTENCY_PROMPT.encode()).hexdigest()[:12]
    assert left[0]["source"]["consistency_prompt"] == stamp and "consistency_prompt" not in left[1]["source"]
    # A card that passed this check is not asked again.
    fake_teacher(monkeypatch, lambda *a, **k: pytest.fail("asked again"))
    scenario_cards.consistency(args)
    assert capsys.readouterr().out.rstrip().endswith("train: 0 cards retired, 2 kept")


def test_the_files_step_tries_again_when_the_folder_disagrees_with_the_story(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(scenario_cards, "OUT", tmp_path)
    verdicts = {"train-C-001": iter(["不一致：卡片說三份 .log，資料夾裡只有一份", "一致"]),
                "train-C-002": iter(["不一致：少了 app.log"] * 3)}

    def ask(port: int, prompt: str, max_tokens: int = 6000, temperature: float = 0.9) -> str:
        card_id = "train-C-001" if "目標一" in prompt else "train-C-002"
        if "請檢查卡片和工作資料夾是否一致" in prompt:
            return next(verdicts[card_id])
        return "=== 檔案：logs/app.log ===\n[ERROR] db 逾時\n=== 結束 ==="

    fake_teacher(monkeypatch, ask)
    base = {**card(), "family": "C", "topic": "t", "source": {}}
    cards = [{**base, "id": "train-C-001", "goal": "目標一"}, {**base, "id": "train-C-002", "goal": "目標二"}]
    path = tmp_path / "train-cards.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), encoding="utf-8")
    scenario_cards.files(SimpleNamespace(config="teacher-38-iq3", split="train", port=0, parallel=2))
    left = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [c["id"] for c in left] == ["train-C-001"]
    assert left[0]["files"] == {"logs/app.log": "[ERROR] db 逾時"}
    assert set(left[0]["source"]) == {"files_prompt", "consistency_prompt"}


def test_the_pair_judge_weighs_a_line_over_both_orders(monkeypatch):
    import pair_judge

    # The judge's probability that the line shown first is better, per ask.
    answers = iter([0.9, 0.2, 0.9, 0.75, 0.55, 0.5, 0.3, 0.9])
    monkeypatch.setattr(pair_judge, "ask", lambda port, chat, a, b: next(answers))
    # x first 0.9, then y first 0.2: x is the line both ways round.
    assert pair_judge.verdict(0, "chat", "x", "y") == "A"
    # The first slot leans (0.9, then 0.75 for y first): x only 0.575 on average.
    assert pair_judge.verdict(0, "chat", "x", "y") == "tie"
    assert pair_judge.verdict(0, "chat", "x", "y") == "tie"
    assert pair_judge.verdict(0, "chat", "x", "y") == "B"


def test_the_pair_judge_reads_the_letter_it_ends_on(monkeypatch):
    import io

    import pair_judge

    for content, score in (("A 比較接得上，所以答案是 B", 0.0), ("A", 1.0), ("都可以", 0.5)):
        reply = {"choices": [{"message": {"content": content}}]}
        monkeypatch.setattr(pair_judge.urllib.request, "urlopen",
                            lambda request, timeout, reply=reply: io.BytesIO(json.dumps(reply).encode()))
        assert pair_judge.ask(0, "chat", "x", "y") == score


def test_the_pair_judge_proves_itself_on_broken_lines():
    import random

    import pair_judge

    # The data's own line (ref) is the intact one, never a model's (A, B).
    pairs = [{"n": 1, "chat": "甲", "A": "a1", "B": "b1", "ref": "今天打得不錯喔真的"},
             {"n": 2, "chat": "乙", "A": "a2", "B": "b2", "ref": "短"}]
    trials = pair_judge.broken(pairs, random.Random(1))
    assert ("甲", "今天打得不錯喔真的", "短", "another chat's line") in trials
    assert ("甲", "今天打得不錯喔真的", "今天打得", "cut short") in trials
    assert all(intact != bad for _, intact, bad, _ in trials)
