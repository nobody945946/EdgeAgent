from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from dotenv import load_dotenv

from silenttalk.copilot.character import AI_HONESTY_MODES, ENGAGEMENT_MODES
from silenttalk.settings_store import SETTINGS_FILE, SettingsStore, merge

DEFAULT_LLM_MODEL = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
STT_ENGINES = ("paraformer", "sensevoice")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AppConfig:
    speak_hotkey: str = "F8"
    stop_hotkey: str = "F9"
    max_text_length: int = 300


@dataclass(frozen=True, slots=True)
class TTSConfig:
    engine: str = "edge"
    voice: str = ""
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    timeout_seconds: float = 20.0


@dataclass(frozen=True, slots=True)
class DiscordConfig:
    token: str
    owner_user_id: int
    disconnect_when_owner_leaves: bool = True
    voice_reconnect_interval_seconds: float = 20.0
    voice_reconnect_max_interval_seconds: float = 60.0
    voice_state_settle_seconds: float = 1.5
    owner_leave_grace_seconds: float = 8.0
    voice_reconnect_cooldown_seconds: float = 2.0
    voice_ready_timeout_seconds: float = 12.0
    # Read and answer in the voice channel's built-in text chat. Needs the
    # Message Content Intent on the bot (Discord Developer Portal).
    text_chat: bool = False


@dataclass(frozen=True, slots=True)
class AudioConfig:
    ffmpeg_path: str = "ffmpeg"
    playback_timeout_seconds: float = 90.0


@dataclass(frozen=True, slots=True)
class CopilotConfig:
    enabled: bool = True
    endpoint_silence_ms: int = 650
    llm_context_size: int = 4096
    recent_turns: int = 16
    max_session_turns: int = 100
    context_char_budget: int = 2400
    max_reply_chars: int = 72
    llama_port: int = 18088
    auto_popup: bool = True
    llm_model: str = DEFAULT_LLM_MODEL
    llm_gpu_layers: int = 99
    llm_low_vram: bool = True
    # Character-mode LoRA adapter in .runtime/models/llm/ ("" = none), and
    # which requests use it (copilot.llm.LORA_PARTS); summaries never do.
    character_lora: str = ""
    character_lora_uses: tuple[str, ...] = ("called", "active", "line")
    # "called": answer only when the model judges a line is for the
    # character. "active": also join in by itself after a quiet stretch.
    # Auto-compaction: older turns are folded into a short summary by the
    # local model, so a long session keeps its thread at a fixed prompt size.
    compact_context: bool = True
    compact_keep_turns: int = 20
    compact_batch_turns: int = 12
    # Asked whether it is an AI: "in_character" keeps the act, "admit" owns up.
    character_ai_honesty: str = "in_character"
    character_engagement: str = "called"
    stt_engine: str = "paraformer"


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppConfig
    tts: TTSConfig
    discord: DiscordConfig
    audio: AudioConfig
    copilot: CopilotConfig

    def validate_runtime(self) -> list[str]:
        issues: list[str] = []
        ffmpeg = self.audio.ffmpeg_path
        if not Path(ffmpeg).is_file() and shutil.which(ffmpeg) is None:
            issues.append(f"找不到 FFmpeg：{ffmpeg}")
        return issues


def _section(data: dict, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"config.toml 的 [{name}] 必須是 table。")
    return value


def _one_of(value: object, allowed: list[str], field: str) -> str:
    choice = str(value).strip().lower()
    if choice not in allowed:
        raise ConfigError(f"copilot.{field} 只能是 {' 或 '.join(allowed)}。")
    return choice


def _stt_engine(value: object) -> str:
    engine = str(value).strip().lower()
    if engine not in STT_ENGINES:
        raise ConfigError(
            'copilot.stt_engine 只能是 "paraformer" 或 "sensevoice"。'
        )
    return engine


def _model_filename(value: object) -> str:
    name = str(value).strip()
    if not name.lower().endswith(".gguf") or Path(name).name != name:
        raise ConfigError("copilot.llm_model 必須是 .runtime/models/llm/ 內的 .gguf 檔名。")
    return name


def _lora_uses(value: object) -> tuple[str, ...]:
    from silenttalk.copilot.llm import LORA_PARTS

    if value is None:
        return ("called", "active", "line")
    parts = value if isinstance(value, list) else str(value).split(",")
    chosen = tuple(str(part).strip() for part in parts if str(part).strip())
    unknown = [part for part in chosen if part not in LORA_PARTS]
    if unknown:
        raise ConfigError(
            f"copilot.character_lora_uses: {unknown} 不是 {', '.join(LORA_PARTS)} 之一"
        )
    return chosen


def _optional_model_filename(value: object) -> str:
    return _model_filename(value) if str(value).strip() else ""


def config_defaults(project_dir: Path) -> dict[str, dict[str, object]]:
    """config.toml as-is: what the settings page calls "the default"."""
    config_path = project_dir / "config.toml"
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    tables = {
        name: dict(values)
        for name, values in raw.items()
        if isinstance(values, dict)
    }
    # Values the file leaves out still have a default in the dataclasses;
    # the page needs them to show a starting position.
    for section, config in (
        ("app", AppConfig("F8", "F9")),
        ("tts", TTSConfig()),
        ("discord", None),
        ("audio", AudioConfig()),
        ("copilot", CopilotConfig()),
    ):
        table = tables.setdefault(section, {})
        if config is None:
            continue
        for name, value in asdict(config).items():
            table.setdefault(name, value)
    return tables


def load_settings(project_dir: Path) -> Settings:
    load_dotenv(project_dir / ".env")
    config_path = project_dir / "config.toml"
    if not config_path.exists():
        raise ConfigError(f"找不到唯一設定檔：{config_path}")
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    # What the settings page changed wins over the file's defaults, and then
    # goes through exactly the same validation below.
    raw = merge(raw, SettingsStore(project_dir / SETTINGS_FILE).load())

    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    owner_raw = os.getenv("OWNER_USER_ID", "").strip()
    if not token:
        raise ConfigError(".env 缺少 DISCORD_BOT_TOKEN。")
    if not owner_raw.isdecimal() or int(owner_raw) <= 0:
        raise ConfigError(".env 的 OWNER_USER_ID 必須是正整數 Discord User ID。")

    app = _section(raw, "app")
    tts = _section(raw, "tts")
    discord = _section(raw, "discord")
    audio = _section(raw, "audio")
    copilot = _section(raw, "copilot")
    engine = str(tts.get("engine", "edge")).lower()
    if engine != "edge":
        raise ConfigError('MVP 目前只支援 tts.engine = "edge"。')

    app_config = AppConfig(
        speak_hotkey=str(app.get("speak_hotkey", "F8")).upper(),
        stop_hotkey=str(app.get("stop_hotkey", "F9")).upper(),
        max_text_length=int(app.get("max_text_length", 300)),
    )
    if app_config.speak_hotkey == app_config.stop_hotkey:
        raise ConfigError("speak_hotkey 與 stop_hotkey 不可相同。")
    if app_config.max_text_length <= 0:
        raise ConfigError("max_text_length 必須大於 0。")

    reconnect_interval = max(
        5.0, float(discord.get("voice_reconnect_interval_seconds", 20.0))
    )
    reconnect_max = max(
        reconnect_interval,
        float(discord.get("voice_reconnect_max_interval_seconds", 60.0)),
    )

    return Settings(
        app=app_config,
        tts=TTSConfig(
            engine=engine,
            voice=str(tts.get("voice", "")),
            rate=str(tts.get("rate", "+0%")),
            pitch=str(tts.get("pitch", "+0Hz")),
            volume=str(tts.get("volume", "+0%")),
            timeout_seconds=max(1.0, float(tts.get("timeout_seconds", 20.0))),
        ),
        discord=DiscordConfig(
            token=token,
            owner_user_id=int(owner_raw),
            disconnect_when_owner_leaves=bool(
                discord.get("disconnect_when_owner_leaves", True)
            ),
            voice_reconnect_interval_seconds=reconnect_interval,
            voice_reconnect_max_interval_seconds=reconnect_max,
            voice_state_settle_seconds=max(
                0.25, float(discord.get("voice_state_settle_seconds", 1.5))
            ),
            owner_leave_grace_seconds=max(
                0.0, float(discord.get("owner_leave_grace_seconds", 8.0))
            ),
            voice_reconnect_cooldown_seconds=max(
                0.5, float(discord.get("voice_reconnect_cooldown_seconds", 2.0))
            ),
            voice_ready_timeout_seconds=max(
                1.0, float(discord.get("voice_ready_timeout_seconds", 12.0))
            ),
            text_chat=bool(discord.get("text_chat", False)),
        ),
        audio=AudioConfig(
            ffmpeg_path=str(audio.get("ffmpeg_path", "ffmpeg")),
            playback_timeout_seconds=max(
                10.0, float(audio.get("playback_timeout_seconds", 90.0))
            ),
        ),
        copilot=CopilotConfig(
            enabled=bool(copilot.get("enabled", True)),
            endpoint_silence_ms=max(
                350, min(2000, int(copilot.get("endpoint_silence_ms", 650)))
            ),
            llm_context_size=max(
                1024, min(4096, int(copilot.get("llm_context_size", 4096)))
            ),
            recent_turns=max(4, min(24, int(copilot.get("recent_turns", 16)))),
            max_session_turns=max(
                20, min(200, int(copilot.get("max_session_turns", 100)))
            ),
            context_char_budget=max(
                600, min(5000, int(copilot.get("context_char_budget", 2400)))
            ),
            max_reply_chars=max(
                24, min(160, int(copilot.get("max_reply_chars", 72)))
            ),
            llama_port=max(1024, min(65535, int(copilot.get("llama_port", 18088)))),
            auto_popup=bool(copilot.get("auto_popup", True)),
            llm_model=_model_filename(
                copilot.get("llm_model", DEFAULT_LLM_MODEL)
            ),
            llm_gpu_layers=max(0, min(999, int(copilot.get("llm_gpu_layers", 99)))),
            llm_low_vram=bool(copilot.get("llm_low_vram", True)),
            character_lora=_optional_model_filename(copilot.get("character_lora", "")),
            character_lora_uses=_lora_uses(copilot.get("character_lora_uses")),
            compact_context=bool(copilot.get("compact_context", True)),
            compact_keep_turns=max(8, min(200, int(copilot.get("compact_keep_turns", 20)))),
            compact_batch_turns=max(4, min(200, int(copilot.get("compact_batch_turns", 12)))),
            character_ai_honesty=_one_of(
                copilot.get("character_ai_honesty", "in_character"),
                [mode for mode, _ in AI_HONESTY_MODES],
                "character_ai_honesty",
            ),
            character_engagement=_one_of(
                copilot.get("character_engagement", "called"),
                [mode for mode, _ in ENGAGEMENT_MODES],
                "character_engagement",
            ),
            stt_engine=_stt_engine(copilot.get("stt_engine", "paraformer")),
        ),
    )
