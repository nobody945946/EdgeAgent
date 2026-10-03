from __future__ import annotations

import asyncio
import collections
import dataclasses
import difflib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from silenttalk.agent_lease import (
    AgentLeaseError,
    acquire_character,
    release_character,
)
from silenttalk.copilot.character import asks_if_ai
from silenttalk.copilot.character_state import (
    STATUSES,
    CharacterState,
    Concern,
    merge_state,
)
from silenttalk.copilot.persona import Persona, blank_persona
from silenttalk.copilot.script import to_taiwan_traditional
from silenttalk.copilot.types import REPLY_LANES, ConversationTurn, ReplySuggestions

_LOG = logging.getLogger(__name__)

# Asked whether it is an AI, the character either steps out of the fiction
# or keeps it. Default is keeping it: a character that announces it is a
# program stops being a character.
_AI_IN_CHARACTER = (
    "你就是這個人，從頭到尾用這個身分講話。有人說你是 AI、機器人或問你是不是真人時，"
    "當成朋友在開你玩笑，用這個角色會有的方式笑著帶過、吐槽或反問回去，不要承認自己是程式，"
    "也不要一本正經地解釋自己不是 AI（那樣反而更可疑）。"
)
_AI_ADMIT = (
    "你是 AI 扮演的角色：平常用角色口吻聊天；有人認真問你是不是 AI、機器人或真人時，"
    "要誠實承認你是 AI 角色（可以用角色口吻說）。"
)

# What the character can actually do, so it never promises the impossible.
# Typing is allowed only when the channel's text chat is wired up.
_VOICE_ONLY = (
    "你只存在於這個語音頻道，只能用嘴巴聊天：不能打字、寫程式、寫文章、翻譯整段文字、"
    "上網查資料、設提醒，也不會記得下次聊天。被要求做這些事時，用這個角色會有的方式回應"
    "（推託、吐槽、給一句口頭建議、叫對方自己來或用別的工具），絕對不要答應或假裝在做你做不到的事。"
)
_CAN_TYPE = (
    "你只存在於這個頻道，可以講話也可以在聊天室打一句短訊息，但你是這個人、不是助理："
    "不寫程式、不寫文章或作業、不翻譯整段文字、不上網查資料、不設提醒，也不會記得下次聊天。"
    "被要求做這些事時，用這個角色會有的方式回應（推託、吐槽、給一句口頭建議、叫對方自己來或用別的工具），"
    "絕對不要答應或假裝在做你做不到的事。"
)

# Character mode as one running chat: the others' lines are user messages,
# the character's own lines are its assistant messages. Nothing before the
# newest line changes between requests, so llama-server only prefills that
# line (RTX 3050 Ti: 93 ms to decide; CPU only: 0.15 s instead of 0.65-1.65 s
# for the old prompt that re-rendered the recent window every time). Whether
# to speak at all is the model's first word: a line, or SKIP.
_TURN_SYSTEM = """你是 Discord 語音頻道裡的{who}，和朋友們一起用語音聊天。你說的話會直接用語音播出來。

你會陸續看到頻道裡大家說的話，格式是「名字：內容」，是語音辨識的結果，可能有錯字、沒有標點，名字也可能被聽成同音字。你自己說過的話就是你之前的回覆。

什麼時候開口：{engagement}

規則：
- 只根據真實對話回應，不得杜撰別人的身分、經歷、情緒或事實。關於你自己，只用人設裡寫到的事，不要編新的經歷。
- {ai_rule}
- {abilities}
- 你不是百科全書：超出你人設會懂的問題，就用這個人真實的程度回答，可以說不知道。
- 台灣繁體中文口語，只說一句話，長度{length}，最長不得超過 {max_chars} 字。
- 像朋友聊天一樣自然接話：附和、追問、吐槽或開玩笑，選這個角色此刻最可能的反應。不要重複別人的話，不要說教，不要每句都叫對方名字。
- 不羞辱、不人身攻擊，不拿外貌、疾病、性別、種族、家庭、收入開玩笑。
{sheet}
每次只輸出一行：你要說的話；這時候不該開口就只輸出 SKIP。不要加引號、名字或解釋。"""

_ENGAGEMENT_RULES = {
    "called": (
        "只有在有人叫你（名字或暱稱）、問你、要你做什麼，或是在回應你剛說的話時才開口。"
        "別人彼此在聊、在講到你（說的是你但不是對你說）、或用「你」在跟別人說話時，一律輸出 SKIP。"
    ),
    "active": (
        "被叫到、被問到、有人回應你時一定要回。其他時候你也是聊天的一份子，"
        "話題你有想法或有梗可以自然插一句，但大部分時候讓別人聊："
        "別人在私下聊、你剛說過話、或沒什麼好補充時輸出 SKIP。"
    ),
}

# Options mode (選項): the same chat as character mode, asked several times
# for a line the character would say; the owner picks one of up to four.
_ENGAGEMENT_RULES["options"] = (
    "每次輪到你，就說一句你現在會說的話，你的主人會從幾句裡挑一句說出口；不要輸出 SKIP。"
)
_OPTIONS_ASK = "（換你說話了：說一句你現在會說的話）"
# A character with nothing going on in its own life only ever reacts: the
# soul judge (turn_eval --judge-soul) read "看不出來" off 8 of 21 test chats
# (know 2.6 / 5). Its life is a CharacterState (character_state.py): a few
# concrete concerns that move on as it talks, views, mood and feelings about
# people, kept between sessions in its own words.
_CONCERNS_WANTED = 4
# character_memory "off" (a 2B model on a 3050 Ti) keeps exactly what was
# measured to work (turn_eval core2 vs shipped: know this person 28 vs 13
# conversations): concerns asked from the persona alone, shown as a plain
# list, no clock in the requests. The richer prompt below (recent life, the
# time of day) lost to it on Gemma 4 E2B: know 10 vs 19 conversations.
_AGENDA_ASK = (
    "用第一人稱寫 4 件你今天心裡掛著的具體小事：今天或最近發生的事、想做的事、在煩的事、在期待的事。"
    "要符合上面的你、具體、有畫面，像真的在過的生活；可以提到你身邊的人。"
    "每件一行、一句話，不要編號，不要解釋。"
)
_AGENDA_HEADER = "你今天心裡掛著的事（聊天時接得上就自然帶到，冷場時可以拿來開話題，不要硬塞）："
_FILL_ASK = (
    "現在是{now}。再用第一人稱寫 {n} 件你心裡掛著的具體小事：最近發生的事、想做的事、"
    "在煩的事、在期待的事。要接著你最近的生活，符合你這個人、具體、有畫面，"
    "可以提到你身邊的人（不是你自己）；不要寫幾點幾分，不要跟上面已經掛著或已經了結的事重複。"
    "每件一行、一句話，不要編號，不要解釋。"
)
_UPDATE_ASK = """下面是你剛剛在 Discord 語音聊天的一段，只用來更新你自己的狀態：
{chat}

你心裡掛著的事：
{concerns}

用你自己的話更新你的狀態，照下面的格式輸出，沒有變化的就不用寫：
進度|編號|told 或 progress 或 done 或 dropped|一句話說現在怎樣
新心事|因為這段聊天，你心裡新掛著的一件事
{full_lines}近況|你最近在忙或發生在你身上的事
心情|你現在的心情，幾個字
told＝你已經跟大家說過這件事；progress＝有進展；done＝已經達成或滿足了；dropped＝不想了、放下了。
規則：只寫你自己的想法和感受，用你自己的話；不要照抄任何人說的話；不要寫別人的電話、地址、帳號這類資料。"""
_UPDATE_FULL_LINES = "看法|你在這段聊天裡說出口的一個看法\n感覺|名字|你現在對這個人的感覺\n"
_UPDATE_CHAT_TURNS = 24
# How much of its life the character carries (config copilot.character_memory):
# off = today's concerns for this session only; light = concerns, recent and
# mood kept between sessions, updated only when the chat is over or long
# quiet; full = also views and feelings about people, updated as it talks.
MEMORY_LEVELS = ("off", "light", "full")
_STATUS_WORDS = {
    "open": "還沒跟大家說過",
    "told": "說過了",
    "progress": "有進展",
    "done": "達成了",
    "dropped": "放下了",
}
_WEEKDAYS = "一二三四五六日"

# After a lull nobody called it, so there is no gate: the character opens a
# topic from its own life, or asks after someone.
_LULL_HINT = (
    "（大家安靜了好一陣子，沒有人在說話。換你開個話題：說說你心裡掛著的一件事，"
    "或問問在場的人最近怎樣。自然一點，一句就好）"
)

# Options are sampled lines, not four fixed intents. Asked for agree / ask /
# disagree / joke in one go, Gemma 4 E2B answered the wrong line, disagreed
# for the sake of it and repeated one trait (laptop test, 2026-09-28: "敷衍、
# 過度跟隨每一句話"); asked to write its thought first and four lines from it,
# it did worse still. Sampling what the character itself would say won the
# blind A/B against the four-intent prompt (options_eval: better 53% vs 42%,
# on-topic 2.94 vs 2.56, usable lines 1.68 vs 1.43 of 4). Each extra sample
# reuses the cached prompt, so it costs only its own ~20 tokens.
_OPTIONS_TEMPERATURE = 1.0
_OPTIONS_TRIES = 7
# Two lines this alike count as one option.
_OPTIONS_ALIKE = 0.6
# Requests the character adapter can be applied to: the two gate questions,
# the character's line, and the options.
LORA_PARTS = ("called", "active", "line", "options")

# Asked on the same chat (same prompt cache) before writing a line, answered
# with one grammar-constrained token. A small model told to "answer or SKIP"
# answered every line (0 SKIPs on 18 test lines, Gemma 4 E2B); asked this
# directly, it can tell a line aimed at it from one merely about it.
_GATE_QUESTION = {
    "called": (
        "（先判斷：最後一句是不是在對你說話？叫你、問你、要你做什麼、回應你剛說的話都算；"
        "別人彼此在聊、只是講到你、或是在跟別人說話都不算。只回答 是 或 否）"
    ),
    "active": (
        "（先判斷：你現在開口自然嗎？有人在對你說話、問大家問題、或話題正好是你會想接的都算；"
        "別人在私下聊、你剛說過話、或你沒什麼好補充都不算。只回答 是 或 否）"
    ),
}
_GATE_GRAMMAR = 'root ::= "是" | "否"'
# "active" mode joins in on its own, but asked "would speaking now be
# natural?" after every line, Gemma 4 E2B said yes to 76% of lines meant for
# others (M1 test set). So every line first gets the "called" question, and
# the join-in question only comes once the character has been quiet this
# many lines: the model judges the moment, the count keeps the pace.
_JOIN_AFTER_QUIET_LINES = 4

# Turns kept verbatim in a character request. Past this the oldest ones are
# dropped a batch at a time, so the prompt prefix (and its KV cache) survives
# most new lines; compaction normally folds them into the summary first.
_TURN_WINDOW = 128
_TURN_DROP_BATCH = 32

# A summary longer than this is no longer a summary; it also has to stay
# small next to the persona in a 32768-token context.
_SUMMARY_CHARS = 640

_END_PUNCTUATION = "？?！!。.…~～」』)）"
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
class LocalLLMError(RuntimeError):
    pass


class LocalReplyModel:
    """Leased inference client for local character replies."""

    def __init__(
        self,
        model_path: Path,
        *,
        port: int = 18088,
        context_size: int = 2048,
        max_reply_chars: int = 72,
        gpu_layers: int = 99,
        request_timeout_seconds: float = 15.0,
        low_vram: bool = True,
        can_type: bool = False,
        ai_honesty: str = "in_character",
        lora_path: Path | None = None,
        lora_uses: Iterable[str] = ("called", "active", "line"),
        memory: str = "off",
        project_dir: Path | None = None,
    ) -> None:
        self._model_path = model_path
        self._lora_path = lora_path
        # Which requests the adapter is applied to (LORA_PARTS): it can help
        # one part and hurt another, so each is switched on only where the
        # eval says it wins.
        self._lora_uses = frozenset(lora_uses)
        # Set once the adapter is on llama-server's command line.
        self._lora_loaded = False
        self._port = port
        self._context_size = context_size
        self._max_reply_chars = max(24, max_reply_chars)
        self._gpu_layers = max(0, gpu_layers)
        self._low_vram = low_vram
        self._can_type = can_type
        self._ai_honesty = ai_honesty
        self._request_timeout = max(3.0, request_timeout_seconds)
        self._project_dir = project_dir or Path.cwd()
        # Kept as a readable invariant for callers/tests: there is no unmanaged
        # fallback anymore. SilentTalk always leases the independent EdgeAgent.
        self._managed_service = True
        self._lease_active = False
        self._base_url = f"http://127.0.0.1:{port}"
        self._persona = blank_persona()
        # Its own life (CharacterState), part of the system prompt.
        self._state = CharacterState()
        self._memory = memory if memory in MEMORY_LEVELS else "off"
        # What the character said lately, to stop it repeating its catchphrases.
        self._recent_character_lines: collections.deque[str] = collections.deque(maxlen=4)

    def set_ai_honesty(self, mode: str) -> None:
        self._ai_honesty = mode

    def set_max_reply_chars(self, value: int) -> None:
        self._max_reply_chars = max(24, int(value))

    def set_persona(self, persona: Persona) -> None:
        """Takes effect on the next request; the system prompt cache refills once."""
        if persona.id != self._persona.id:
            self._recent_character_lines.clear()
            self._state = CharacterState()
        self._persona = persona

    async def start(self) -> None:
        if self._lease_active:
            return
        try:
            lease = await asyncio.to_thread(
                acquire_character, self._project_dir, os.getpid()
            )
            port = lease.get("port") if isinstance(lease, dict) else None
            if (
                isinstance(port, int)
                and not isinstance(port, bool)
                and 1024 <= port <= 65535
            ):
                self._base_url = f"http://127.0.0.1:{port}"
        except AgentLeaseError as exc:
            raise LocalLLMError(str(exc)) from exc
        self._lora_loaded = self._lora_path is not None
        self._lease_active = True

    async def stop(self) -> None:
        if not self._lease_active:
            return
        try:
            await asyncio.to_thread(release_character, os.getpid())
        except AgentLeaseError:
            _LOG.exception("agent_lease_return_failed")
        finally:
            self._lease_active = False
            self._lora_loaded = False

    async def suggest(
        self,
        *,
        turns: Sequence[ConversationTurn],
        summary: str = "",
        trigger_speaker: str,
        trigger_text: str,
        on_partial: Callable[[ReplySuggestions], None] | None = None,
    ) -> ReplySuggestions:
        """Up to four different lines the character itself would say next,
        sampled from one cached request. With `on_partial`, each new line is
        reported as soon as it is written (from a worker thread), so the
        first choice shows up long before the fourth."""
        messages = self.option_messages(turns=turns, summary=summary)
        lines: list[str] = []
        for _ in range(_OPTIONS_TRIES):
            if len(lines) >= len(REPLY_LANES):
                break
            raw = await asyncio.to_thread(
                self._request_messages, messages, None, 60,
                "options" in self._lora_uses, _OPTIONS_TEMPERATURE,
            )
            line = self._parse_character_line(raw, trigger_text)
            if not line or any(_alike(line, other) for other in lines):
                continue
            lines.append(line)
            if on_partial is not None and len(lines) < len(REPLY_LANES):
                on_partial(
                    dataclasses.replace(
                        _options(lines, trigger_speaker, trigger_text), partial=True
                    )
                )
        return _options(lines, trigger_speaker, trigger_text)

    def option_messages(
        self, *, turns: Sequence[ConversationTurn], summary: str = ""
    ) -> list[dict[str, str]]:
        """The character chat (character mode's format, "你" as the assistant)
        with the options request on the end. Only that tail changes between
        rounds, so llama-server prefills just the new lines."""
        messages = self.character_messages(
            turns=turns, summary=summary, engagement="options", gate=True
        )
        ask = _OPTIONS_ASK
        if self._memory != "off":
            ask = f"（現在是{_now_words()}）\n" + ask
        if not self._persona.is_blank:
            ask = self._persona.reminder_line("說出口的話要是", list_catchphrases=False) + "\n" + ask
        if messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n\n" + ask}
        else:
            messages.append({"role": "user", "content": ask})
        return messages

    async def character_turn(
        self,
        *,
        turns: Sequence[ConversationTurn],
        summary: str = "",
        addressed: bool = False,
        engagement: str = "called",
        lull: bool = False,
    ) -> str:
        """What the character says after the newest line, or "" to stay quiet.

        `turns` is the whole chat so far, the character's own lines marked
        `is_self`; `addressed` is Discord's own @mention or reply signal.
        `lull`: the channel has gone quiet; open a topic, no gate."""
        if lull:
            messages = self.character_messages(
                turns=turns, summary=summary, engagement=engagement, lull=True
            )
            line = ""
            # Read out word for word, a concern sounds like a note, not like
            # talking (options_eval-style check on 21 lulls: 2 near-copies).
            for temperature in (0.65, 1.0):
                raw = await asyncio.to_thread(
                    self._request_messages, messages, None, 90,
                    "line" in self._lora_uses, temperature,
                )
                line = self._parse_character_line(raw, "")
                if not any(_alike_to(line, item, 0.8) for item in self.agenda):
                    break
            if line:
                self._recent_character_lines.append(line)
            return line
        joining = False
        if not addressed:
            if turns and turns[-1].is_self:
                return ""  # the character spoke last; nothing new to answer
            addressed = await self._gate(turns, summary, engagement, "called")
            if not addressed and engagement == "active":
                joining = quiet_for(turns) >= _JOIN_AFTER_QUIET_LINES and await self._gate(
                    turns, summary, engagement, "active"
                )
            if not addressed and not joining:
                return ""
        messages = self.character_messages(
            turns=turns, summary=summary, addressed=addressed, joining=joining,
            engagement=engagement,
        )
        if messages[-1]["role"] != "user":
            return ""
        raw = await asyncio.to_thread(
            self._request_messages, messages, None, 90, "line" in self._lora_uses
        )
        newest = next((turn.text for turn in reversed(turns) if not turn.is_self), "")
        line = self._parse_character_line(raw, newest)
        if line:
            self._recent_character_lines.append(line)
        return line

    async def _gate(
        self,
        turns: Sequence[ConversationTurn],
        summary: str,
        engagement: str,
        question: str,
    ) -> bool:
        gate = self.gate_messages(
            turns=turns, summary=summary, engagement=engagement, question=question
        )
        if gate is None:
            return False
        use_lora = question in self._lora_uses
        return await asyncio.to_thread(self._request_gate, gate, use_lora) == "是"

    def gate_messages(
        self,
        *,
        turns: Sequence[ConversationTurn],
        summary: str = "",
        engagement: str = "called",
        question: str = "called",
    ) -> list[dict[str, str]] | None:
        """The 是/否 request asked before writing a line: the cached chat with
        a gate question on the end ("called": is this line for you; "active":
        would joining in now be natural). None when the character spoke last."""
        base = self.character_messages(
            turns=turns, summary=summary, engagement=engagement, gate=True
        )
        if base[-1]["role"] != "user":
            return None
        return base[:-1] + [
            {"role": "user", "content": base[-1]["content"] + "\n\n" + _GATE_QUESTION[question]}
        ]

    def character_messages(
        self,
        *,
        turns: Sequence[ConversationTurn],
        summary: str = "",
        addressed: bool = False,
        engagement: str = "called",
        gate: bool = False,
        joining: bool = False,
        lull: bool = False,
    ) -> list[dict[str, str]]:
        """The chat as messages. Everything but the tail of the last message
        is byte-identical to the previous request's, for the prompt cache.
        `gate` leaves out the per-request hints (the gate adds its question)."""
        persona = self._persona
        messages = [
            {"role": "system", "content": self._build_turn_system_prompt(persona, engagement)}
        ]
        if summary:
            messages.append({"role": "user", "content": f"（這場對話稍早的重點：{summary}）"})
        for turn in _stable_window(turns):
            if turn.is_self:
                if messages[-1]["role"] == "system":
                    # Chat templates want a user turn first.
                    messages.append({"role": "user", "content": "（對話開始）"})
                if messages[-1]["role"] == "assistant":
                    messages[-1]["content"] += "\n" + turn.text
                else:
                    messages.append({"role": "assistant", "content": turn.text})
                continue
            line = f"{turn.speaker_name}：{turn.text}"
            if messages[-1]["role"] == "user":
                messages[-1]["content"] += "\n" + line
            else:
                messages.append({"role": "user", "content": line})
        if lull and messages[-1]["role"] != "user":
            # The character spoke last; the lull itself is the new "message".
            messages.append({"role": "user", "content": "（安靜）"})
        if messages[-1]["role"] != "user" or gate:
            return messages
        # Per-request hints ride on the end of the newest message only; the
        # next request drops them again, which costs a few tokens of prefill.
        hints = []
        if lull:
            hints.append(_LULL_HINT)
        elif addressed:
            hints.append("（最後這句是在叫你，要回應）")
        elif joining:
            # "active" mode chiming in: nobody called it, so it must not
            # answer as if someone had.
            hints.append("（沒有人叫你，你決定自然地插一句話）")
        # It knows what time it is ("都半夜了還不睡"): with memory on only;
        # on Gemma 4 E2B the clock was part of a variant that lost.
        if self._memory != "off":
            hints.append(f"（現在是{_now_words()}）")
        if not persona.is_blank:
            hints.append(persona.reminder_line("開口的話要是", list_catchphrases=False))
        used = [
            phrase
            for phrase in persona.signature_phrases()
            if any(_signature_used(phrase, line) for line in self._recent_character_lines)
        ]
        if used:
            hints.append(
                "（你最近已經說過" + "、".join(f"「{p}」" for p in used[:3])
                + "，這句換個說法，不要再用）"
            )
        if self._ai_honesty == "admit":
            recent = [turn.text for turn in turns if not turn.is_self][-4:]
            if asks_if_ai("\n".join(recent)):
                hints.append(
                    "（有人在問你是不是 AI 或真人：要老實承認你是 AI 扮演的角色，用你的口吻說）"
                )
        if hints:
            messages[-1] = {
                "role": "user",
                "content": messages[-1]["content"] + "\n\n" + "\n".join(hints),
            }
        return messages

    def _build_turn_system_prompt(self, persona: Persona, engagement: str) -> str:
        sheet = persona.render_prompt_block()
        # All of them, not just who is talking right now: the list is part of
        # the persona, so it stays in the cached prompt. This is what makes a
        # character treat the owner as its husband, kid or brother.
        relations = [
            f"- {rel.name}：{rel.relation}"
            for rel in persona.relationships
            if rel.name and rel.relation
        ]
        if relations:
            sheet += (
                "\n你和這些人的關係（他們說話時，對話裡會出現這個名字）：\n"
                + "\n".join(relations)
                # M1 family chats: a wife answered "老婆你選哪臺車啊" as if
                # she were the husband, and missed lines that opened with 老婆.
                + "\n他們用關係叫你（例如「老婆」「媽」「阿嬤」「哥」）就是在叫你；"
                "你跟他們說話要符合這個關係，不要搞混誰是誰。"
            )
        name = persona.name.strip()
        return _TURN_SYSTEM.format(
            who=f"「{name}」" if name else "一個友善、好聊的朋友",
            engagement=_ENGAGEMENT_RULES.get(engagement, _ENGAGEMENT_RULES["called"]),
            ai_rule=_AI_ADMIT if self._ai_honesty == "admit" else _AI_IN_CHARACTER,
            abilities=_CAN_TYPE if self._can_type else _VOICE_ONLY,
            length=persona.length_rule(),
            max_chars=self._max_reply_chars,
            sheet=f"\n【你的人設】口吻、用詞、句長、立場都照這裡：\n{sheet}\n{self._state_block()}"
            if sheet
            else "",
        )

    def _state_block(self) -> str:
        if self._memory == "off":
            items = self.agenda
            if not items:
                return ""
            return _AGENDA_HEADER + "\n" + "\n".join(f"- {item}" for item in items) + "\n"
        block = self._state.prompt_block(full=self._memory == "full")
        return f"\n【你自己的生活】\n{block}\n" if block else ""

    @property
    def state(self) -> CharacterState:
        return self._state

    @property
    def agenda(self) -> tuple[str, ...]:
        """What is on its mind right now (the active concerns)."""
        return tuple(concern.text for concern in self._state.active_concerns())

    def set_state(self, state: CharacterState) -> None:
        """Takes effect on the next request (the system prompt cache refills once)."""
        self._state = state

    def set_agenda(self, items: Sequence[str]) -> None:
        """Concerns only, nothing else about its life (tests and evals)."""
        self._state = CharacterState(concerns=tuple(Concern(item) for item in items))

    async def make_agenda(self) -> tuple[str, ...]:
        """Top up what is on its mind to a few concerns; returns them all."""
        self._state = await self.fill_concerns(self._state)
        return self.agenda

    async def fill_concerns(self, state: CharacterState) -> CharacterState:
        """New concerns for today on top of the ones still open, following on
        from its recent life. Made from the persona and its own state only."""
        sheet = self._persona.render_prompt_block()
        missing = _CONCERNS_WANTED - len(state.active_concerns())
        if not sheet or missing <= 0:
            return state
        if self._memory == "off":
            messages = [
                {"role": "system", "content": f"你是下面這個人：\n{sheet}"},
                {"role": "user", "content": _AGENDA_ASK},
            ]
        else:
            known = state.prompt_block(full=self._memory == "full")
            messages = [
                {"role": "system", "content": f"你是下面這個人：\n{sheet}" + (f"\n\n{known}" if known else "")},
                {"role": "user", "content": _FILL_ASK.format(now=_now_words(), n=missing)},
            ]
        raw = await asyncio.to_thread(
            self._request_messages, messages, None, 200, False, 0.9
        )
        taken = [c.text for c in state.concerns]
        new: list[Concern] = []
        for line in raw.splitlines():
            item = to_taiwan_traditional(line.strip().lstrip("-*•0123456789.、) ").strip())
            if len(item) >= 4 and not any(_alike_to(item, old, 0.7) for old in taken):
                taken.append(item)
                new.append(Concern(item))
        return replace(state, concerns=state.concerns + tuple(new[:missing])).normalized()

    async def update_state(self, turns: Sequence[ConversationTurn]) -> CharacterState:
        """How its life moved on in this stretch of chat: concerns shared,
        progressing, done or dropped; new ones; views it stated; feelings
        about people; recent; mood. In its own words; nothing is quoted."""
        sheet = self._persona.render_prompt_block()
        if not sheet or not turns:
            return self._state
        name = self._persona.name.strip() or "你"
        chat = "\n".join(
            f"{'你（' + name + '）' if turn.is_self else turn.speaker_name}：{turn.text}"
            for turn in turns[-_UPDATE_CHAT_TURNS:]
        )
        active = self._state.active_concerns()
        concerns = "\n".join(
            f"{n}. {c.text}（{_STATUS_WORDS[c.status]}）" for n, c in enumerate(active, 1)
        ) or "（目前沒有）"
        full = self._memory == "full"
        known = self._state.prompt_block(full=full)
        messages = [
            {"role": "system", "content": f"你是下面這個人：\n{sheet}" + (f"\n\n{known}" if known else "")},
            {
                "role": "user",
                "content": _UPDATE_ASK.format(
                    chat=chat, concerns=concerns, full_lines=_UPDATE_FULL_LINES if full else ""
                ),
            },
        ]
        raw = await asyncio.to_thread(
            self._request_messages, messages, None, 260 if full else 160, False
        )
        update = _parse_state_update(raw, self._state)
        if not full:
            update = replace(update, views=(), feelings=())
        return merge_state(self._state, update)

    def _parse_character_line(self, raw: str, trigger_text: str) -> str:
        cleaned = raw.strip()
        close_index = cleaned.lower().rfind(_THINK_CLOSE)
        if close_index >= 0:
            cleaned = cleaned[close_index + len(_THINK_CLOSE) :]
        line = next((part.strip() for part in cleaned.splitlines() if part.strip()), "")
        # Tolerate "SAY|…", "你要說：…" and "<own name>：…" decorations, but
        # never strip other text before a colon ("說真的：…" is speech).
        names = {"你", "我", self._persona.name.strip()} | {
            nick.strip() for nick in self._persona.nickname.split("、")
        }
        speaker = "|".join(re.escape(name) for name in names if name)
        line = re.sub(rf"^(SAY\s*[|｜:：]|你要說\s*[:：]|({speaker})\s*[:：])", "", line).strip()
        value = self._clean_reply(line)
        if not value or value.upper().startswith("SKIP") or _echoes(value, trigger_text):
            return ""
        return value

    async def summarize(self, transcript: str, previous: str = "") -> str:
        """Fold an older stretch of chat into a couple of lines, so a long
        session keeps its thread without growing the prompt."""
        system = (
            "你是對話記錄員。把 Discord 語音聊天的一段記錄濃縮成重點，"
            "只寫真的發生過的事。保留人物、尚未結束的話題、約定、決定和專有名詞。"
            "台灣繁體中文，最多六句、320 字以內，不要換行、不要評論、不要加標題。"
        )
        earlier = f"更早的重點：{previous}\n\n" if previous else ""
        prompt = (
            f"{earlier}這段對話：\n{transcript}\n\n"
            "寫成重點（誰在聊什麼、決定了什麼、有什麼還沒結束）："
        )
        raw = await asyncio.to_thread(
            self._request_completion, system, prompt, None, 640
        )
        text = raw.strip()
        close_index = text.lower().rfind(_THINK_CLOSE)
        if close_index >= 0:
            text = text[close_index + len(_THINK_CLOSE) :]
        return to_taiwan_traditional(" ".join(text.split()))[:_SUMMARY_CHARS]

    def _health_ok(self) -> bool:
        try:
            request = urllib.request.Request(
                self._base_url + "/health",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=1.0) as response:
                return 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            return False

    def _request_completion(
        self,
        system: str,
        prompt: str,
        on_lines: Callable[[str], None] | None = None,
        max_tokens: int = 240,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "/no_think\n" + prompt},
        ]
        return self._request_messages(messages, on_lines, max_tokens)

    def _request_gate(self, messages: list[dict[str, str]], character: bool = True) -> str:
        payload = {
            "model": "local",
            "messages": messages,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "grammar": _GATE_GRAMMAR,
            "temperature": 0.0,
            "max_tokens": 1,
            **self._lora_field(character),
        }
        body = self._post(payload)
        try:
            return str(body["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalLLMError("本機回覆模型回傳格式異常。") from exc

    def _post(self, payload: dict[str, object]) -> dict:
        request = urllib.request.Request(
            self._base_url + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LocalLLMError(f"本機回覆模型失敗：{exc}") from exc

    def _lora_field(self, character: bool) -> dict[str, object]:
        """Every request sets the adapter's scale (llama-server applies a
        loaded adapter to requests that leave it out) and the slot kept for
        that scale, so switching does not throw away the other's cache."""
        if not self._lora_loaded:
            return {}
        return {
            "lora": [{"id": 0, "scale": 1.0 if character else 0.0}],
            "id_slot": 0 if character else 1,
        }

    def _request_messages(
        self,
        messages: list[dict[str, str]],
        on_lines: Callable[[str], None] | None = None,
        max_tokens: int = 240,
        character: bool = False,
        temperature: float = 0.65,
    ) -> str:
        payload = {
            "model": "local",
            **self._lora_field(character),
            "messages": messages,
            # The system prompt is identical across requests; reuse its KV cache.
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": temperature,
            "top_p": 0.85,
            "max_tokens": max_tokens,
            "stream": on_lines is not None,
        }
        request = urllib.request.Request(
            self._base_url + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._request_timeout
            ) as response:
                if on_lines is not None:
                    return consume_stream(response, on_lines)
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise LocalLLMError(f"本機回覆模型失敗：{exc}") from exc

        try:
            return str(body["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalLLMError("本機回覆模型回傳格式異常。") from exc

    def _clean_reply(self, value: str) -> str:
        value = value.strip().strip("\"'「」『』*")
        # Small Qwen models slip into Simplified now and then (2-9% of lanes).
        value = to_taiwan_traditional(" ".join(value.split()))
        if value.startswith("...") or value in {"…", "..."}:
            return ""
        if len(value) > self._max_reply_chars:
            value = value[: self._max_reply_chars].rstrip() + "…"
        return value


def consume_stream(events: Iterable[bytes], on_lines: Callable[[str], None]) -> str:
    """Read an OpenAI-style SSE stream; call `on_lines(text so far)` whenever a
    line is complete. Returns the whole text."""
    text = ""
    for raw in events:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            choice = json.loads(data)["choices"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
        piece = (choice.get("delta") or {}).get("content") or ""
        if not piece:
            continue
        text += piece
        if "\n" in piece:
            on_lines(text[: text.rfind("\n")])
    return text


def quiet_for(turns: Sequence[ConversationTurn]) -> int:
    """Lines from others since the character last spoke."""
    count = 0
    for turn in reversed(turns):
        if turn.is_self:
            break
        count += 1
    return count


def _stable_window(turns: Sequence[ConversationTurn]) -> Sequence[ConversationTurn]:
    """The newest turns, trimmed a batch at a time so the start stays put."""
    excess = len(turns) - _TURN_WINDOW
    if excess <= 0:
        return turns
    start = -(-excess // _TURN_DROP_BATCH) * _TURN_DROP_BATCH
    return turns[start:]


def _signature_used(phrase: str, line: str) -> bool:
    """A catchphrase appears, or a long sample line is echoed in part."""
    phrase, line = _compact(phrase), _compact(line)
    if len(phrase) <= 6:
        return bool(phrase) and phrase in line
    return any(phrase[i : i + 5] in line for i in range(len(phrase) - 4))


def _options(lines: list[str], trigger_speaker: str, trigger_text: str) -> ReplySuggestions:
    """Sampled lines in the W/A/D/S slots, in the order they were written."""
    values = {lane.field: "" for lane in REPLY_LANES}
    for lane, line in zip(REPLY_LANES, lines, strict=False):
        values[lane.field] = line
    return ReplySuggestions(trigger_speaker=trigger_speaker, trigger_text=trigger_text, **values)


def _now_words(now: datetime | None = None) -> str:
    """ "星期五晚上 11:40": the local time the way people say it."""
    now = now or datetime.now()
    hour = now.hour
    part = (
        "半夜" if hour < 5 else "清晨" if hour < 8 else "早上" if hour < 11
        else "中午" if hour < 13 else "下午" if hour < 18 else "晚上"
    )
    return f"星期{_WEEKDAYS[now.weekday()]}{part} {now:%H:%M}"


def _parse_state_update(raw: str, state: CharacterState) -> CharacterState:
    """The model's update lines ("進度|2|done|約成了") as a new state; lines
    it could not read are skipped, and anything unmentioned stays as it was."""
    active = state.active_concerns()
    updates: dict[int, tuple[str, str]] = {}
    new: list[str] = []
    views: list[str] = []
    feelings: list[tuple[str, str]] = []
    recent = mood = ""
    for line in raw.splitlines():
        parts = [part.strip() for part in re.split(r"[|｜]", line.strip().lstrip("-*• "))]
        if len(parts) < 2 or not parts[1]:
            continue
        tag = parts[0]
        if tag == "進度" and len(parts) >= 3:
            number = re.sub(r"\D", "", parts[1])
            status = parts[2].lower()
            if number and status in STATUSES and 1 <= int(number) <= len(active):
                updates[int(number) - 1] = (status, parts[3] if len(parts) > 3 else "")
        elif tag == "新心事":
            new.append(parts[1])
        elif tag == "看法":
            views.append(parts[1])
        elif tag == "感覺" and len(parts) >= 3 and parts[2]:
            feelings.append((parts[1], to_taiwan_traditional(parts[2])))
        elif tag == "近況":
            recent = parts[1]
        elif tag == "心情":
            mood = parts[1]
    moved = []
    for index, concern in enumerate(active):
        if index in updates:
            status, note = updates[index]
            moved.append(Concern(concern.text, status, to_taiwan_traditional(note) or concern.note))
        else:
            moved.append(concern)
    return CharacterState(
        concerns=tuple(
            state.finished_concerns() + moved + [Concern(to_taiwan_traditional(t)) for t in new]
        ),
        recent=to_taiwan_traditional(recent),
        mood=to_taiwan_traditional(mood),
        views=tuple(to_taiwan_traditional(view) for view in views),
        feelings=tuple(feelings),
    )


def _alike_to(a: str, b: str, ratio: float) -> bool:
    return bool(a) and difflib.SequenceMatcher(None, _compact(a), _compact(b)).ratio() >= ratio


def _alike(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, _compact(a), _compact(b)).ratio() >= _OPTIONS_ALIKE


def _echoes(reply: str, line: str) -> bool:
    """The reply mostly repeats the line it answers ("有啊我們週末去看電影" ->
    "有啊，週末去看電影"): a small model does this instead of answering."""
    reply, line = _compact(reply), _compact(line)
    if reply == line:
        return True
    return len(reply) >= 4 and difflib.SequenceMatcher(None, reply, line).ratio() >= 0.75


def _compact(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum()).lower()
