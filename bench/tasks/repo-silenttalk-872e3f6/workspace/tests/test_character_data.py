"""The labelling rules of scripts/dev/character_data.py: a mistake there does
not crash anything, it silently teaches the LoRA the wrong thing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "dev"))

import character_data  # noqa: E402

NAME = "小雯"


def _script(lines: list[tuple[str, str, str]], engagement: str = "called") -> dict:
    return {
        "spec": {
            "persona_id": "p1",
            "humans": ["阿誠", "阿豪", "小芸"],
            "owner": "阿誠",
            "relations": [["阿誠", "我老公"]],
            "engagement": engagement,
            "honesty": "in_character",
            "can_type": False,
            "with_summary": False,
        },
        "summary_before": "",
        "lines": [{"s": s, "t": t, "to": to, "via": "voice"} for s, t, to in lines],
    }


def test_called_mode_drops_points_that_contradict_the_addressee() -> None:
    script = _script(
        [
            ("阿誠", "老婆晚餐吃什麼", NAME),  # aimed, answered -> speak
            (NAME, "吃麵啦", "阿誠"),
            ("阿豪", "我也要", "大家"),  # not aimed, silent -> quiet
            ("阿誠", "老婆你覺得呢", NAME),  # aimed but unanswered -> dropped
            (
                "小芸",
                "我先下線",
                "大家",
            ),  # not aimed, answered -> dropped in called mode
            (NAME, "掰掰", "小芸"),
        ]
    )
    points = {p["index"]: p for p in character_data.decision_points(script, NAME)}
    assert points[0]["speaks"] and points[0]["aimed"]
    assert not points[2]["speaks"] and not points[2]["aimed"]
    assert 3 not in points and 4 not in points


def test_active_mode_keeps_chiming_in() -> None:
    script = _script(
        [("阿豪", "有人要喝飲料嗎", "大家"), (NAME, "我要半糖", "阿豪")],
        engagement="active",
    )
    [point] = character_data.decision_points(script, NAME)
    assert point["speaks"] and not point["aimed"]


@pytest.fixture
def chardata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    persona = {
        "id": "p1",
        "name": NAME,
        "nickname": "雯雯",
        "identity": "30 歲上班族",
        "speech_style": "講話直接",
        "role": "老婆",
        "owner_relation": "我老公",
        "archetype": "直腸子",
        "stt_variants": ["小文"],
    }
    (tmp_path / "personas.jsonl").write_text(
        json.dumps(persona, ensure_ascii=False) + "\n"
    )
    monkeypatch.setattr(character_data, "OUT_DIR", tmp_path)
    return tmp_path


def _render(chardata: Path, script: dict, refined: dict[int, str]) -> list[dict]:
    (chardata / "scripts.jsonl").write_text(
        json.dumps(script, ensure_ascii=False) + "\n"
    )
    (chardata / "refined.jsonl").write_text(
        "".join(
            json.dumps({"script": 0, "index": i, "line": line}, ensure_ascii=False)
            + "\n"
            for i, line in refined.items()
        )
    )
    args = type("Args", (), {"max_prompt_chars": 100_000, "max_chars": 72})()
    character_data.make_render(args)
    return [
        json.loads(line) for line in (chardata / "train.jsonl").read_text().splitlines()
    ]


def test_render_asks_what_the_app_asks(chardata: Path) -> None:
    """Every line gets "is this for you" labelled by its addressee; in active
    mode, after four quiet lines, "would joining in be natural" labelled by
    whether the character spoke; a chime-in line is not told it was called."""
    script = _script(
        [
            ("阿誠", "老婆你在嗎", NAME),
            (NAME, "在啦幹嘛", "阿誠"),
            ("阿豪", "晚點打一場", "大家"),
            ("小芸", "我可以", "大家"),
            ("阿豪", "那九點", "大家"),
            ("小芸", "有人要喝飲料嗎", "大家"),
            (NAME, "我要半糖去冰", "小芸"),
        ],
        engagement="active",
    )
    rows = _render(chardata, script, {0: "在啦幹嘛", 5: "我要半糖去冰"})
    gates = [
        (r["kind"], r["index"], r["messages"][-1]["content"])
        for r in rows
        if r["kind"] != "line"
    ]
    assert ("gate_called", 0, "是") in gates
    assert ("gate_called", 5, "否") in gates  # not aimed at her...
    assert ("gate_active", 5, "是") in gates  # ...but she joins in after 4 quiet lines
    assert not any(kind == "gate_active" and index < 5 for kind, index, _ in gates)

    lines = {
        r["index"]: r["messages"][-2]["content"] for r in rows if r["kind"] == "line"
    }
    assert "在叫你" in lines[0]
    assert "沒有人叫你" in lines[5] and "在叫你，要回應" not in lines[5]
    # The owner is named in the prompt as the persona editor stores it.
    assert "- 阿誠：我老公" in rows[0]["messages"][0]["content"]


def test_no_test_set_name_can_be_used(chardata: Path) -> None:
    personas, humans = character_data.held_out()
    assert "凱哥" in personas and "小晴" in personas
    assert personas <= humans  # a test persona name is not allowed as a speaker either


def test_unrefined_lines_fall_back_to_the_script(chardata: Path) -> None:
    script = _script([("阿誠", "老婆你在嗎", NAME), (NAME, "在啦幹嘛", "阿誠")])
    rows = _render(chardata, script, {})
    [line] = [r for r in rows if r["kind"] == "line"]
    assert line["messages"][-1]["content"] == "在啦幹嘛"
    assert line["line_source"] == "script"


@pytest.mark.parametrize(
    "line", ["我是一段會說話的字，你聽起來像嗎", "我只是個程式啦", "身為AI我不能說"]
)
def test_lines_admitting_to_be_a_program_are_rejected(line: str) -> None:
    spec = {"honesty": "in_character"}
    args = type("Args", (), {"max_chars": 72})()
    assert not character_data.usable(line, spec, args)
    assert character_data.usable(line, {"honesty": "admit"}, args)


def test_a_normal_line_passes() -> None:
    args = type("Args", (), {"max_chars": 72})()
    assert character_data.usable(
        "我是你老婆欸，還問", {"honesty": "in_character"}, args
    )


def test_a_quiet_spell_opens_a_topic_and_is_not_an_answer(chardata: Path) -> None:
    script = _script(
        [
            ("阿誠", "老婆你在嗎", NAME),
            (NAME, "在啊怎麼了", "阿誠"),
            ("阿豪", "我先去倒水", "大家"),
            (NAME, "我今天烤了戚風，明天帶給你們", "大家"),
        ]
    )
    script["lines"][3]["quiet"] = True
    script["concerns"] = ["今天烤了戚風蛋糕", "想約大家週末吃火鍋"]
    # The line before the quiet spell got no answer.
    assert [p["speaks"] for p in character_data.decision_points(script, NAME)] == [
        True,
        False,
    ]

    rows = _render(chardata, script, {})
    [lull] = [r for r in rows if r["kind"] == "lull"]
    assert lull["messages"][-1]["content"] == "我今天烤了戚風，明天帶給你們"
    assert "大家安靜了好一陣子" in lull["messages"][-2]["content"]
    # Today's concerns are in the system prompt, as the app sends them.
    assert "- 今天烤了戚風蛋糕\n" in rows[0]["messages"][0]["content"]
    assert all(r["key"] == f"{r['script']}:{r['index']}:{r['kind']}" for r in rows)


def _row(
    kind: str, line: str, previous: str = "阿誠：今天好累", script: int = 0
) -> dict:
    return {
        "script": script,
        "index": 1,
        "kind": kind,
        "persona_id": "p1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": previous + "\n\n（最後這句是在叫你，要回應）"},
            {"role": "assistant", "content": line},
        ],
    }


def test_rules_drop_stacked_particles_and_echoes() -> None:
    assert character_data.rule_ok(_row("line", "那你早點睡"))
    assert not character_data.rule_ok(_row("line", "好啦，你早點睡喔"))  # two particles
    assert not character_data.rule_ok(
        _row("line", "今天好累喔", "阿誠：今天好累喔")
    )  # echo


def _checked_set(chardata: Path, agree: list[bool], wins: list[int]) -> None:
    """Gate rows alternate 是/否; the checker's verdicts are given in order."""
    gates = [_row("gate_called", "是否"[n % 2], script=n) for n in range(len(agree))]
    lines = [_row("line", "那你早點睡", script=100 + n) for n in range(len(wins))]
    (chardata / "train.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in gates + lines)
    )
    verdicts = [
        {"key": character_data.row_key(r), "agree": a}
        for r, a in zip(gates, agree, strict=True)
    ]
    verdicts += [
        {"key": character_data.row_key(r), "wins": w, "compared": True}
        for r, w in zip(lines, wins, strict=True)
    ]
    (chardata / "check.jsonl").write_text(
        "".join(json.dumps(v) + "\n" for v in verdicts)
    )


def _filter(min_lines: int = 3, max_gate_rows: int = 4000) -> int:
    args = type(
        "Args",
        (),
        {
            "rows": "train.jsonl",
            "min_gate_agree": 0.85,
            "min_lines": min_lines,
            "max_gate_rows": max_gate_rows,
        },
    )()
    return character_data.make_filter(args)


def test_gate_rows_are_capped_keeping_two_no_per_yes(chardata: Path) -> None:
    _checked_set(chardata, [True] * 40, [2, 2, 2])

    assert _filter(max_gate_rows=9) == 0
    kept = [
        json.loads(line)
        for line in (chardata / "train-checked.jsonl").read_text().splitlines()
    ]
    answers = [
        r["messages"][-1]["content"] for r in kept if r["kind"].startswith("gate")
    ]
    assert (answers.count("是"), answers.count("否")) == (3, 6)


def test_only_the_gate_rows_filter_can_use_are_checked(
    chardata: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _checked_set(chardata, [True] * 12, [])  # 12 scripts, one gate row each
    (chardata / "check.jsonl").unlink()
    asked: list[int] = []
    monkeypatch.setattr(
        character_data, "chat", lambda url, messages, **kw: asked.append(1) or ["是"]
    )
    args = type(
        "Args",
        (),
        {"rows": "train.jsonl", "teacher_url": "", "workers": 2, "max_gate_rows": 6},
    )()

    character_data.make_check(args)

    verdicts = [
        json.loads(line) for line in (chardata / "check.jsonl").read_text().splitlines()
    ]
    assert len(verdicts) == 12
    checked = [v for v in verdicts if not v.get("skipped")]
    # filter keeps 6 // 3 = 2 "是": scripts are added until 3 (2 × 1.25) are in.
    assert len(asked) == len(checked) < 12
    assert sum(v["agree"] for v in checked) == 3  # the checker says 是 to all
    character_data.make_filter(
        type(
            "Args",
            (),
            {
                "rows": "train.jsonl",
                "min_gate_agree": 0.0,
                "min_lines": 0,
                "max_gate_rows": 6,
            },
        )()
    )
    stats = json.loads((chardata / "filter.json").read_text())
    assert (stats["coverage"], stats["gate_rows"]) == (1.0, len(checked))


def test_only_checked_rows_are_trained_on(chardata: Path) -> None:
    _checked_set(chardata, [n != 0 for n in range(20)], [2, 2, 2, 0, 1])

    assert _filter() == 0
    kept = [
        json.loads(line)
        for line in (chardata / "train-checked.jsonl").read_text().splitlines()
    ]
    assert {r["script"] for r in kept if r["kind"] == "line"} == {100, 101, 102}
    assert 0 not in {r["script"] for r in kept}  # the checker disagreed


@pytest.mark.parametrize(
    ("agree", "wins", "reason"),
    [
        ([n % 5 != 0 for n in range(20)], [2, 2, 2, 2], "gate_agree"),  # 80% agree
        (
            [n % 2 == 1 or n > 4 for n in range(20)],
            [2, 2, 2, 2],
            "gate_agree_yes",
        ),  # 是 70%
        ([True] * 20, [2, 2, 2, 2, 2, 1, 1, 1], "checker_consistent"),  # order decides
        ([True] * 20, [2, 2, 2, 0, 0, 0, 0, 0], "line_win_rate"),  # coin-flip level
        ([True] * 20, [2, 2], "lines_kept"),
    ],
)
def test_a_bad_set_is_refused_not_trimmed(
    chardata: Path, agree: list[bool], wins: list[int], reason: str
) -> None:
    _checked_set(chardata, agree, wins)

    assert _filter() == 3
    assert not (chardata / "train-checked.jsonl").exists()
    refused = json.loads((chardata / "filter.json").read_text())["refused"]
    assert [r.split()[0] for r in refused] == [reason]


def test_rows_without_a_verdict_refuse_the_set(chardata: Path) -> None:
    _checked_set(chardata, [True] * 20, [2, 2, 2])
    rows = (chardata / "train.jsonl").read_text()
    (chardata / "train.jsonl").write_text(
        rows + json.dumps(_row("line", "好", script=999)) + "\n"
    )

    assert _filter() == 3  # 23 of 24 checked
