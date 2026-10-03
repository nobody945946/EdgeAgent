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
