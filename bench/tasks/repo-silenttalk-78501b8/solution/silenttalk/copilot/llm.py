from __future__ import annotations

import asyncio
import collections
import dataclasses
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import IO

from silenttalk.copilot.character import asks_if_ai
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
# Rides on every line and options request, never on the gate (the LoRA's
# gate was trained without it). A persona that says "常用「欸」「好啦」"
# made Gemma 4 E2B put them in 22% of lines twice or more (humans in the
# test chats: 8%), and read aloud they sound put on (laptop test,
# 2026-09-28: "回復刻意使用一些語助詞，超級爛").
_PARTICLE_RULE = (
    "（語氣詞像真人一樣少用：大多數句子一個都不用，一句最多一個；"
    "人設寫的語氣詞和口頭禪是偶爾才出現的習慣，不是每句都要）"
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
_TURN_WINDOW = 40
_TURN_DROP_BATCH = 16

# A summary longer than this is no longer a summary; it also has to stay
# small next to the persona in a 4096-token context.
_SUMMARY_CHARS = 320

_END_PUNCTUATION = "？?！!。.…~～」』)）"
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
# llama-server stderr lines worth keeping in the app log: they prove whether
# the model actually landed on the GPU. Conversation text never appears here.
_DIAGNOSTIC_MARKERS = ("offloaded", "using device", "error", "failed", "out of memory")


class LocalLLMError(RuntimeError):
    pass


class LocalReplyModel:
    """Persistent llama.cpp server for tiny local reply generation."""

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
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr_tail: collections.deque[str] = collections.deque(maxlen=40)
        self._base_url = f"http://127.0.0.1:{port}"
        self._persona = blank_persona()
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
        self._persona = persona

    async def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self._model_path.is_file():
            raise LocalLLMError(f"找不到 Copilot 模型：{self._model_path}")

        executable = find_llama_server()

        threads = max(2, min(6, (os.cpu_count() or 4) // 2))
        devices = await asyncio.to_thread(list_llama_devices, executable)
        _LOG.info("copilot_llm_devices %s", " | ".join(devices) or "none(cpu-only)")
        self._lora_loaded = False
        if self._lora_path is not None and not self._lora_path.is_file():
            _LOG.warning("copilot_lora_missing file=%s", self._lora_path.name)
        with_lora = self._lora_path is not None and self._lora_path.is_file()
        # One slot per adapter setting. llama-server drops a slot's prompt
        # cache whenever the adapter scale differs from the last request's
        # (measured: the whole 1.3k-token chat re-read, ~0.9 s on a 3090),
        # so requests with and without it keep a slot each (_slot_field).
        # Without an adapter one slot: recent llama-server defaults to
        # several, which splits the context on a 4 GB laptop GPU.
        slots = 2 if with_lora else 1
        args = [
            executable,
            "--model",
            str(self._model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--ctx-size",
            str(self._context_size * slots),
            "--threads",
            str(threads),
            "--n-gpu-layers",
            str(self._gpu_layers),
            "--parallel",
            str(slots),
            # Needed so chat_template_kwargs.enable_thinking reaches Qwen3's template.
            "--jinja",
        ]
        if with_lora:
            args += ["--lora", str(self._lora_path)]
            self._lora_loaded = True
        _LOG.info(
            "copilot_llm_start model=%s context=%d threads=%d gpu_layers=%d low_vram=%s",
            self._model_path.name,
            self._context_size,
            threads,
            self._gpu_layers,
            self._low_vram,
        )
        if self._low_vram:
            # 8-bit KV cache: 1.7B at ctx 4096 drops from ~1.6 GB to ~1.4 GB of
            # VRAM, which matters next to a game on a 4 GB card. Quantized V
            # needs flash attention; drivers without it fall back to f16.
            try:
                await self._launch(
                    args + ["--flash-attn", "on"]
                    + ["--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]
                )
                return
            except LocalLLMError as exc:
                _LOG.warning("copilot_llm_low_vram_fallback reason=%s", str(exc)[:160])
        await self._launch(args)

    async def _launch(self, args: list[str]) -> None:
        self._stderr_tail.clear()
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if self._process.stderr is not None:
            threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr,),
                name="copilot-llm-stderr",
                daemon=True,
            ).start()

        deadline = asyncio.get_running_loop().time() + 60.0
        while asyncio.get_running_loop().time() < deadline:
            if self._process.poll() is not None:
                code = self._process.returncode
                self._process = None
                raise LocalLLMError(
                    f"llama-server 啟動失敗，exit code={code}｜{self._last_error_line()}"
                )
            if await asyncio.to_thread(self._health_ok):
                return
            await asyncio.sleep(0.25)

        await self.stop()
        raise LocalLLMError("llama-server 載入模型逾時。")

    async def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)

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
        ask = _PARTICLE_RULE + "\n" + _OPTIONS_ASK
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
    ) -> str:
        """What the character says after the newest line, or "" to stay quiet.

        `turns` is the whole chat so far, the character's own lines marked
        `is_self`; `addressed` is Discord's own @mention or reply signal."""
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
        if messages[-1]["role"] != "user" or gate:
            return messages
        # Per-request hints ride on the end of the newest message only; the
        # next request drops them again, which costs a few tokens of prefill.
        hints = []
        if addressed:
            hints.append("（最後這句是在叫你，要回應）")
        elif joining:
            # "active" mode chiming in: nobody called it, so it must not
            # answer as if someone had.
            hints.append("（沒有人叫你，你決定自然地插一句話）")
        if not persona.is_blank:
            hints.append(persona.reminder_line("開口的話要是", list_catchphrases=False))
        hints.append(_PARTICLE_RULE)
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
        sheet = persona.render_system_block()
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
            sheet=f"\n【你的人設】口吻、用詞、句長、立場都照這裡：\n{sheet}\n" if sheet else "",
        )

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
            "只寫真的發生過的事。台灣繁體中文，最多兩句、80 字以內，不要換行、"
            "不要評論、不要加標題。"
        )
        earlier = f"更早的重點：{previous}\n\n" if previous else ""
        prompt = (
            f"{earlier}這段對話：\n{transcript}\n\n"
            "寫成重點（誰在聊什麼、決定了什麼、有什麼還沒結束）："
        )
        raw = await asyncio.to_thread(
            self._request_completion, system, prompt, None, 160
        )
        text = raw.strip()
        close_index = text.lower().rfind(_THINK_CLOSE)
        if close_index >= 0:
            text = text[close_index + len(_THINK_CLOSE) :]
        return to_taiwan_traditional(" ".join(text.split()))[:_SUMMARY_CHARS]

    def _drain_stderr(self, stream: IO[bytes]) -> None:
        for raw_line in iter(stream.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            self._stderr_tail.append(line)
            lower = line.lower()
            if any(marker in lower for marker in _DIAGNOSTIC_MARKERS):
                _LOG.info("copilot_llm_server %s", line[:300])
        stream.close()

    def _last_error_line(self) -> str:
        for line in reversed(self._stderr_tail):
            lower = line.lower()
            if "error" in lower or "failed" in lower or "memory" in lower:
                return line[:200]
        return self._stderr_tail[-1][:200] if self._stderr_tail else "沒有輸出"

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


def find_llama_server() -> str:
    executable = (
        shutil.which("llama-server.exe")
        or shutil.which("llama-server")
        or _winget_llama_server()
    )
    if not executable:
        raise LocalLLMError(
            "找不到 llama-server.exe；請重新執行「啟動 SilentTalk.cmd」自動部署。"
        )
    return executable


def _winget_llama_server() -> str | None:
    """winget can unpack llama.cpp without creating its WinGet\\Links shim
    (seen on a 3050 Ti laptop); the exe is then only in the package folder."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    found = sorted(packages.glob("ggml.llamacpp*/**/llama-server.exe"))
    return str(found[-1]) if found else None


def list_llama_devices(executable: str) -> list[str]:
    """GPUs llama.cpp can use, e.g. "Vulkan0: NVIDIA GeForce RTX 3050 Ti (4096 MiB, …)".

    An empty list means the model will run on the CPU (about 3x slower).
    """
    try:
        listing = subprocess.run(
            [executable, "--list-devices"],
            capture_output=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    output = (listing.stdout + listing.stderr).decode("utf-8", errors="replace")
    return [
        line.strip() for line in output.splitlines() if "MiB" in line and ":" in line
    ]


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
