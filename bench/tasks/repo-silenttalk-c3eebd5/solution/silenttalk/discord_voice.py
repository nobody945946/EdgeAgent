from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

import discord
from discord.ext import voice_recv

from silenttalk import voice_recv_patch
from silenttalk.config import AudioConfig, DiscordConfig
from silenttalk.state import AppState
from silenttalk.tts.base import AudioResult

_LOG = logging.getLogger(__name__)
voice_recv_patch.apply()
# 20 ms of 48 kHz stereo s16le: every decoded Discord voice frame.
FRAME_BYTES = 3840
StatusCallback = Callable[[AppState, str], None]
PCMCallback = Callable[[int, str, bytes], None]
# (author id, display name, content, addressed) typed in the channel's chat.
# `addressed` is Discord's own signal: an @mention of the bot or a reply to
# one of its messages - exact, unlike matching the name as a substring.
TextCallback = Callable[[int, str, str, bool], None]
# Discord's own limit; a character's line is far shorter, but a paste is not.
MAX_TEXT_CHARS = 2000


class DiscordVoiceClient(discord.Client):
    def __init__(
        self,
        config: DiscordConfig,
        status: StatusCallback,
        pcm_callback: PCMCallback | None = None,
        text_callback: TextCallback | None = None,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        if config.text_chat:
            # Message Content is privileged: without it in the Developer
            # Portal the gateway refuses the connection, so this is opt-in.
            intents.guild_messages = True
            intents.message_content = True
        super().__init__(intents=intents)
        self._config = config
        self._status = status
        self._pcm_callback = pcm_callback
        self._text_callback = text_callback
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._voice_lock = asyncio.Lock()
        self._voice_monitor_task: asyncio.Task[None] | None = None
        self._voice_reconcile_task: asyncio.Task[None] | None = None
        self._owner_missing_since: float | None = None
        self._next_reconnect_at = 0.0
        self._reconnect_delay = config.voice_reconnect_cooldown_seconds
        self._voice_ready = asyncio.Event()
        self._listen_retries = 0
        self._receive = ReceiveStats(time.monotonic())

    def set_pcm_callback(self, callback: PCMCallback | None) -> None:
        self._pcm_callback = callback

    def apply_config(self, config: DiscordConfig) -> None:
        """Live settings. Intents and the token are startup-only, so only the
        behaviour flags and timings are taken from here."""
        self._config = config

    def set_text_callback(self, callback: TextCallback | None) -> None:
        self._text_callback = callback

    async def on_message(self, message: discord.Message) -> None:
        """Typed lines from the voice channel the bot is sitting in.

        Only that channel's own chat: SilentTalk never reads the server's
        other text channels, and nothing typed is written to disk.
        """
        callback = self._text_callback
        voice = self.active_voice()
        if callback is None or voice is None or message.channel.id != voice.channel.id:
            return
        if message.author.bot or (self.user and message.author.id == self.user.id):
            return
        content = message.clean_content.strip()
        if not content:
            return  # attachment, sticker or embed only
        name = str(
            getattr(message.author, "display_name", None)
            or getattr(message.author, "name", None)
            or message.author.id
        )
        addressed = self.user is not None and (
            self.user in message.mentions
            or getattr(getattr(message.reference, "resolved", None), "author", None)
            == self.user
        )
        _LOG.info(
            "text_message_received char_count=%d addressed=%s", len(content), addressed
        )
        callback(message.author.id, name, content, addressed)

    async def send_text(self, text: str) -> bool:
        """Say something in the voice channel's text chat. False if it could
        not be sent (not connected, missing permission, Discord error)."""
        voice = self.active_voice()
        channel = getattr(voice, "channel", None)
        if channel is None or not hasattr(channel, "send"):
            _LOG.info("text_send_skipped reason=no_channel")
            return False
        try:
            await channel.send(text[:MAX_TEXT_CHARS])
        except discord.Forbidden:
            _LOG.warning("text_send_failed reason=forbidden channel_id=%s", channel.id)
            return False
        except discord.HTTPException as exc:
            _LOG.warning("text_send_failed status=%s", getattr(exc, "status", "?"))
            return False
        return True

    async def on_ready(self) -> None:
        self._event_loop = asyncio.get_running_loop()
        _LOG.info(
            "discord_ready bot_user_id=%s", self.user.id if self.user else "unknown"
        )
        if self._voice_monitor_task is None or self._voice_monitor_task.done():
            self._voice_monitor_task = asyncio.create_task(
                self._voice_monitor(), name="voice-self-heal"
            )
        self._schedule_voice_reconcile(delay=0.0)

    async def on_resumed(self) -> None:
        _LOG.info("discord_gateway_resumed")
        self._schedule_voice_reconcile(delay=0.0)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        is_owner = member.id == self._config.owner_user_id
        is_bot = self.user is not None and member.id == self.user.id
        if before.channel == after.channel or not (is_owner or is_bot):
            return
        self._schedule_voice_reconcile()

    def _schedule_voice_reconcile(self, delay: float | None = None) -> None:
        if self.is_closed():
            return
        current = asyncio.current_task()
        if (
            self._voice_reconcile_task is not None
            and self._voice_reconcile_task is not current
        ):
            self._voice_reconcile_task.cancel()
        settle = (
            self._config.voice_state_settle_seconds
            if delay is None
            else max(0.0, delay)
        )
        self._voice_reconcile_task = asyncio.create_task(
            self._delayed_reconcile(settle), name="voice-reconcile"
        )

    async def _delayed_reconcile(self, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            await self.sync_owner_voice()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning("voice_reconcile_failed error_type=%s", type(exc).__name__)

    async def _voice_monitor(self) -> None:
        while not self.is_closed():
            try:
                await asyncio.sleep(self._config.voice_reconnect_interval_seconds)
                await self.sync_owner_voice()
                self._log_receive_stats()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOG.warning("voice_self_heal_failed error_type=%s", type(exc).__name__)

    async def close(self) -> None:
        self._voice_ready.clear()
        self._event_loop = None
        tasks = [self._voice_monitor_task, self._voice_reconcile_task]
        for task in tasks:
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not None),
            return_exceptions=True,
        )
        self._voice_monitor_task = None
        self._voice_reconcile_task = None
        await super().close()

    def _find_owner_channel(self) -> discord.abc.Connectable | None:
        for guild in self.guilds:
            for channel in guild.voice_channels:
                if self._config.owner_user_id in channel.voice_states:
                    return channel
        return None

    def voice_diagnostics(self) -> tuple[int, int, bool]:
        channels = [
            channel for guild in self.guilds for channel in guild.voice_channels
        ]
        owner_visible = any(
            self._config.owner_user_id in channel.voice_states for channel in channels
        )
        return len(self.guilds), len(channels), owner_visible

    async def sync_owner_voice(self) -> None:
        channel = self._find_owner_channel()
        now = time.monotonic()

        if channel is None:
            if self._owner_missing_since is None:
                self._owner_missing_since = now
            elapsed = now - self._owner_missing_since
            grace = self._config.owner_leave_grace_seconds
            if self.voice_clients and elapsed < grace:
                remaining = grace - elapsed
                _LOG.info("owner_voice_missing_grace remaining_seconds=%.2f", remaining)
                self._schedule_voice_reconcile(delay=remaining)
                return
        else:
            self._owner_missing_since = None

        await self._follow(channel)

    async def _follow(self, channel: discord.abc.Connectable | None) -> None:
        async with self._voice_lock:
            voices = list(self.voice_clients)
            if channel is None:
                self._voice_ready.clear()
                if self._config.disconnect_when_owner_leaves:
                    for voice in voices:
                        await self._close_voice(voice)
                self._status(AppState.OWNER_NOT_IN_VOICE, "請先加入 Discord 語音頻道")
                return

            target_guild_id = channel.guild.id
            target_voice = next(
                (
                    voice
                    for voice in voices
                    if voice.guild.id == target_guild_id and voice.is_connected()
                ),
                None,
            )

            if target_voice is not None and target_voice.channel.id == channel.id:
                self._ensure_listening(target_voice)
                self._mark_voice_healthy()
                self._status(AppState.VOICE_CONNECTED, f"已連線：{channel.name}")
                return

            if target_voice is not None:
                self._voice_ready.clear()
                try:
                    await target_voice.move_to(channel)
                except Exception:
                    _LOG.warning("voice_move_failed_reconnect")
                    await self._connect_fresh_locked(channel, voices)
                else:
                    self._ensure_listening(target_voice)
                    self._mark_voice_healthy()
                self._status(AppState.VOICE_CONNECTED, f"已連線：{channel.name}")
                return

            now = time.monotonic()
            if now < self._next_reconnect_at:
                self._voice_ready.clear()
                wait_seconds = max(0.0, self._next_reconnect_at - now)
                _LOG.info(
                    "voice_reconnect_cooldown remaining_seconds=%.2f",
                    wait_seconds,
                )
                self._schedule_voice_reconcile(delay=wait_seconds)
                return

            await self._connect_fresh_locked(channel, voices)
            self._status(AppState.VOICE_CONNECTED, f"已連線：{channel.name}")

    async def _connect_fresh_locked(
        self,
        channel: discord.abc.Connectable,
        voices: list[discord.VoiceClient] | None = None,
    ) -> None:
        self._voice_ready.clear()
        for voice in voices if voices is not None else list(self.voice_clients):
            await self._close_voice(voice)

        try:
            if self._pcm_callback is None:
                new_voice = await channel.connect(timeout=20.0, reconnect=True)
            else:
                new_voice = await channel.connect(
                    timeout=20.0,
                    reconnect=True,
                    cls=voice_recv.VoiceRecvClient,
                )
        except Exception:
            self._record_reconnect_failure()
            raise

        self._listen_retries = 0
        self._ensure_listening(new_voice)
        self._mark_voice_healthy()

    async def wait_until_voice_ready(
        self, timeout_seconds: float | None = None
    ) -> None:
        if self.active_voice() is not None:
            return

        self._voice_ready.clear()
        await self.sync_owner_voice()
        if self.active_voice() is not None:
            return

        timeout = (
            self._config.voice_ready_timeout_seconds
            if timeout_seconds is None
            else max(0.1, timeout_seconds)
        )
        try:
            await asyncio.wait_for(self._voice_ready.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise RuntimeError("Discord Voice 尚未恢復連線，請稍後再試。") from exc

        if self.active_voice() is None:
            self._voice_ready.clear()
            raise RuntimeError("Discord Voice 尚未恢復連線，請稍後再試。")

    async def recover_voice_transport(self, reason: str) -> bool:
        channel = self._find_owner_channel()
        if channel is None:
            self._voice_ready.clear()
            self._schedule_voice_reconcile()
            return False

        async with self._voice_lock:
            self._voice_ready.clear()
            voices = list(self.voice_clients)
            _LOG.warning(
                "voice_transport_recovery reason=%s stale_clients=%d",
                reason,
                len(voices),
            )

            now = time.monotonic()
            if now < self._next_reconnect_at:
                wait_seconds = max(0.0, self._next_reconnect_at - now)
                self._schedule_voice_reconcile(delay=wait_seconds)
                return False

            try:
                await self._connect_fresh_locked(channel, voices)
            except Exception as exc:
                _LOG.warning(
                    "voice_transport_recovery_failed error_type=%s",
                    type(exc).__name__,
                )
                return False

            self._status(AppState.VOICE_CONNECTED, f"已恢復：{channel.name}")
            return True

    def _ensure_listening(self, voice: discord.VoiceClient | None) -> None:
        if self._pcm_callback is None or voice is None:
            return
        if not isinstance(voice, voice_recv.VoiceRecvClient):
            _LOG.warning("voice_receive_unavailable wrong_voice_client=true")
            return
        if voice.is_listening():
            return
        connection = getattr(voice, "_connection", None)
        dave_version = int(getattr(connection, "dave_protocol_version", 0) or 0)
        if isinstance(voice.channel, discord.StageChannel):
            _LOG.warning("voice_receive_skipped reason=stage_channel")
            return
        if dave_version and getattr(connection, "dave_session", None) is None:
            # With DAVE on, frames can only be decrypted once discord.py has
            # the session; connect() can return just before it exists.
            self._listen_retries += 1
            _LOG.warning(
                "voice_receive_waiting reason=no_dave_session attempt=%d",
                self._listen_retries,
            )
            if self._listen_retries <= 10:
                self._schedule_voice_reconcile(delay=1.0)
            return
        self._listen_retries = 0
        voice.listen(
            voice_recv.BasicSink(
                lambda user, data: self._on_voice_pcm(user, data, voice)
            ),
            after=self._on_listen_stopped,
        )
        _LOG.info("voice_receive_started dave_version=%d", dave_version)

    def _on_voice_pcm(
        self,
        user: discord.Member | discord.User | None,
        data: voice_recv.VoiceData,
        voice: discord.VoiceClient | None = None,
    ) -> None:
        callback = self._pcm_callback
        if callback is None or user is None or not data.pcm:
            return
        if self.user is not None and user.id == self.user.id:
            return
        if bool(getattr(user, "bot", False)):
            return
        # DAVE on but no session yet: nothing decrypted these frames and they
        # decode to noise, which STT would turn into invented words.
        ready = voice is None or _dave_receive_ready(voice)
        if ready:
            self._receive.frame(user.id, len(data.pcm))
        else:
            self._receive.dropped += 1
            return

        name = str(
            getattr(user, "display_name", None)
            or getattr(user, "name", None)
            or user.id
        )
        callback(user.id, name, data.pcm)

    def _log_receive_stats(self) -> None:
        """Every 30 s while listening, even when no frame gets through (all
        of them failing to decrypt is exactly the case to see)."""
        now = time.monotonic()
        if now - self._receive.started < ReceiveStats.PERIOD:
            return
        voice = self.active_voice()
        stats, self._receive = self._receive, ReceiveStats(now)
        if voice is None or not getattr(voice, "is_listening", lambda: False)():
            return
        members = getattr(voice.channel, "members", [])
        stats.log(voice, now, [m.id for m in members if not getattr(m, "bot", False)])

    def _on_listen_stopped(self, error: Exception | None) -> None:
        if error is None or self.is_closed():
            return
        _LOG.warning(
            "voice_receive_stopped error_type=%s",
            type(error).__name__,
        )
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return

        def recover() -> None:
            asyncio.create_task(
                self.recover_voice_transport("voice_receive"),
                name="voice-receive-recovery",
            )

        loop.call_soon_threadsafe(recover)

    def _mark_voice_healthy(self) -> None:
        self._reconnect_delay = self._config.voice_reconnect_cooldown_seconds
        self._next_reconnect_at = 0.0
        self._voice_ready.set()

    def _record_reconnect_failure(self) -> None:
        self._voice_ready.clear()
        now = time.monotonic()
        self._next_reconnect_at = now + self._reconnect_delay
        _LOG.warning(
            "voice_reconnect_backoff delay_seconds=%.2f",
            self._reconnect_delay,
        )
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self._config.voice_reconnect_max_interval_seconds,
        )
        self._schedule_voice_reconcile(
            delay=max(0.0, self._next_reconnect_at - time.monotonic())
        )

    @staticmethod
    async def _close_voice(voice: discord.VoiceClient) -> None:
        try:
            await voice.disconnect(force=True)
        except Exception:
            voice.cleanup()

    def active_voice(self) -> discord.VoiceClient | None:
        return next(
            (vc for vc in self.voice_clients if vc.is_connected()),
            None,
        )


def _dave_receive_ready(voice: discord.VoiceClient) -> bool:
    """voice_recv decrypts DAVE frames with discord.py's session and drops the
    ones it can't; frames arriving while there is no session were not
    decrypted at all."""
    connection = getattr(voice, "_connection", None)
    if int(getattr(connection, "dave_protocol_version", 0) or 0) == 0:
        return True
    return getattr(connection, "dave_session", None) is not None


class ReceiveStats:
    """How voice arrives, in numbers only (no audio, no names): frames per
    second per speaker (50 is every frame), frames of an unexpected size,
    frames dropped for lack of a DAVE session, Opus decode errors, and the
    DAVE session's own decrypt counters per speaker."""

    PERIOD = 30.0

    def __init__(self, started: float) -> None:
        self.started = started
        self.frames: dict[int, int] = {}
        self.odd = 0
        self.dropped = 0
        self.patch_before = _patch_counters()

    def frame(self, user_id: int, size: int) -> None:
        self.frames[user_id] = self.frames.get(user_id, 0) + 1
        if size != FRAME_BYTES:
            self.odd += 1

    def log(
        self, voice: discord.VoiceClient | None, now: float, members: list[int]
    ) -> None:
        seconds = max(now - self.started, 1e-3)
        connection = getattr(voice, "_connection", None)
        version = int(getattr(connection, "dave_protocol_version", 0) or 0)
        session = getattr(connection, "dave_session", None)
        dave = f"v{version}"
        if session is not None:
            counters = []
            speakers = sorted(self.frames, key=self.frames.__getitem__, reverse=True)
            for user_id in speakers + [m for m in members if m not in self.frames]:
                try:
                    stats = session.get_decryption_stats(user_id)
                except Exception:  # davey raises for users it has no decryptor for
                    stats = None
                counters.append(
                    "-"
                    if stats is None
                    else f"{stats.attempts}/{stats.successes}/{stats.failures}/{stats.passthroughs}"
                )
            dave += (
                f" ready={int(bool(getattr(session, 'ready', False)))}"
                f" epoch={getattr(session, 'epoch', None)}"
                f" decrypt(attempts/ok/failed/passthrough)=[{' '.join(counters)}]"
            )
        rates = sorted((n / seconds for n in self.frames.values()), reverse=True)
        counters = {
            name: value - self.patch_before[name]
            for name, value in _patch_counters().items()
        }
        expected = counters["rtp_expected"]
        loss = 1 - counters["rtp_received"] / expected if expected else 0.0
        _LOG.info(
            "voice_receive_stats seconds=%.0f speakers=%d fps=[%s] odd_frames=%d "
            "dropped_no_session=%d decode_errors=%d padded=%d dave_dropped=%d "
            "unknown_ssrc=%d recoveries=%d rtp_packets=%d rtp_loss=%.1f%% dave=%s",
            seconds,
            len(self.frames),
            " ".join(f"{rate:.1f}" for rate in rates),
            self.odd,
            self.dropped,
            counters["decode_errors"],
            counters["padded"],
            counters["dave_dropped"],
            counters["unknown_ssrc"],
            counters["recoveries"],
            counters["rtp_received"],
            100 * max(0.0, loss),
            dave,
        )


def _patch_counters() -> dict[str, int]:
    return {
        name: getattr(voice_recv_patch, name)
        for name in (
            "decode_errors",
            "padded",
            "dave_dropped",
            "unknown_ssrc",
            "recoveries",
            "rtp_expected",
            "rtp_received",
        )
    }


STREAM_INPUT_OPTIONS = "-f mp3 -probesize 32 -analyzeduration 0 -fflags nobuffer"


class DiscordAudioPlayer:
    def __init__(self, client: DiscordVoiceClient, audio: AudioConfig) -> None:
        self._client = client
        self._audio = audio

    async def play(self, audio: AudioResult) -> None:
        await self._client.wait_until_voice_ready()
        try:
            await self._play_once(audio)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOG.warning(
                "voice_playback_failed_recovering error_type=%s",
                type(exc).__name__,
            )
            await self.stop()
            recovered = await self._client.recover_voice_transport(
                reason=type(exc).__name__
            )
            if recovered:
                raise RuntimeError(
                    "Discord Voice 播放中斷；連線已自動恢復。該句未重播，以避免回音。"
                ) from exc
            raise RuntimeError(
                "Discord Voice 播放中斷；正在背景恢復連線。該句未重播，以避免回音。"
            ) from exc

    async def _play_once(self, audio: AudioResult) -> None:
        voice = self._client.active_voice()
        if voice is None:
            raise RuntimeError("Discord Voice 尚未建立可用連線。")

        loop = asyncio.get_running_loop()
        finished: asyncio.Future[None] = loop.create_future()

        def after(error: Exception | None) -> None:
            def complete() -> None:
                if finished.done():
                    return
                if error:
                    finished.set_exception(error)
                else:
                    finished.set_result(None)

            loop.call_soon_threadsafe(complete)

        options = "-vn"
        if audio.ffmpeg_filter:
            options += f' -af "{audio.ffmpeg_filter}"'
        if audio.stream is not None:
            # MP3 still arriving on stdin: say what it is, or FFmpeg buffers
            # input to probe it (first packet 602 ms -> 267 ms median).
            source = discord.FFmpegOpusAudio(
                audio.stream,
                pipe=True,
                executable=self._audio.ffmpeg_path,
                before_options=STREAM_INPUT_OPTIONS,
                options=options,
            )
        else:
            source = discord.FFmpegOpusAudio(
                str(audio.path),
                executable=self._audio.ffmpeg_path,
                options=options,
            )
        voice.play(source, after=after)
        await asyncio.wait_for(
            finished,
            timeout=self._audio.playback_timeout_seconds,
        )

    async def stop(self) -> None:
        voice = self._client.active_voice()
        if voice is not None and voice.is_playing():
            stop_playing = getattr(voice, "stop_playing", None)
            if callable(stop_playing):
                stop_playing()
            else:
                voice.stop()


async def play_smoke_tone(
    client: DiscordVoiceClient,
    audio: AudioConfig,
    path: Path,
) -> None:
    await client.wait_until_voice_ready()
    await DiscordAudioPlayer(client, audio).play(
        AudioResult(path, None, None, path.suffix.lstrip("."))
    )
