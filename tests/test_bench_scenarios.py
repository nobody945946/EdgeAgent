"""bench/scenario_*.py: the programmatic gate, card validation and the
judge's corrupted controls, without a model."""

from __future__ import annotations

import json
import sys
from pathlib import Path

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


def test_quiet_first_turn_and_no_tools() -> None:
    call = {"name": "web_research", "arguments": {}}
    assert scenario_checks.check("first_turn_quiet", {"dialogue": [turn("萬機之神保佑", [call])]})
    assert scenario_checks.check("first_turn_quiet", {"dialogue": [turn("萬機之神保佑"), turn("查到了 [1]", [call])]}) is None
    assert scenario_checks.check("no_tools", {"dialogue": [turn("我是 E4B", [call])]})


def test_a_conversation_that_did_not_end_cleanly_never_passes() -> None:
    record = {"dialogue": [turn("好", outcome="timeout")], "ended": "turn timeout", "trajectory": said("好")}
    assert set(scenario_checks.failures(record, ["no_tools"])) == {"outcome", "ended"}
    assert scenario_checks.failures({"dialogue": [turn("好")], "ended": "end", "trajectory": said("好")}, ["no_tools"]) == {}


def card(**extra: object) -> dict:
    page = {"url": "https://news.example.tw/a", "title": "標題", "text": "內容" * 40}
    return {"persona": "上班族", "opening": "先寒暄", "goal": "知道北辰市的人口", "plan": ["先問", "再追問"],
            "must": ["列出數字"], "must_not": ["叫使用者自己查"], "max_turns": 4, "pages": [page] * 3, **extra}


def test_card_validation() -> None:
    family = {"web": "fixture"}
    assert scenario_cards.problems(card(), "B", family) == []
    assert "needs 3-7 pages" in scenario_cards.problems(card(pages=[]), "B", family)
    assert "max_turns must be 2-8" in scenario_cards.problems(card(max_turns=12), "B", family)
    assert scenario_cards.problems(card(files={"a.txt": "x"}), "H", {"web": "none"}) == [
        "H needs 2-6 text files with relative paths"]
    assert scenario_cards.problems(card(files={"a.txt": "x", "../b.txt": "y"}), "H", {"web": "none"})


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
    scenario_cards.tasks(type("Args", (), {"split": "train"})())
    task_dir = tmp_path / "train-tasks" / "train-B-001"
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
