"""Real-voice STT check: the owner reads fixed sentences in the voice channel.

`python app.py --stt-check` joins the owner's voice channel the way the app
does and listens only to the owner. The console shows one sentence at a
time; what the local STT heard is scored against it (character error rate).
Transcripts stay in memory: only rates and counts are printed and logged,
so nobody has to judge transcripts by eye.

The same sentences are also taken straight from this computer's default
microphone (when sounddevice can open it), bypassing Discord, into a second
recogniser: both paths about equally wrong means the recogniser is the
limit; Discord's path much worse means the transport. The
voice_receive_stats and copilot_stt_segment lines logged meanwhile say more
(frames not decrypted or dropped, audio that looks like noise).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from silenttalk.config import Settings
from silenttalk.copilot.smoke import (
    DISCORD_FRAME_BYTES,
    STT_MAX_CER,
    STT_SENTENCES,
    build_transcriber,
    char_error_rate,
)
from silenttalk.discord_voice import DiscordVoiceClient
from silenttalk.state import AppState

_LOG = logging.getLogger(__name__)

CHECK_SENTENCES = STT_SENTENCES + (
    "今天晚上七點半在老地方集合",
    "我覺得這把我們應該先守一下",
    "阿傑你那邊還有幾個人",
)
SENTENCE_TIMEOUT = 20.0


async def run_stt_check(settings: Settings, project_dir: Path) -> bool:
    owner = settings.discord.owner_user_id
    transcriber, collector = build_transcriber(project_dir, settings.copilot)

    def on_pcm(user_id: int, name: str, pcm: bytes) -> None:
        if user_id == owner:
            transcriber.push_pcm(user_id, name, pcm)

    def status(state: AppState, message: str) -> None:
        print(f"{state.value}: {message}")

    _LOG.info("copilot_stt_check_start sentences=%d", len(CHECK_SENTENCES))
    await asyncio.to_thread(transcriber.start)
    local, local_collector = build_transcriber(project_dir, settings.copilot)
    await asyncio.to_thread(local.start)
    microphone = _open_microphone(lambda pcm: local.push_pcm(0, "local", pcm))
    if microphone is None:
        print("（本機麥克風開不起來，只量 Discord 這條路）")
    else:
        print(f"本機麥克風：{microphone.name}")
        microphone.stream.start()
    client = DiscordVoiceClient(settings.discord, status, pcm_callback=on_pcm)
    rates: list[float] = []
    local_rates: list[float] = []
    try:
        async with client:
            await client.login(settings.discord.token)
            connect_task = asyncio.create_task(client.connect(reconnect=False))
            try:
                await asyncio.wait_for(client.wait_until_ready(), timeout=30)
                print("請先進 Discord 語音頻道；Bot 會跟著進來。")
                await _wait_listening(client, timeout=120)
                # Let the DAVE handshake settle before the first sentence.
                await asyncio.sleep(3)
                print(
                    f"\n接下來一句一句顯示 {len(CHECK_SENTENCES)} 句話，用平常的速度念。"
                    "念完按 Enter 看下一句；念錯了按 r 再念一次；不想念的按 s 跳過"
                    "（跳過的不算）。\n"
                )
                index = 0
                while index < len(CHECK_SENTENCES):
                    sentence = CHECK_SENTENCES[index]
                    print(f"[{index + 1}/{len(CHECK_SENTENCES)}] 請念：{sentence}")
                    collector.reset()
                    local_collector.reset()
                    if microphone is not None:
                        microphone.peak_dbfs = -120.0
                    heard = await asyncio.to_thread(collector.wait, SENTENCE_TIMEOUT)
                    rate = char_error_rate(sentence, heard) if heard else 1.0
                    local_heard, local_rate = "", -1.0
                    if microphone is not None:
                        local_heard = await asyncio.to_thread(local_collector.wait, 5.0)
                        local_rate = (
                            char_error_rate(sentence, local_heard)
                            if local_heard
                            else 1.0
                        )
                    print(
                        (
                            f"    Discord：錯字率 {rate:.0%}（聽到 {len(heard)} 字）"
                            if heard
                            else f"    Discord：{SENTENCE_TIMEOUT:.0f} 秒內沒有辨識結果"
                        )
                        + (
                            f"｜本機麥克風：錯字率 {local_rate:.0%}（聽到 {len(local_heard)} 字，"
                            f"最大音量 {microphone.peak_dbfs:.0f} dBFS）"
                            if microphone is not None
                            else ""
                        )
                    )
                    choice = (
                        (
                            await asyncio.to_thread(
                                input, "    Enter 下一句／r 再念／s 跳過："
                            )
                        )
                        .strip()
                        .lower()
                    )
                    if choice == "r":
                        continue
                    skipped = choice == "s"
                    _LOG.info(
                        "copilot_stt_check index=%d cer=%.3f heard_chars=%d "
                        "local_cer=%.3f local_chars=%d mic_peak_dbfs=%.0f skipped=%d",
                        index + 1,
                        rate,
                        len(heard),
                        local_rate,
                        len(local_heard),
                        microphone.peak_dbfs if microphone is not None else -120.0,
                        int(skipped),
                    )
                    if not skipped:
                        rates.append(rate)
                        if microphone is not None:
                            local_rates.append(local_rate)
                    index += 1
            finally:
                await client.close()
                await asyncio.gather(connect_task, return_exceptions=True)
    finally:
        if microphone is not None:
            microphone.stream.close()
        await asyncio.to_thread(local.stop)
        await asyncio.to_thread(transcriber.stop)
    average = statistics.fmean(rates) if rates else 1.0
    local_average = statistics.fmean(local_rates) if local_rates else -1.0
    heard_count = sum(rate < 1.0 for rate in rates)
    passed = bool(rates) and average <= STT_MAX_CER
    _LOG.info(
        "copilot_stt_check_done sentences=%d cer=%.3f passed=%d local_cer=%.3f",
        len(rates),
        average,
        int(passed),
        local_average,
    )
    print(
        f"\n經過 Discord：平均錯字率 {average:.1%}（{len(rates)} 句，門檻 {STT_MAX_CER:.0%}），"
        f"{heard_count}/{len(rates)} 句有結果 → {'PASS' if passed else 'FAIL'}"
    )
    if local_rates:
        print(f"本機麥克風直接收：平均錯字率 {local_average:.1%}")
    return passed


class _Microphone:
    def __init__(self, stream: Any, name: str) -> None:
        self.stream = stream
        self.name = name
        self.peak_dbfs = -120.0


def _open_microphone(push: Callable[[bytes], None]) -> _Microphone | None:
    """The default microphone as 20 ms frames of 48 kHz stereo s16le (the
    shape Discord receive yields), with its loudest 20 ms level since the
    last reset; None when it can't be opened (no sounddevice or PortAudio,
    no input device)."""
    try:
        import sounddevice
    except (ImportError, OSError):
        return None
    pending = bytearray()
    microphone: _Microphone | None = None

    def on_audio(indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        samples = indata[:, 0]
        if microphone is not None and len(samples):
            rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) / 32768
            microphone.peak_dbfs = max(
                microphone.peak_dbfs, 20 * np.log10(max(rms, 1e-6))
            )
        pending.extend(np.repeat(samples, 2).astype("<i2").tobytes())
        while len(pending) >= DISCORD_FRAME_BYTES:
            push(bytes(pending[:DISCORD_FRAME_BYTES]))
            del pending[:DISCORD_FRAME_BYTES]

    try:
        stream = sounddevice.InputStream(
            samplerate=48_000,
            channels=1,
            dtype="int16",
            blocksize=960,
            callback=on_audio,
        )
    except Exception as exc:  # noqa: BLE001 - any device error means "no local path"
        _LOG.warning(
            "copilot_stt_check_no_microphone error_type=%s", type(exc).__name__
        )
        return None
    try:
        name = str(sounddevice.query_devices(kind="input")["name"])
    except Exception:  # noqa: BLE001 - the name is only for the report
        name = "?"
    microphone = _Microphone(stream, name)
    return microphone


async def _wait_listening(client: DiscordVoiceClient, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        voice = client.active_voice()
        if voice is not None and getattr(voice, "is_listening", lambda: False)():
            return
        if loop.time() >= deadline:
            raise RuntimeError(f"{timeout:.0f} 秒內沒有開始收音（Bot 沒進語音頻道？）")
        await asyncio.sleep(0.5)
