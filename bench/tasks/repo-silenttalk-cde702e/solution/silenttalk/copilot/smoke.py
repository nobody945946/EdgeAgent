"""Discord-free Copilot checks: local STT accuracy and local LLM speed/format.

`python app.py --copilot-smoke` runs both gates on the real ASR models and the
independent EdgeAgent so a new machine (e.g. an RTX 3050 Ti laptop) can be verified
without joining a voice channel. scripts/dev/ reuses the same helpers.
"""

from __future__ import annotations

import asyncio
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from silenttalk.config import CopilotConfig, Settings
from silenttalk.copilot.context import ConversationStore
from silenttalk.copilot.llm import LocalReplyModel
from silenttalk.copilot.script import to_taiwan_traditional
from silenttalk.copilot.stt import StreamingTranscriber
from silenttalk.copilot.stt_offline import OfflineTranscriber
from silenttalk.copilot.types import REPLY_LANES, ConversationTurn, ReplySuggestions
from silenttalk.tts.edge import EdgeTTSEngine

DISCORD_FRAME_BYTES = 3840  # 20 ms of 48 kHz stereo s16le, what voice_recv delivers

# Baseline on 2026-09-25 with the bundled Paraformer: CER ~0% on these five
# (no English words); a regression past 15% means something broke.
STT_SENTENCES = (
    "左邊有人，快點過來幫我",
    "等一下我去倒個水馬上回來",
    "剛剛那波團戰真的打得很好",
    "你們有人看過昨天的直播嗎",
    "誰有空幫我解一下這個任務",
)
STT_MAX_CER = 0.15

# (speaker, text) turns as they look after STT + s2twp: Traditional, no punctuation.
# "你" marks lines the owner actually said.
LLM_SCENES: tuple[tuple[tuple[str, str], ...], ...] = (
    (
        ("阿傑", "欸我們等等要不要直接衝B點"),
        ("小雨", "上一把衝B被架死欸"),
        ("你", "我覺得可以先丟煙"),
        ("阿傑", "可是他們B點只有一個人在守 直接衝就好了吧"),
    ),
    (
        ("小雨", "我今天終於把那個boss打過了"),
        ("阿傑", "哪個"),
        ("小雨", "就是第三章那個會分身的 打了快兩個小時"),
    ),
    (("Ken", "你們有人知道怎麼退掉Steam的遊戲嗎 我買錯了"),),
    (
        ("阿傑", "我這邊一直lag 是不是伺服器的問題"),
        ("Ken", "我這邊正常啊"),
        ("阿傑", "幹 那就是我家網路又爛了"),
    ),
    (
        ("小雨", "我明天要面試 有點緊張"),
        ("你", "加油！"),
        ("小雨", "我怕他問我為什麼離職 我不知道要怎麼講"),
    ),
    (
        ("Ken", "剛剛那波是誰沒開大"),
        ("阿傑", "不是我喔"),
        ("Ken", "我們就是輸在那波團戰"),
    ),
    (
        ("小雨", "你們晚餐吃什麼"),
        ("阿傑", "還沒想 要不要一起叫外送"),
        ("小雨", "好啊 但我不要再吃麥當勞了"),
    ),
    (
        ("阿傑", "我覺得這遊戲的新角色超爛 根本沒人會玩"),
        ("Ken", "可是排位勝率好像蠻高的"),
        ("阿傑", "那是因為大家不會打他而已"),
    ),
)
# Four sampled options on a 3050 Ti Laptop: Gemma 4 E2B ~2.0 s, E4B (the
# default since 2026-09-29, clearly better lines) 3.0 s. Past 4 s the story-mode
# choices feel laggy, or the model fell off the GPU.
LLM_MAX_MEDIAN_SECONDS = 4.0
# Sampling may not find four different lines, so three of four is the floor.
LLM_MIN_LANES = 3
SUMMARY_MARKER = "藍鯨七號"


@dataclass(frozen=True, slots=True)
class SceneResult:
    trigger: str
    seconds: float
    suggestions: ReplySuggestions

    @property
    def lanes(self) -> int:
        return sum(bool(text) for _, text in self.suggestions.items())


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Character error rate after script/punctuation normalization (English = letters)."""

    def normalize(text: str) -> str:
        converted = to_taiwan_traditional(text).lower()
        return "".join(ch for ch in converted if ch.isalnum())

    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, ref_ch in enumerate(ref, 1):
        diagonal, row[0] = row[0], i
        for j, hyp_ch in enumerate(hyp, 1):
            diagonal, row[j] = (
                row[j],
                min(row[j] + 1, row[j - 1] + 1, diagonal + (ref_ch != hyp_ch)),
            )
    return row[-1] / len(ref)


async def synthesize_discord_pcm(
    engine: EdgeTTSEngine, text: str, ffmpeg: str, voice_style: str = "default"
) -> bytes:
    """EdgeTTS -> 48 kHz stereo s16le, the same PCM shape Discord receive yields."""
    audio = await engine.synthesize(text, voice_style=voice_style)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [ffmpeg, "-v", "error", "-i", str(audio.path)]
            + ["-f", "s16le", "-ac", "2", "-ar", "48000", "-"],
            capture_output=True,
            check=True,
        )
    finally:
        audio.path.unlink(missing_ok=True)
    return result.stdout


def transcribe_pcm(
    transcriber: StreamingTranscriber | OfflineTranscriber,
    collector: TurnCollector,
    pcm: bytes,
    timeout: float = 8.0,
) -> str:
    """Feed one utterance in 20 ms frames and wait for its final transcript(s)."""
    collector.reset()
    for start in range(0, len(pcm), DISCORD_FRAME_BYTES):
        transcriber.push_pcm(1, "smoke", pcm[start : start + DISCORD_FRAME_BYTES])
    return collector.wait(timeout)


class TurnCollector:
    """Collects on_turn callbacks; one utterance may be split by endpoint rules."""

    def __init__(self) -> None:
        self._texts: list[str] = []
        self._event = threading.Event()
        self._lock = threading.Lock()

    def on_turn(self, _speaker_id: int, _speaker_name: str, text: str) -> None:
        with self._lock:
            self._texts.append(text)
            self._event.set()

    def reset(self) -> None:
        with self._lock:
            self._texts.clear()
            self._event.clear()

    def wait(self, timeout: float) -> str:
        self._event.wait(timeout)
        time.sleep(1.0)  # let a trailing split segment land
        with self._lock:
            return " ".join(self._texts)


def build_transcriber(
    project_dir: Path, config: CopilotConfig
) -> tuple[StreamingTranscriber | OfflineTranscriber, TurnCollector]:
    """Same engine + model dir selection as CopilotRuntime (config.stt_engine)."""
    collector = TurnCollector()
    models_dir = project_dir / ".runtime" / "models"
    if config.stt_engine == "sensevoice":
        transcriber_class, model_dir = OfflineTranscriber, models_dir / "sensevoice"
    else:
        transcriber_class, model_dir = StreamingTranscriber, models_dir / "asr"
    transcriber = transcriber_class(
        model_dir,
        on_turn=collector.on_turn,
        on_error=lambda message: print(f"  STT error: {message}"),
        endpoint_silence_ms=config.endpoint_silence_ms,
    )
    return transcriber, collector


def scene_prompt_inputs(
    scene: tuple[tuple[str, str], ...],
) -> tuple[list[ConversationTurn], str, str]:
    """Returns (turns, trigger speaker, trigger text) exactly as runtime does."""
    store = ConversationStore()
    for index, (speaker, text) in enumerate(scene):
        if speaker == "你":
            store.append_self(1, text)
        else:
            store.append_external(100 + index, speaker, text)
    speaker, text = next((s, t) for s, t in reversed(scene) if s != "你")
    return store.snapshot(), speaker, text


def build_reply_model(
    project_dir: Path, config: CopilotConfig, model_file: str | None = None
) -> LocalReplyModel:
    return LocalReplyModel(
        Path(model_file or config.llm_model),
        port=config.llama_port,
        context_size=config.llm_context_size,
        max_reply_chars=config.max_reply_chars,
        gpu_layers=config.llm_gpu_layers,
        low_vram=config.llm_low_vram,
        lora_path=(
            Path(config.character_lora) if config.character_lora else None
        ),
        lora_uses=config.character_lora_uses,
        project_dir=project_dir,
    )


async def run_scenes(
    model: LocalReplyModel, scenes: tuple[tuple[tuple[str, str], ...], ...]
) -> list[SceneResult]:
    results: list[SceneResult] = []
    for scene in scenes:
        turns, speaker, text = scene_prompt_inputs(scene)
        started = time.perf_counter()
        suggestions = await model.suggest(
            turns=turns, trigger_speaker=speaker, trigger_text=text
        )
        results.append(SceneResult(text, time.perf_counter() - started, suggestions))
    return results


async def run_copilot_smoke(settings: Settings, project_dir: Path) -> bool:
    """Prints a report; returns True only if both gates pass."""
    stt_ok = await _stt_gate(settings, project_dir)
    llm_ok = await _llm_gate(settings, project_dir)
    print()
    print(
        f"Copilot smoke: STT {'PASS' if stt_ok else 'FAIL'} / LLM {'PASS' if llm_ok else 'FAIL'}"
    )
    return stt_ok and llm_ok


async def _stt_gate(settings: Settings, project_dir: Path) -> bool:
    engine_name = {"sensevoice": "SenseVoice"}.get(
        settings.copilot.stt_engine, "Paraformer"
    )
    print(f"== Gate C1: 本機 STT（EdgeTTS 產生語音 → {engine_name}）")
    transcriber, collector = build_transcriber(project_dir, settings.copilot)
    await asyncio.to_thread(transcriber.start)
    rates: list[float] = []
    try:
        with tempfile.TemporaryDirectory(prefix="silenttalk-copilot-smoke-") as temp:
            engine = EdgeTTSEngine(settings.tts, Path(temp))
            await engine.initialize()
            try:
                for sentence in STT_SENTENCES:
                    pcm = await synthesize_discord_pcm(
                        engine, sentence, settings.audio.ffmpeg_path
                    )
                    heard = await asyncio.to_thread(
                        transcribe_pcm, transcriber, collector, pcm
                    )
                    rate = char_error_rate(sentence, heard)
                    rates.append(rate)
                    print(f"  {rate:5.0%}  說：{sentence}｜聽到：{heard or '（沒有）'}")
            finally:
                await engine.shutdown()
    finally:
        await asyncio.to_thread(transcriber.stop)
    average = statistics.fmean(rates)
    passed = average <= STT_MAX_CER
    print(
        f"  平均 CER {average:.1%}（門檻 {STT_MAX_CER:.0%}）→ {'PASS' if passed else 'FAIL'}"
    )
    return passed


async def _llm_gate(settings: Settings, project_dir: Path) -> bool:
    print("== Gate C2: 獨立 EdgeAgent（設定中的模型）")
    model = build_reply_model(project_dir, settings.copilot)
    started = time.perf_counter()
    await model.start()
    print(
        f"  模型 {settings.copilot.llm_model} 載入 {time.perf_counter() - started:.1f}s"
    )
    try:
        results = await run_scenes(model, LLM_SCENES[:3])
        first_transcript = "\n".join(
            [f"阿傑：大家決定這次測試代號是{SUMMARY_MARKER}，之後要記得"]
            + [f"小雨：這是第一段聊天的第{n}件小事" for n in range(1, 32)]
        )
        second_transcript = "\n".join(
            f"Ken：這是後來聊天的第{n}件小事" for n in range(32, 64)
        )
        summary_started = time.perf_counter()
        first_summary = await model.summarize(first_transcript)
        final_summary = await model.summarize(second_transcript, first_summary)
        summary_seconds = time.perf_counter() - summary_started
    finally:
        await model.stop()
    for result in results:
        print(f"  {result.seconds:4.1f}s  {result.lanes}/4 格｜對方：{result.trigger}")
        for lane in REPLY_LANES:
            print(
                f"        {lane.key.upper()}：{result.suggestions.text_for(lane) or '—'}"
            )
    median = statistics.median(r.seconds for r in results)
    format_ok = all(r.lanes >= LLM_MIN_LANES for r in results)
    speed_ok = median <= LLM_MAX_MEDIAN_SECONDS
    print(
        f"  延遲中位數 {median:.2f}s（門檻 {LLM_MAX_MEDIAN_SECONDS:.0f}s）"
        f"{'' if speed_ok else ' ← 太慢'}；格式 {'OK' if format_ok else '不合格（模型沒照四行格式）'}"
    )
    summary_ok = SUMMARY_MARKER in first_summary and SUMMARY_MARKER in final_summary
    print(
        f"  兩輪長對話摘要 {summary_seconds:.1f}s；"
        f"早期代號 {'PASS' if summary_ok else 'FAIL'}"
    )
    return format_ok and speed_ok and summary_ok
