import json
from pathlib import Path

import pytest

from silenttalk.copilot import llm
from silenttalk.copilot.llm import LocalLLMError, LocalReplyModel
from silenttalk.copilot.persona import PRESET_PERSONAS
from silenttalk.copilot.types import ConversationTurn


def test_options_parser_reads_the_thought_and_four_lanes() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        """<think>不要顯示這段</think>
心裡|（阿傑又想硬衝，我覺得先丟煙比較穩）
接話|可以啊，丟完煙再進比較穩。
追問|你說的直接衝是走哪條路？
表態|我倒覺得先守一下比較好。
玩笑|再衝一次我們就可以直接改行送頭了。""",
        "A",
        "要不要直接衝？",
    )

    assert parsed.thought == "阿傑又想硬衝，我覺得先丟煙比較穩"
    assert parsed.agree == "可以啊，丟完煙再進比較穩。"
    assert parsed.ask == "你說的直接衝是走哪條路？"
    assert parsed.disagree == "我倒覺得先守一下比較好。"
    assert parsed.humor == "再衝一次我們就可以直接改行送頭了。"


def test_parser_tolerates_small_model_decoration() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        """1. **接話**｜好，那我跟你一起。
- 追問: 你要幾點開始？
表態：「我覺得不太行」
AGREE|舊的代號也認得""",
        "A",
        "我們等等開",
    )

    assert parsed.agree == "舊的代號也認得"
    assert parsed.ask == "你要幾點開始？"
    assert parsed.disagree == "我覺得不太行"
    assert parsed.humor == ""


def test_parser_drops_echoes_of_the_line_or_the_thought() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        """心裡|阿豪說公司在裁員，我有點擔心他
接話|阿豪說公司在裁員，我有點擔心他。
追問|...
表態|要不要直接衝
玩笑|先守一下吧""",
        "A",
        "要不要直接衝",
    )

    assert parsed.thought
    assert parsed.agree == ""
    assert parsed.ask == ""
    assert parsed.disagree == ""
    assert parsed.humor == "先守一下吧"


def test_joke_lane_can_abstain_instead_of_forcing_a_bad_joke() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        """接話|好，我知道了。
表態|我想法不太一樣。
玩笑|SKIP""",
        "B",
        "這件事先這樣。",
    )

    assert parsed.agree
    assert parsed.disagree
    assert parsed.humor == ""


def test_reply_length_is_hard_capped() -> None:
    model = LocalReplyModel(Path("unused.gguf"), max_reply_chars=24)
    parsed = model._parse_suggestions("接話|" + ("很" * 100), "A", "test")

    assert len(parsed.agree) == 25
    assert parsed.agree.endswith("…")


def test_simplified_model_output_is_shown_in_traditional() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        "心裡|这个视频我看过\n接話|那个视频超好笑", "A", "昨天那部片"
    )

    assert parsed.thought == "這個影片我看過"
    assert parsed.agree == "那個影片超好笑"


def test_stream_reports_each_completed_line() -> None:
    from silenttalk.copilot.llm import consume_stream

    pieces = ["AGREE|好", "啊\nASK|真的", "嗎？\nDISAG", "REE|不要\nHUMOR|笑死"]
    events = [b": keep-alive\n"] + [
        ("data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n").encode()
        for p in pieces
    ] + [b"data: [DONE]\n"]
    seen: list[str] = []

    text = consume_stream(events, seen.append)

    assert text == "AGREE|好啊\nASK|真的嗎？\nDISAGREE|不要\nHUMOR|笑死"
    assert seen == ["AGREE|好啊", "AGREE|好啊\nASK|真的嗎？", "AGREE|好啊\nASK|真的嗎？\nDISAGREE|不要"]


def test_ask_lane_gets_a_question_mark_for_tts() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    parsed = model._parse_suggestions(
        "接話|好啊\n追問|你買錯的是哪一款\n表態|不要吧！\n玩笑|笑死", "Ken", "退款"
    )

    assert parsed.ask == "你買錯的是哪一款？"
    assert parsed.disagree == "不要吧！"
    assert parsed.humor == "笑死"


def test_options_are_asked_on_the_end_of_the_character_chat() -> None:
    """Same chat as character mode, so the prompt cache holds from round to
    round; the thought comes first so the four lanes share it."""
    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(PRESET_PERSONAS[0])
    turns = _chat(CHAT)
    messages = model.option_messages(turns=turns)
    chat = model.character_messages(turns=turns, engagement="options", gate=True)

    assert messages[:-1] == chat[:-1]
    assert messages[-1]["content"].startswith(chat[-1]["content"] + "\n\n（記住：你是凱哥")
    ask = messages[-1]["content"]
    assert ask.index("心裡|") < ask.index("接話|") < ask.index("追問|")
    assert ask.index("追問|") < ask.index("表態|") < ask.index("玩笑|")
    assert "不要輸出 SKIP" in messages[0]["content"]
    # Next round: everything but the tail is unchanged.
    later = model.option_messages(turns=turns + [ConversationTurn(2, "阿傑", "再一場")])
    assert later[:-1] == messages[:-1]
    assert later[-1]["content"].startswith(chat[-1]["content"])


def test_the_last_thought_carries_into_the_next_round() -> None:
    import asyncio

    model = LocalReplyModel(Path("unused.gguf"))
    model._request_messages = lambda *a, **k: "心裡|想先丟煙\n接話|丟完煙再進"  # type: ignore[method-assign]
    turns = _chat(CHAT)

    assert "剛才心裡想" not in model.option_messages(turns=turns)[-1]["content"]
    asyncio.run(model.suggest(turns=turns, trigger_speaker="阿傑", trigger_text="對啊打野一直送"))
    assert "（你剛才心裡想的是：想先丟煙）" in model.option_messages(turns=turns)[-1]["content"]
    # Another character starts from its own head.
    model.set_persona(PRESET_PERSONAS[0])
    assert "剛才心裡想" not in model.option_messages(turns=turns)[-1]["content"]


def test_options_after_your_own_line_get_their_own_request_turn() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    messages = model.option_messages(turns=_chat(CHAT[:4]))

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"].startswith("（換你說話了")


def test_streamed_options_show_the_thought_then_each_lane() -> None:
    import asyncio

    model = LocalReplyModel(Path("unused.gguf"))
    text = "心裡|想先丟煙\n接話|丟完煙再進\n追問|誰有煙？\n表態|硬衝會被架\n玩笑|我的煙比我還準"

    def fake(messages, on_lines=None, max_tokens=240, character=False):
        lines = text.split("\n")
        for n in range(1, len(lines)):
            on_lines("\n".join(lines[:n]))
        return text

    model._request_messages = fake  # type: ignore[method-assign]
    seen = []
    final = asyncio.run(
        model.suggest(
            turns=_chat(CHAT), trigger_speaker="阿傑", trigger_text="對啊打野一直送",
            on_partial=seen.append,
        )
    )

    assert [s.thought for s in seen] == ["想先丟煙"] * 4
    assert [s.has_any() for s in seen] == [False, True, True, True]
    assert all(s.partial for s in seen) and not final.partial
    assert final.humor == "我的煙比我還準"


def test_character_line_parsing() -> None:
    from silenttalk.copilot.persona import PRESET_PERSONAS

    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(PRESET_PERSONAS[0])

    assert model._parse_character_line("凱哥：穩啦，下波我先進\n多的", "衝B") == "穩啦，下波我先進"
    assert model._parse_character_line("SAY|好啊走", "衝B") == "好啊走"
    assert model._parse_character_line("說真的：我覺得先丟煙", "衝B") == "說真的：我覺得先丟煙"
    assert model._parse_character_line("SKIP", "衝B") == ""
    assert model._parse_character_line("衝B", "衝B") == ""


def test_character_keeps_the_act_by_default_and_can_be_told_to_own_up() -> None:
    carry = PRESET_PERSONAS[0]
    in_character = LocalReplyModel(Path("unused.gguf"))._build_turn_system_prompt(
        carry, "called"
    )
    admitting = LocalReplyModel(
        Path("unused.gguf"), ai_honesty="admit"
    )._build_turn_system_prompt(carry, "called")

    assert "不要承認自己是程式" in in_character
    assert "誠實承認你是 AI 角色" not in in_character
    assert "誠實承認你是 AI 角色" in admitting
    assert "名字：凱哥" in in_character


def test_the_nudge_follows_the_chosen_policy() -> None:
    turns = [ConversationTurn(2, "Ken", "欸你是不是AI啊")]
    in_character = LocalReplyModel(Path("unused.gguf"))
    admitting = LocalReplyModel(Path("unused.gguf"), ai_honesty="admit")
    for model in (in_character, admitting):
        model.set_persona(PRESET_PERSONAS[0])

    # Staying in character is what the model does anyway (A/B: 0/18 broke
    # character with or without a nudge), so the default path adds nothing.
    assert "承認" not in in_character.character_messages(turns=turns, addressed=True)[-1]["content"]
    assert "要老實承認" in admitting.character_messages(turns=turns, addressed=True)[-1]["content"]


def test_character_is_told_not_to_repeat_a_recent_catchphrase() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(PRESET_PERSONAS[2])  # 黑焰
    turns = [ConversationTurn(2, "Ken", "打團嗎")]

    first = model.character_messages(turns=turns, addressed=True)[-1]["content"]
    model._recent_character_lines.append("哼，吾之右手尚未解放")
    second = model.character_messages(turns=turns, addressed=True)[-1]["content"]

    assert "你最近已經說過" not in first
    assert "吾之右手" in second.split("你最近已經說過")[1]
    assert "口頭禪偶爾用" not in first


def test_llama_server_found_in_winget_package_without_links_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = (
        tmp_path
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe"
    )
    package.mkdir(parents=True)
    (package / "llama-server.exe").write_bytes(b"")
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert llm.find_llama_server() == str(package / "llama-server.exe")


def test_missing_llama_server_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    with pytest.raises(LocalLLMError):
        llm.find_llama_server()


def _chat(lines: list[tuple[str, str]]) -> list[ConversationTurn]:
    return [
        ConversationTurn(2, name, text, is_self=name == "凱哥") for name, text in lines
    ]


CHAT = [
    ("阿傑", "欸今天要打幾場"),
    ("小雨", "我等等要先吃飯"),
    ("小雨", "凱哥你要上嗎"),
    ("凱哥", "穩啦等我五分鐘"),
    ("Ken", "昨天那把有夠雷"),
    ("阿傑", "對啊打野一直送"),
]


def test_character_chat_only_ever_grows_at_the_end() -> None:
    """Each new line leaves every earlier message byte-identical, so the
    llama-server prompt cache only prefills the new line."""
    model = LocalReplyModel(Path("unused.gguf"))  # blank persona: no hints
    turns = _chat(CHAT)
    for n in range(1, len(turns)):
        before = model.character_messages(turns=turns[:n])
        after = model.character_messages(turns=turns[: n + 1])
        assert after[: len(before) - 1] == before[:-1]
        assert after[len(before) - 1]["content"].startswith(before[-1]["content"])


def test_character_chat_roles() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    messages = model.character_messages(turns=_chat(CHAT), summary="在聊晚餐")

    # Roles alternate, as chat templates require: the summary and the lines
    # after it share one user turn.
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == (
        "（這場對話稍早的重點：在聊晚餐）\n"
        "阿傑：欸今天要打幾場\n小雨：我等等要先吃飯\n小雨：凱哥你要上嗎"
    )
    assert messages[2]["content"] == "穩啦等我五分鐘"
    assert messages[3]["content"] == "Ken：昨天那把有夠雷\n阿傑：對啊打野一直送"


def test_character_chat_opening_with_its_own_line_starts_with_a_user_turn() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    messages = model.character_messages(turns=_chat([("凱哥", "安安"), ("阿傑", "嗨")]))

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_hints_ride_only_on_the_newest_message() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(PRESET_PERSONAS[0])
    messages = model.character_messages(turns=_chat(CHAT), addressed=True)

    assert "（最後這句是在叫你，要回應）" in messages[-1]["content"]
    assert "記住：你是凱哥" in messages[-1]["content"]
    assert all("記住" not in m["content"] for m in messages[1:-1])
    assert "凱哥" in messages[0]["content"]


def test_engagement_setting_changes_only_the_system_prompt() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    called = model.character_messages(turns=_chat(CHAT), engagement="called")
    active = model.character_messages(turns=_chat(CHAT), engagement="active")

    assert called[0] != active[0]
    assert called[1:] == active[1:]


def test_long_chats_drop_old_turns_a_batch_at_a_time() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    turns = [ConversationTurn(2, "阿傑", f"第{n}句") for n in range(60)]

    first_kept = []
    for n in range(41, 61):
        content = model.character_messages(turns=turns[:n])[1]["content"]
        first_kept.append(content.split("\n")[0])
    # 41..56 turns share one starting point; only crossing a batch moves it.
    assert set(first_kept[:16]) == {"阿傑：第16句"}
    assert set(first_kept[16:]) == {"阿傑：第32句"}


def test_nothing_to_answer_after_the_characters_own_line() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    messages = model.character_messages(turns=_chat(CHAT[:4]))

    assert messages[-1]["role"] == "assistant"


class _Scripted(LocalReplyModel):
    """Answers the gate and the line from a script, recording each request."""

    def __init__(self, gate: str, line: str = "穩啦") -> None:
        super().__init__(Path("unused.gguf"))
        self.set_persona(PRESET_PERSONAS[0])
        self.gate, self.line = gate, line
        self.requests: list[tuple[str, list[dict[str, str]]]] = []

    def _request_gate(self, messages, character=True):
        self.requests.append(("gate", messages))
        # A list scripts one answer per question, in order.
        return self.gate.pop(0) if isinstance(self.gate, list) else self.gate

    def _request_messages(self, messages, on_lines=None, max_tokens=240, character=False):
        self.requests.append(("line", messages))
        return self.line


@pytest.mark.asyncio
async def test_gate_no_means_silence_without_writing_a_line() -> None:
    model = _Scripted(gate="否")
    assert await model.character_turn(turns=_chat(CHAT)) == ""
    assert [kind for kind, _ in model.requests] == ["gate"]
    question = model.requests[0][1][-1]["content"]
    assert question.endswith("只回答 是 或 否）") and "記住" not in question


@pytest.mark.asyncio
async def test_gate_yes_writes_a_line_told_to_answer() -> None:
    model = _Scripted(gate="是")
    assert await model.character_turn(turns=_chat(CHAT)) == "穩啦"
    assert [kind for kind, _ in model.requests] == ["gate", "line"]
    gate, line = model.requests[0][1], model.requests[1][1]
    assert gate[:-1] == line[:-1]  # same cached chat, only the tail differs
    assert "（最後這句是在叫你，要回應）" in line[-1]["content"]


@pytest.mark.asyncio
async def test_a_discord_mention_skips_the_gate() -> None:
    model = _Scripted(gate="否")
    assert await model.character_turn(turns=_chat(CHAT), addressed=True) == "穩啦"
    assert [kind for kind, _ in model.requests] == ["line"]


@pytest.mark.asyncio
async def test_active_mode_asks_to_join_in_only_after_a_quiet_stretch() -> None:
    quiet = _chat(CHAT[:4] + [("Ken", "昨天那把有夠雷"), ("阿傑", "對啊"), ("Ken", "笑死"), ("阿傑", "好啦")])
    just_spoke = _chat(CHAT)  # the character spoke two lines ago

    asked = _Scripted(gate="否")
    await asked.character_turn(turns=quiet, engagement="active")
    questions = [m[-1]["content"] for _, m in asked.requests]
    assert len(questions) == 2
    assert "是不是在對你說話" in questions[0] and "開口自然嗎" in questions[1]

    held = _Scripted(gate="否")
    await held.character_turn(turns=just_spoke, engagement="active")
    assert len(held.requests) == 1

    called = _Scripted(gate="否")
    await called.character_turn(turns=quiet, engagement="called")
    assert len(called.requests) == 1


@pytest.mark.asyncio
async def test_gate_and_line_share_the_system_prompt_in_active_mode() -> None:
    """Same system prompt for both requests, so the second one reuses the
    first one's cache, and the gate judges by the chosen engagement."""
    model = _Scripted(gate="是")
    await model.character_turn(turns=_chat(CHAT), engagement="active")
    gate, line = model.requests[0][1], model.requests[1][1]
    assert gate[0] == line[0]
    assert "話題你有想法或有梗" in gate[0]["content"]


def test_relationships_are_in_the_character_system_prompt() -> None:
    """A "wife" persona has to know which speaker is her husband."""
    from dataclasses import replace

    from silenttalk.copilot.persona import Relationship

    model = LocalReplyModel(Path("unused.gguf"))
    model.set_persona(
        replace(PRESET_PERSONAS[1], relationships=(Relationship("阿明", "我老公，結婚三年"),))
    )
    system = model.character_messages(turns=_chat(CHAT))[0]["content"]
    assert "- 阿明：我老公，結婚三年" in system


def test_a_line_that_mostly_repeats_what_was_said_is_dropped() -> None:
    model = LocalReplyModel(Path("unused.gguf"))
    assert model._parse_character_line("有啊，週末去看電影", "有啊我們週末去看電影") == ""
    # Sharing a phrase is not echoing.
    assert model._parse_character_line("好啦好啦，油錢我們分吧", "讚喔 那油錢我們分")
    assert model._parse_character_line("穩啦", "穩") == "穩啦"


def test_only_character_requests_use_the_lora() -> None:
    model = LocalReplyModel(Path("unused.gguf"), lora_path=Path("adapter.gguf"))
    assert model._lora_field(True) == {}  # not loaded until the server starts with it
    model._lora_loaded = True
    assert model._lora_field(True) == {"lora": [{"id": 0, "scale": 1.0}]}
    assert model._lora_field(False) == {"lora": [{"id": 0, "scale": 0.0}]}


@pytest.mark.asyncio
async def test_joining_in_is_not_told_it_was_called() -> None:
    quiet = _chat(CHAT[:4] + [("Ken", "昨天那把有夠雷"), ("阿傑", "對啊"), ("Ken", "笑死"), ("阿傑", "好啦")])
    model = _Scripted(gate=["否", "是"])
    assert await model.character_turn(turns=quiet, engagement="active") == "穩啦"
    line = model.requests[-1][1][-1]["content"]
    assert "（沒有人叫你，你決定自然地插一句話）" in line
    assert "在叫你" not in line
