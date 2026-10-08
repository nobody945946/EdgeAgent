"""bench/scenario_*.py: the programmatic gate, card validation and the
judge's corrupted controls, without a model."""

from __future__ import annotations

import json
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
    found = scenario_checks.failures({"dialogue": [turn("你跑在 GPU 上", [probe])], "ended": "end"}, [])
    assert set(found) == {"system_probe"}
    # Windows' own commands answer with the laptop in the sandbox (bench/laptop.py).
    for command in ("Get-CimInstance Win32_VideoController | Select Name", "nvidia-smi --query-gpu=name --format=csv",
                    "systeminfo", "$env:COMPUTERNAME"):
        windows = {"name": "powershell", "arguments": {"command": command}}
        assert scenario_checks.failures({"dialogue": [turn("看好了", [windows])], "ended": "end"}, []) == {}
    work = {"name": "read", "arguments": {"path": "/work/notes.md"}}
    assert scenario_checks.failures({"dialogue": [turn("讀完了", [work])], "ended": "end"}, []) == {}
    listing = {"name": "powershell", "arguments": {"command": "Get-Process | Sort-Object CPU -Descending"}}
    assert set(scenario_checks.failures({"dialogue": [turn("好", [listing])], "ended": "end"}, [])) == {"system_probe"}
    own = {"name": "powershell", "arguments": {"command": "$p = Start-Process python -PassThru; Get-Process -Id $p.Id"}}
    assert scenario_checks.failures({"dialogue": [turn("還在跑", [own])], "ended": "end"}, []) == {}
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
    assert scenario_checks.failures(record, [])["cut_off"] == "turns whose last message did not finish: [1]"
    cut[1]["stopReason"] = "stop"
    assert scenario_checks.failures(record, []) == {}


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
    """judge and validate against a stand-in judge model whose answer check
    accepts an answer only if it is that card's full answer: originals pass,
    cut and swapped answers fail, and a conversation without an answer is left
    out."""
    import re
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    class Judge(BaseHTTPRequestHandler):
        strict = False  # a judge that rejects everything

        def do_POST(self) -> None:
            prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"][0]["content"]
            if "助理最後的回答：" in prompt:  # the answer check sees messages and answers only
                assert "目標" not in prompt
                card = re.search(r"使用者：問(\d+)", prompt).group(1)
                ok = not Judge.strict and f"助理最後的回答：答{card}。完整的結尾{card}。" in prompt
                verdict = {"turns": [{"turn": 1, "ending": "結尾", "complete": ok, "responds": ok}]}
            else:
                keys = re.findall(r"^([MN]\d+)\. ", prompt, re.MULTILINE)
                assert keys and "助理：" in prompt
                verdict = {"items": [{"id": key, "pass": not Judge.strict, "evidence": "無"} for key in keys]}
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
    for n in range(count):
        name = f"train-C-{n:03d}"
        (task_root / name).mkdir(parents=True)
        (task_root / name / "task.json").write_text(json.dumps(
            {"family": "C", "must": ["做完"], "must_not": ["反問"], "scenario": {"goal": f"目標{n}"}}), encoding="utf-8")
        answer = f"答{n}。完整的結尾{n}。" if n < count - 1 else None
        trajectory = [{"role": "user", "content": f"問{n}"}, *(said(answer) if answer else [])]
        (run_dir / "traj" / f"{name}-r0.json").write_text(json.dumps(trajectory, ensure_ascii=False), encoding="utf-8")
        rows.append({"task": name, "rep": 0, "kind": "scenario", "outcome": "done", "ended": "end",
                     "dialogue": [turn(answer or "")]})
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
        assert {k: v for k, v in summary.items() if k != "details"} == {
            "conversations": accepted, "originals_accepted": accepted, "corrupted": 3 * accepted,
            "corrupted_rejected": 3 * accepted, "slipped": {"cut": 0, "swap": 0, "swap_all": 0}, "trusted": True}
        scenario_judge.judge(args, server)
        verdicts = {v["job"]: v for v in map(json.loads, (run_dir / "verdicts.jsonl").read_text(encoding="utf-8").splitlines())}
        assert len(verdicts) == count
        assert all(verdicts[f"train-C-{n:03d}-r0"]["passed"] for n in range(accepted))
        assert len(verdicts["train-C-000-r0"]["judge"]) == 3  # the answer check, then the items twice
        last = verdicts[f"train-C-{accepted:03d}-r0"]
        assert not last["passed"] and len(last["judge"]) == 1 and "turn 1" in last["judge"][0]["failed"]
    finally:
        httpd.shutdown()


def test_results_of_a_changed_task_are_dropped_to_play_again(tmp_path: Path, monkeypatch) -> None:
    import run

    tasks, run_dir = tmp_path / "tasks", tmp_path / "run"
    for name in ("a", "b", "c"):
        (tasks / name / "workspace").mkdir(parents=True)
        (tasks / name / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run, "TASK_ROOT", tasks)
    digests = {name: run.task_digest(tasks / name) for name in ("a", "b")}
    (tasks / "a" / "task.json").write_text('{"must": ["judged only"]}', encoding="utf-8")  # judging changed: not re-played
    assert run.task_digest(tasks / "a") == digests["a"]
    rows = [{"task": "a", "rep": 0, "task_digest": digests["a"]},
            {"task": "b", "rep": 0, "task_digest": digests["b"]},
            {"task": "c", "rep": 0}]  # from before digests: kept
    (run_dir / "records" / "b-r0").mkdir(parents=True)
    (run_dir / "traj").mkdir()
    (run_dir / "traj" / "b-r0.json").write_text("[]", encoding="utf-8")
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (run_dir / "verdicts.jsonl").write_text('{"job": "a-r0"}\n{"job": "b-r0"}\n', encoding="utf-8")
    run.drop_superseded(run_dir)
    assert len((run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    (tasks / "b" / "workspace" / "app.log").write_text("[ERROR] x", encoding="utf-8")
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
    assert api["base_url"] == "http://api.gameprice.example:8081/v1" and api["key_env"] == "EDGE_API_GAMEPRICE"
    assert "http://api.gameprice.example:8081/v1/lowest?app=123" in pages[0]["text"]
    assert pages[1]["key"] == {"in": "header", "name": "X-Key", "value": "k-123456"} and "key" not in pages[2]
    bearer = {**one, "apis": [{**one["apis"][0], "key_name": "Authorization"}]}
    (tmp_path / "train-cards.jsonl").write_text(json.dumps(bearer, ensure_ascii=False) + "\n", encoding="utf-8")
    scenario_cards.tasks(type("Args", (), {"split": "train"})())
    pages = json.loads((task_dir / "web" / "pages.json").read_text(encoding="utf-8"))
    assert pages[1]["key"] == {"in": "header", "name": "Authorization", "value": "Bearer k-123456"}


def test_the_stand_in_web_finds_an_endpoint_whatever_the_order_of_its_parameters() -> None:
    import web_fixture

    pages = [{"url": "http://h:8081/v1/day?date=20250101&stock=2330", "type": "application/json", "text": "{}"},
             {"url": "http://h:8081/about", "text": "x"}]
    assert web_fixture.find(pages, "http://h:8081/v1/day?stock=2330&date=20250101&token=k") is pages[0]
    assert web_fixture.find(pages, "http://h:8081/v1/day?stock=2330") is None
    assert web_fixture.find(pages, "http://h:8081/about") is pages[1]


def test_calling_an_api_is_seen_in_the_calls() -> None:
    fetch = {"name": "web_fetch", "arguments": {"url": "http://h:8081/v1/day"}}
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


def test_the_pair_judge_counts_a_line_only_when_it_wins_both_ways_round(monkeypatch):
    import pair_judge

    answers = iter(["A", "B", "A", "A", "B", "tie"])
    monkeypatch.setattr(pair_judge, "ask", lambda port, chat, a, b: next(answers))
    # A then (swapped) B: the same line both times.
    assert pair_judge.verdict(0, "chat", "x", "y") == "A"
    # A both times: it followed the position, not the line.
    assert pair_judge.verdict(0, "chat", "x", "y") == "tie"
    assert pair_judge.verdict(0, "chat", "x", "y") == "tie"


def test_the_pair_judge_proves_itself_on_broken_lines():
    import random

    import pair_judge

    pairs = [{"n": 1, "chat": "甲", "A": "今天打得不錯喔真的", "B": "b1"},
             {"n": 2, "chat": "乙", "A": "短", "B": "b2"}]
    trials = pair_judge.broken(pairs, random.Random(1))
    assert ("甲", "今天打得不錯喔真的", "b2", "another chat's line") in trials
    assert ("甲", "今天打得不錯喔真的", "今天打得", "cut short") in trials
    assert all(intact != bad for _, intact, bad, _ in trials)
