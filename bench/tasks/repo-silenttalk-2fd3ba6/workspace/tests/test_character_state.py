from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from silenttalk.copilot import llm
from silenttalk.copilot.character_state import (
    CharacterState,
    CharacterStateStore,
    Concern,
    merge_state,
)
from silenttalk.copilot.llm import LocalReplyModel
from silenttalk.copilot.persona import PRESET_PERSONAS
from silenttalk.copilot.types import ConversationTurn

LIFE = CharacterState(
    concerns=(
        Concern("想揪大家週六打通宵"),
        Concern("新滑鼠還沒拆", "told"),
        Concern("想換一張顯卡", "done", "終於買了"),
    ),
    recent="這週公司一直加班",
    mood="有點累",
    views=("恐怖片我真的不行",),
    feelings=(("阿明", "最近常加班，有點心疼"),),
)


def test_state_survives_a_restart_and_can_be_cleared(tmp_path: Path) -> None:
    store = CharacterStateStore(tmp_path / "character_state.json")
    store.save("carry", LIFE)
    loaded = store.load("carry")

    assert loaded.concerns == LIFE.concerns
    assert loaded.views == LIFE.views and loaded.feelings == LIFE.feelings
    assert loaded.updated  # stamped on save
    assert store.load("someone-else").is_empty
    store.clear("carry")
    assert store.load("carry").is_empty


def test_nothing_that_looks_like_contact_details_is_kept(tmp_path: Path) -> None:
    store = CharacterStateStore(tmp_path / "character_state.json")
    store.save(
        "carry",
        CharacterState(
            concerns=(Concern("打給 0912-345-678 問看看"), Concern("週末去看海")),
            views=("看 https://example.com 那篇",),
            feelings=(("小雨", "她的信箱是 rain@example.com"), ("阿傑", "很講義氣")),
        ),
    )
    loaded = store.load("carry")

    assert [c.text for c in loaded.concerns] == ["週末去看海"]
    assert loaded.views == ()
    assert loaded.feelings == (("阿傑", "很講義氣"),)


def test_the_prompt_shows_its_life_and_marks_what_it_already_said() -> None:
    now = datetime(2026, 9, 28, 21, 0)
    block = LIFE.prompt_block(now)

    assert "你最近：這週公司一直加班" in block
    assert "- 想揪大家週六打通宵\n" in block
    assert "- 新滑鼠還沒拆（說過了）" in block
    assert "想換一張顯卡（達成了）" in block
    assert "恐怖片我真的不行" in block
    assert "阿明：最近常加班，有點心疼" in block


def test_a_mood_passes_and_a_long_absence_is_noticed() -> None:
    state = merge_state(CharacterState(), LIFE, now=datetime(2026, 9, 20, 22, 0))
    block = state.prompt_block(datetime(2026, 9, 28, 21, 0))

    assert "上次跟大家聊天是 7 天前" in block
    assert "有點累" not in block


def test_the_update_moves_concerns_on_in_its_own_words() -> None:
    raw = """進度|1|done|約成了，週六大家都會上線
進度|9|done|沒有這一件
新心事|週六前要先把零食買好
看法|通宵還是要有人顧著時間
感覺|小雨|每次都最早答應，很講義氣
心情|很期待"""
    state = llm._parse_state_update(raw, LIFE)

    concerns = {c.text: c for c in state.concerns}
    assert concerns["想揪大家週六打通宵"].status == "done"
    assert concerns["想揪大家週六打通宵"].note == "約成了，週六大家都會上線"
    assert concerns["新滑鼠還沒拆"].status == "told"  # untouched
    assert concerns["週六前要先把零食買好"].status == "open"
    assert state.views == ("通宵還是要有人顧著時間",)
    assert state.feelings == (("小雨", "每次都最早答應，很講義氣"),)
    assert state.mood == "很期待"
    merged = merge_state(LIFE, state)
    assert merged.recent == "這週公司一直加班"  # not mentioned: kept
    assert "恐怖片我真的不行" in merged.views and dict(merged.feelings)["阿明"]


def test_off_is_the_measured_prompt_and_light_adds_its_life_and_the_clock() -> None:
    turns = [ConversationTurn(2, "阿明", "今天打嗎")]
    off = LocalReplyModel(Path("unused.gguf"))
    light = LocalReplyModel(Path("unused.gguf"), memory="light")
    for model in (off, light):
        model.set_persona(PRESET_PERSONAS[0])
        model.set_state(LIFE)

    system = off._build_turn_system_prompt(PRESET_PERSONAS[0], "called")
    assert (
        "你今天心裡掛著的事（聊天時接得上就自然帶到，冷場時可以拿來開話題，不要硬塞）：\n"
        "- 想揪大家週六打通宵\n- 新滑鼠還沒拆\n"
    ) in system
    assert "【你自己的生活】" not in system and "加班" not in system
    assert "現在是" not in off.character_messages(turns=turns, addressed=True)[-1]["content"]

    system = light._build_turn_system_prompt(PRESET_PERSONAS[0], "called")
    assert "【你自己的生活】" in system and "你最近：這週公司一直加班" in system
    assert "現在是星期" in light.character_messages(turns=turns, addressed=True)[-1]["content"]


def test_new_concerns_follow_on_and_do_not_repeat() -> None:
    model = LocalReplyModel(Path("unused.gguf"), memory="light")
    model.set_persona(PRESET_PERSONAS[0])
    asked: list[str] = []

    def fake(messages, on_lines=None, max_tokens=240, character=False, temperature=0.65):
        asked.append(messages[0]["content"] + messages[-1]["content"])
        return "想揪大家週六打通宵啦\n1. 想把新鍵盤的燈調好\n下班想吃一碗牛肉麵"

    model._request_messages = fake  # type: ignore[method-assign]
    state = asyncio.run(model.fill_concerns(LIFE))

    texts = [c.text for c in state.active_concerns()]
    assert texts == ["想揪大家週六打通宵", "新滑鼠還沒拆", "想把新鍵盤的燈調好", "下班想吃一碗牛肉麵"]
    assert "這週公司一直加班" in asked[0]  # it knows its recent life
    assert "再用第一人稱寫 2 件" in asked[0]


def test_updating_reads_the_chat_and_keeps_the_rest() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(PRESET_PERSONAS[0])
    model.set_state(LIFE)
    seen: list[str] = []

    def fake(messages, on_lines=None, max_tokens=240, character=False, temperature=0.65):
        seen.append(messages[-1]["content"])
        return "進度|2|progress|拆了，手感比舊的好"

    model._request_messages = fake  # type: ignore[method-assign]
    turns = [ConversationTurn(2, "阿明", "你滑鼠拆了沒"), ConversationTurn(1, "凱哥", "拆了啊", is_self=True)]
    state = asyncio.run(model.update_state(turns))

    assert "阿明：你滑鼠拆了沒" in seen[0] and "你（凱哥）：拆了啊" in seen[0]
    assert "2. 新滑鼠還沒拆（說過了）" in seen[0]
    assert {c.text: c.status for c in state.concerns}["新滑鼠還沒拆"] == "progress"
    assert state.recent == LIFE.recent


def test_now_in_words() -> None:
    assert llm._now_words(datetime(2026, 9, 25, 23, 40)) == "星期五晚上 23:40"
    assert llm._now_words(datetime(2026, 9, 27, 2, 5)) == "星期日半夜 02:05"
    assert llm._now_words(datetime.now() + timedelta(0)).startswith("星期")


def test_memory_levels_are_the_same_in_config_and_model() -> None:
    from silenttalk.config import MEMORY_LEVEL_CHOICES, MEMORY_LEVEL_NAMES

    assert llm.MEMORY_LEVELS == MEMORY_LEVEL_NAMES
    assert tuple(key for key, _ in MEMORY_LEVEL_CHOICES) == MEMORY_LEVEL_NAMES


def test_the_same_worry_in_other_words_is_kept_once() -> None:
    state = CharacterState(
        concerns=(
            Concern("阿凱今天中午又偷偷把我的能量飲料喝光了，該找他算算帳"),
            Concern("阿凱今天中午又偷喝我的能量飲料，該找他算算帳", "done", "算完帳了"),
            Concern("想把新出的戰場攻略傳給隊友"),
        )
    ).normalized()

    texts = [c.text for c in state.concerns]
    assert len(texts) == 2
    assert state.concerns[-1].status == "done" or state.finished_concerns()
