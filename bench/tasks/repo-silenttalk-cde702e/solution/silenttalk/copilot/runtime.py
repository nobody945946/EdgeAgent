from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from silenttalk.config import CopilotConfig
from silenttalk.copilot.character import CharacterPolicy
from silenttalk.copilot.character_state import STATE_FILE, CharacterStateStore
from silenttalk.copilot.context import ConversationStore
from silenttalk.copilot.llm import LocalReplyModel
from silenttalk.copilot.persona import Persona, blank_persona
from silenttalk.copilot.stt import StreamingTranscriber
from silenttalk.copilot.stt_offline import OfflineTranscriber
from silenttalk.copilot.types import ConversationTurn, ReplySuggestions

_LOG = logging.getLogger(__name__)

SuggestionCallback = Callable[[ReplySuggestions], None]
MessageCallback = Callable[[str], None]
TurnCallback = Callable[[ConversationTurn], None]
ThinkingCallback = Callable[[str, str], None]
AgendaCallback = Callable[[tuple[str, ...]], None]

# How often the worker looks for a lull while nothing else wakes it.
_LULL_POLL_SECONDS = 1.0
# Updating the character's life costs a 3050 Ti a few seconds of generation,
# so it never runs mid-chat: "light" waits for this long a silence (after any
# lull topic), "full" also runs every _FULL_UPDATE_LINES lines while idle.
_QUIET_UPDATE_SECONDS = 90.0
_FULL_UPDATE_LINES = 16
# Too little chat since the last update to be worth one.
_MIN_UPDATE_LINES = 4
LineCallback = Callable[[str], None]

# "嗯", "好", "喔" are context, not something worth four reply options.
_MIN_TRIGGER_CHARS = 2


@dataclass(frozen=True, slots=True)
class _Trigger:
    speaker_name: str
    text: str
    # Position in the conversation, to notice when the chat moved on.
    seq: int = 0
    # Discord's own signal: an @mention of the bot or a reply to it.
    addressed: bool = False
    via: str = "voice"
    # time.monotonic() when the line came in, to log how long an answer took
    # including any wait behind the previous generation.
    heard_at: float = 0.0


class CopilotRuntime:
    """Owns local STT/context and leases reply generation from EdgeAgent."""

    def __init__(
        self,
        project_dir: Path,
        config: CopilotConfig,
        owner_user_id: int,
        on_suggestions: SuggestionCallback,
        on_error: MessageCallback,
        *,
        on_turn: TurnCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        on_character_line: LineCallback | None = None,
        on_character_text: LineCallback | None = None,
        on_agenda: AgendaCallback | None = None,
        can_type: bool = False,
    ) -> None:
        self._config = config
        self._owner_user_id = owner_user_id
        self._on_suggestions = on_suggestions
        self._on_error = on_error
        self._on_turn = on_turn
        self._on_thinking = on_thinking
        self._on_character_line = on_character_line
        self._on_character_text = on_character_text
        self._on_agenda = on_agenda
        self._agenda_task: asyncio.Task[None] | None = None
        self._memory = config.character_memory
        self._states = CharacterStateStore(project_dir / STATE_FILE)
        self._lines_since_update = 0
        # Lulls: when anyone last spoke, and whether the character already
        # opened a topic in this quiet spell (only someone else talking
        # starts a new one, so it never talks to an empty room twice).
        self._last_heard_at = 0.0
        self._lull_spoken = True
        self._persona = blank_persona()
        self._character_mode = False
        self._character_policy = CharacterPolicy(mode=config.character_engagement)
        self._turn_seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: _Trigger | None = None
        self._latest_trigger: _Trigger | None = None
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task[None] | None = None

        runtime_dir = project_dir / ".runtime"
        if config.stt_engine == "sensevoice":
            transcriber, asr_dir = OfflineTranscriber, runtime_dir / "models" / "sensevoice"
        else:
            transcriber, asr_dir = StreamingTranscriber, runtime_dir / "models" / "asr"
        llm_model = Path(config.llm_model)

        self._context = ConversationStore(
            max_turns=config.max_session_turns,
        )
        # Keep this many turns verbatim; older ones are folded into a summary
        # so a long session never gets truncated mid-thought.
        self._compact_keep = config.compact_keep_turns
        self._compact_trigger = self._compact_keep + max(4, config.compact_batch_turns)
        self._compacting = False
        self._llm = LocalReplyModel(
            llm_model,
            port=config.llama_port,
            context_size=config.llm_context_size,
            max_reply_chars=config.max_reply_chars,
            gpu_layers=config.llm_gpu_layers,
            low_vram=config.llm_low_vram,
            can_type=can_type,
            ai_honesty=config.character_ai_honesty,
            lora_path=(
                Path(config.character_lora) if config.character_lora else None
            ),
            lora_uses=config.character_lora_uses,
            memory=config.character_memory,
            project_dir=project_dir,
        )
        self._stt = transcriber(
            asr_dir,
            on_turn=self._on_transcribed_turn,
            on_error=on_error,
            endpoint_silence_ms=config.endpoint_silence_ms,
        )

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        await asyncio.to_thread(self._stt.start)
        await self._llm.start()
        self._worker = asyncio.create_task(
            self._suggestion_worker(), name="copilot-suggestion-worker"
        )
        _LOG.info("copilot_runtime_ready")

    async def stop(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        # The chat is over: let its life move on once more before closing.
        if self._memory != "off" and self._lines_since_update >= _MIN_UPDATE_LINES:
            try:
                await asyncio.wait_for(self._update_life(), timeout=30)
            except (TimeoutError, asyncio.CancelledError):
                _LOG.warning("copilot_state_update_skipped reason=timeout")
        self._save_life()
        await asyncio.to_thread(self._stt.stop)
        await self._llm.stop()
        self._loop = None

    def accept_pcm(self, speaker_id: int, speaker_name: str, pcm: bytes) -> None:
        # Options speak for the owner, so their mic is not input there. In
        # character mode the owner is another participant the character hears.
        if speaker_id == self._owner_user_id and not self._character_mode:
            return
        self._stt.push_pcm(speaker_id, speaker_name, pcm)

    def accept_text(
        self, speaker_id: int, speaker_name: str, text: str, addressed: bool = False
    ) -> None:
        """A line someone typed in the channel; safe from any thread.
        `addressed`: Discord says it @mentioned the bot or replied to it."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._handle_turn, speaker_id, speaker_name, text, "text", addressed
        )

    def record_self(self, text: str) -> None:
        """Something the bot actually said aloud (after playback finished)."""
        name = "你"
        if self._character_mode and self._persona.name.strip():
            name = self._persona.name.strip()
        turn = ConversationTurn.now(self._owner_user_id, name, text, is_self=True)
        self._context.append(turn)
        self._emit_turn(turn)
        # The silence counts from the last thing anyone said, the bot included.
        self._last_heard_at = time.monotonic()
        self._lines_since_update += 1

    def set_persona(self, persona: Persona) -> None:
        """Must run on the runtime loop; applies from the next generation on."""
        changed = persona.id != self._persona.id
        if changed:
            self._save_life()  # the previous character's, before it is swapped out
        self._persona = persona
        self._llm.set_persona(persona)
        _LOG.info("copilot_persona_set blank=%s", persona.is_blank)
        if changed and self._memory != "off" and not persona.is_blank:
            self._llm.set_state(self._states.load(persona.id))
            self._lines_since_update = 0
        if changed or not self._llm.agenda:
            self._start_agenda(reroll=False)

    def refresh_agenda(self) -> None:
        """換一批: new concerns in place of the ones it has not brought up yet.
        Must run on the runtime loop."""
        self._start_agenda(reroll=True)

    def clear_life(self) -> None:
        """Forget what the character carried between sessions (persona page).
        Must run on the runtime loop."""
        if not self._persona.is_blank:
            self._states.clear(self._persona.id)
        self._llm.set_state(type(self._llm.state)())
        self._start_agenda(reroll=False)

    def _start_agenda(self, *, reroll: bool) -> None:
        if self._loop is None or self._persona.is_blank:
            return
        if self._agenda_task is not None:
            self._agenda_task.cancel()
        self._agenda_task = self._loop.create_task(
            self._make_agenda(reroll), name="copilot-agenda"
        )

    async def _make_agenda(self, reroll: bool) -> None:
        started = time.perf_counter()
        state = self._llm.state
        if reroll:
            state = replace(
                state, concerns=tuple(c for c in state.concerns if c.status != "open")
            )
        try:
            state = await self._llm.fill_concerns(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("copilot_agenda_failed error_type=%s", type(exc).__name__)
            return
        self._llm.set_state(state)
        self._save_life()
        _LOG.info(
            "copilot_agenda_ready items=%d ms=%d",
            len(state.active_concerns()),
            round((time.perf_counter() - started) * 1000),
        )
        self._show_life()

    def _show_life(self) -> None:
        if self._on_agenda is None:
            return
        marks = {"told": "（說過了）", "progress": "（有進展）"}
        self._on_agenda(
            tuple(c.text + marks.get(c.status, "") for c in self._llm.state.active_concerns())
        )

    def _save_life(self) -> None:
        if self._memory == "off" or self._persona.is_blank or self._llm.state.is_empty:
            return
        try:
            self._states.save(self._persona.id, self._llm.state)
        except OSError as exc:
            _LOG.warning("copilot_state_save_failed error_type=%s", type(exc).__name__)

    def _update_due(self) -> bool:
        if self._memory == "off" or self._persona.is_blank or self._pending is not None:
            return False
        if self._memory == "full" and self._lines_since_update >= _FULL_UPDATE_LINES:
            return True
        quiet = time.monotonic() - self._last_heard_at
        return self._lines_since_update >= _MIN_UPDATE_LINES and quiet >= _QUIET_UPDATE_SECONDS

    async def _update_life(self) -> None:
        """How the chat moved its life on: concerns shared, progressing, done
        or dropped, new ones, recent and mood (views and feelings in "full").
        Counts only in the log; the state itself is the user's file."""
        started = time.perf_counter()
        self._lines_since_update = 0
        try:
            state = await self._llm.update_state(self._context.snapshot())
            if len(state.active_concerns()) < 2:
                state = await self._llm.fill_concerns(state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("copilot_state_update_failed error_type=%s", type(exc).__name__)
            return
        self._llm.set_state(state)
        self._save_life()
        _LOG.info(
            "copilot_state_updated active=%d finished=%d ms=%d",
            len(state.active_concerns()),
            len(state.finished_concerns()),
            round((time.perf_counter() - started) * 1000),
        )
        self._show_life()

    def apply_settings(self, copilot: dict[str, object]) -> None:
        """Live settings from the settings page. Must run on the runtime loop.

        Only what does not need a reload: engagement, compaction and reply
        length. Model, port and STT engine are startup-only."""
        self._config = replace(
            self._config,
            character_lull_seconds=float(
                copilot.get("character_lull_seconds", self._config.character_lull_seconds)
            ),
        )
        policy = self._character_policy
        self._character_policy = CharacterPolicy(
            mode=str(copilot.get("character_engagement", policy.mode)),
            stale_turns=policy.stale_turns,
        )
        self._config = replace(
            self._config,
            compact_context=bool(
                copilot.get("compact_context", self._config.compact_context)
            ),
            max_reply_chars=int(
                copilot.get("max_reply_chars", self._config.max_reply_chars)
            ),
            character_ai_honesty=str(
                copilot.get("character_ai_honesty", self._config.character_ai_honesty)
            ),
        )
        self._compact_keep = max(
            8, int(copilot.get("compact_keep_turns", self._compact_keep))
        )
        self._compact_trigger = self._compact_keep + max(
            4, int(copilot.get("compact_batch_turns", self._config.compact_batch_turns))
        )
        self._llm.set_max_reply_chars(self._config.max_reply_chars)
        self._llm.set_ai_honesty(
            str(copilot.get("character_ai_honesty", self._config.character_ai_honesty))
        )
        _LOG.info(
            "copilot_settings_applied engagement=%s compact=%s",
            self._character_policy.mode,
            self._config.compact_context,
        )

    def set_character_mode(self, enabled: bool) -> None:
        """Copilot (you pick, the bot says it) vs. character (the bot is the
        persona and joins in by itself). Must run on the runtime loop."""
        if enabled == self._character_mode:
            return
        self._character_mode = enabled
        self._pending = None
        _LOG.info("copilot_character_mode enabled=%s", enabled)

    def regenerate(self) -> bool:
        """Ask again for the newest line. Must run on the runtime loop."""
        if self._latest_trigger is None or self._wake is None:
            return False
        self._pending = self._latest_trigger
        self._wake.set()
        return True

    def _on_transcribed_turn(
        self,
        speaker_id: int,
        speaker_name: str,
        text: str,
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._handle_turn, speaker_id, speaker_name, text)

    def _handle_turn(
        self,
        speaker_id: int,
        speaker_name: str,
        text: str,
        via: str = "voice",
        mentioned: bool = False,
    ) -> None:
        # In copilot mode the bot speaks *for* the owner, so the owner's own mic
        # is not conversation; in character mode the owner is one more friend.
        if speaker_id == self._owner_user_id and not self._character_mode:
            return

        turn = ConversationTurn.now(speaker_id, speaker_name, text, via=via)
        self._context.append(turn)
        self._emit_turn(turn)
        self._turn_seq += 1
        self._last_heard_at = time.monotonic()
        self._lull_spoken = False
        self._lines_since_update += 1
        if self._wake is not None and self._pending is None:
            # Nothing waiting to generate: a good moment to compact.
            self._maybe_compact()
        if len(_compact(text)) < _MIN_TRIGGER_CHARS or self._wake is None:
            return

        # In character mode every line goes to the model, which reads the
        # whole chat and decides whether it is being talked to (SKIP if not).
        # Keyword rules could not tell "阿凱你在嗎" from "我剛才跟阿凱講話".
        trigger = _Trigger(
            speaker_name, text, self._turn_seq, mentioned, via, time.monotonic()
        )

        # Latest wins: while a generation is running, newer lines replace the
        # queued trigger instead of stacking requests on a small GPU.
        self._latest_trigger = trigger
        self._pending = trigger
        self._wake.set()

    def _maybe_compact(self) -> None:
        """Start folding the oldest turns into the summary, off the hot path."""
        loop = self._loop
        if self._compacting or loop is None or not self._config.compact_context:
            return
        if len(self._context.snapshot()) < self._compact_trigger:
            return
        self._compacting = True
        loop.create_task(self._compact(), name="copilot-compact")

    async def _compact(self) -> None:
        try:
            keep = self._compact_keep
            older = self._context.pending_compaction(keep)
            if not older:
                return
            transcript = "\n".join(
                f"{'你' if turn.is_self else turn.speaker_name}：{turn.text}"
                for turn in older
            )
            started = time.perf_counter()
            summary = await self._llm.summarize(transcript, self._context.summary)
            if not summary:
                return
            self._context.compact(summary, len(older), keep)
            _LOG.info(
                "copilot_context_compacted folded=%d summary_chars=%d ms=%d",
                len(older),
                len(summary),
                round((time.perf_counter() - started) * 1000),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.warning("copilot_compact_failed", exc_info=False)
        finally:
            self._compacting = False

    async def _suggestion_worker(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_LULL_POLL_SECONDS)
            except TimeoutError:
                if self._lull_due():
                    await self._open_topic()
                elif self._update_due():
                    await self._update_life()
                continue
            self._wake.clear()
            trigger, self._pending = self._pending, None
            self._maybe_compact()
            if trigger is None:
                continue
            if self._character_mode:
                await self._speak(trigger)
            else:
                await self._generate(trigger)

    def _lull_due(self) -> bool:
        seconds = self._config.character_lull_seconds
        return (
            self._character_mode
            and seconds > 0
            and not self._lull_spoken
            and self._pending is None
            and time.monotonic() - self._last_heard_at >= seconds
        )

    async def _open_topic(self) -> None:
        """Nobody has said anything for a while: the character brings up
        something on its mind, or asks after someone."""
        self._lull_spoken = True
        heard_at = self._last_heard_at
        started = time.perf_counter()
        try:
            line = await self._llm.character_turn(
                turns=self._context.snapshot(),
                summary=self._context.summary,
                engagement=self._character_policy.mode,
                lull=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("copilot_lull_failed error_type=%s", type(exc).__name__)
            return
        _LOG.info(
            "copilot_character_lull latency_ms=%d spoke=%s",
            round((time.perf_counter() - started) * 1000),
            bool(line),
        )
        # Someone spoke while it was thinking: the lull is over.
        if not line or not self._character_mode or self._last_heard_at != heard_at:
            return
        if self._on_character_line is not None:
            self._on_character_line(line)

    async def _speak(self, trigger: _Trigger) -> None:
        started = time.perf_counter()
        try:
            line = await self._llm.character_turn(
                turns=self._context.snapshot(),
                summary=self._context.summary,
                addressed=trigger.addressed,
                engagement=self._character_policy.mode,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("copilot_character_failed error_type=%s", type(exc).__name__)
            self._on_error(f"角色回話產生失敗：{exc}")
            return
        moved_on = self._turn_seq - trigger.seq
        _LOG.info(
            "copilot_character_line latency_ms=%d since_heard_ms=%d spoke=%s"
            " addressed=%s moved_on=%d",
            round((time.perf_counter() - started) * 1000),
            round((time.monotonic() - trigger.heard_at) * 1000) if trigger.heard_at else -1,
            bool(line),
            trigger.addressed,
            moved_on,
        )
        if not line or not self._character_mode:
            return
        if moved_on >= self._character_policy.stale_turns and not trigger.addressed:
            return  # the chat moved on; a late reply would sound off
        # Answer in the medium the line came in: typed gets typed, spoken
        # gets spoken. Falls back to speaking if text is unavailable.
        deliver = self._on_character_line
        if trigger.via == "text" and self._on_character_text is not None:
            deliver = self._on_character_text
        if deliver is not None:
            deliver(line)

    async def _generate(self, trigger: _Trigger) -> None:
        if self._on_thinking is not None:
            self._on_thinking(trigger.speaker_name, trigger.text)
        started = time.perf_counter()
        try:
            suggestions = await self._llm.suggest(
                turns=self._context.snapshot(),
                summary=self._context.summary,
                trigger_speaker=trigger.speaker_name,
                trigger_text=trigger.text,
                on_partial=self._on_suggestions,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning(
                "copilot_suggestion_failed error_type=%s",
                type(exc).__name__,
            )
            self._on_error(f"Copilot 建議產生失敗：{exc}")
            suggestions = ReplySuggestions(
                trigger_speaker=trigger.speaker_name,
                trigger_text=trigger.text,
                error=str(exc),
            )
        _LOG.info(
            "copilot_suggestion_ready latency_ms=%d lanes=%d failed=%s",
            round((time.perf_counter() - started) * 1000),
            sum(bool(text) for _, text in suggestions.items()),
            bool(suggestions.error),
        )
        self._on_suggestions(suggestions)

    def _emit_turn(self, turn: ConversationTurn) -> None:
        if self._on_turn is None:
            return
        try:
            self._on_turn(turn)
        except Exception:
            _LOG.exception("copilot_turn_callback_failed")


def _compact(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())
