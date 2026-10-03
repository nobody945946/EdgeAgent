"""What the settings page shows, in the order it shows it.

One declarative list so a new setting means one entry here, not new widgets.
`config.toml` stays the file of defaults; the page writes only what the user
changed into settings.json (see settings_store.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from silenttalk.config import MEMORY_LEVEL_CHOICES, STT_ENGINES
from silenttalk.copilot.character import AI_HONESTY_MODES, ENGAGEMENT_MODES
from silenttalk.voice_styles import PERSONA_VOICES


@dataclass(frozen=True, slots=True)
class Setting:
    section: str  # config.toml table
    key: str
    label: str
    kind: str  # bool | int | float | choice | text
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float = 0.0
    maximum: float = 0.0
    step: float = 1.0
    unit: str = ""
    # Reading a value at startup only: changing it needs SilentTalk restarted.
    restart: bool = False


@dataclass(frozen=True, slots=True)
class Group:
    title: str
    subtitle: str = ""
    settings: tuple[Setting, ...] = ()
    # Rarely touched: folded under the page's 「進階」 section.
    advanced: bool = False


@dataclass(frozen=True, slots=True)
class Page:
    key: str
    label: str
    groups: tuple[Group, ...] = field(default_factory=tuple)


_VOICE_CHOICES = tuple(
    (voice or "auto", label if voice else "自動（台灣女聲）") for voice, label in PERSONA_VOICES
)

PAGES: tuple[Page, ...] = (
    Page("chat", "聊天", (
        Group(
            "角色什麼時候開口",
            "角色模式下，Bot 自己決定要不要接話。",
            (
                Setting(
                    "copilot", "character_engagement", "開口時機", "choice",
                    "被叫到才回：有人叫它、問它或回應它時才接話（每句由本機模型判斷）。"
                    "會主動加入：話題合適時它也會自己插話。",
                    choices=ENGAGEMENT_MODES,
                ),
                Setting(
                    "copilot", "character_lull_seconds", "冷場時找話聊", "int",
                    "頻道安靜這麼多秒後，角色會說說自己今天在想的事，或問問大家最近怎樣。0＝不會。",
                    minimum=0, maximum=300, step=5, unit="秒",
                ),
                Setting(
                    "copilot", "character_ai_honesty", "被問「你是AI嗎」", "choice",
                    "照角色演：當成朋友開玩笑，用角色的方式帶過，不承認自己是程式——"
                    "角色比較完整。承認：認真被問時老實說自己是 AI 扮演的角色。",
                    choices=AI_HONESTY_MODES,
                ),
            ),
        ),
        Group(
            "記憶",
            "聊很久也不會忘記前面在幹嘛，而且 prompt 不會越來越大。",
            (
                Setting(
                    "copilot", "compact_context", "自動壓縮舊對話", "bool",
                    "超過保留量時，較早的對話由本機模型濃縮成一兩句重點留著。",
                ),
                Setting(
                    "copilot", "compact_keep_turns", "原文保留句數", "int",
                    "這麼多句以內保持原樣，更早的才會被濃縮。",
                    minimum=8, maximum=200, step=2,
                ),
                Setting(
                    "copilot", "compact_batch_turns", "每次濃縮句數", "int",
                    "超過保留量這麼多句時才啟動一次濃縮。",
                    minimum=4, maximum=200, step=2,
                ),
                Setting(
                    "copilot", "character_memory", "角色記得自己的生活", "choice",
                    "記得的話，他的心事會隨聊天往前走（約成了、放下了、有新的），下次開啟接著過；"
                    "只存他自己的狀態，不存對話內容，可在人設頁清除。",
                    choices=MEMORY_LEVEL_CHOICES,
                ),
                Setting(
                    "copilot", "max_session_turns", "記憶體保留句數", "int",
                    "這次執行最多留這麼多句在記憶體；不會寫進硬碟。",
                    minimum=20, maximum=500, step=10,
                ),
            ),
            advanced=True,
        ),
        Group(
            "回覆",
            "",
            (
                Setting(
                    "copilot", "max_reply_chars", "單句最長字數", "int",
                    "每個候選、每句角色台詞的長度上限。",
                    minimum=20, maximum=200, step=4, unit="字",
                ),
                Setting(
                    "copilot", "auto_popup", "想好就自動跳出視窗", "bool",
                    "有新的候選回覆時自動顯示 SilentTalk（不會搶走遊戲焦點）。",
                ),
                Setting(
                    "app", "max_text_length", "打字輸入上限", "int",
                    "「打字」一次最多能送出的字數。",
                    minimum=20, maximum=2000, step=20, unit="字",
                ),
            ),
        ),
    )),
    Page("discord", "Discord", (
        Group(
            "文字聊天",
            "只讀寫 Bot 所在語音頻道內建的聊天室，不碰其他文字頻道。",
            (
                Setting(
                    "discord", "text_chat", "讀寫文字聊天", "bool",
                    "需要先在 Discord Developer Portal 開啟 Message Content Intent。",
                    restart=True,
                ),
            ),
        ),
        Group(
            "跟隨",
            "",
            (
                Setting(
                    "discord", "disconnect_when_owner_leaves", "你離開就跟著離開", "bool",
                    "你退出語音頻道時，Bot 也跟著離開。",
                ),
                Setting(
                    "discord", "owner_leave_grace_seconds", "離開緩衝", "float",
                    "你離開後先等這麼久，避免切換頻道時來回進出。",
                    minimum=0, maximum=60, step=1, unit="秒",
                ),
                Setting(
                    "discord", "voice_state_settle_seconds", "頻道切換緩衝", "float",
                    "偵測到頻道變化後先等這麼久再動作。",
                    minimum=0.25, maximum=10, step=0.25, unit="秒",
                ),
            ),
        ),
        Group(
            "重連",
            "語音斷線時的重試節奏，網路不穩可以調長。",
            (
                Setting(
                    "discord", "voice_reconnect_interval_seconds", "重連間隔", "float",
                    "", minimum=5, maximum=300, step=5, unit="秒",
                ),
                Setting(
                    "discord", "voice_reconnect_max_interval_seconds", "重連間隔上限", "float",
                    "", minimum=5, maximum=600, step=5, unit="秒",
                ),
                Setting(
                    "discord", "voice_reconnect_cooldown_seconds", "重連冷卻", "float",
                    "", minimum=0.5, maximum=60, step=0.5, unit="秒",
                ),
                Setting(
                    "discord", "voice_ready_timeout_seconds", "連線逾時", "float",
                    "等語音連線就緒的上限。",
                    minimum=1, maximum=120, step=1, unit="秒",
                ),
            ),
            advanced=True,
        ),
    )),
    Page("voice", "聲音", (
        Group(
            "預設聲線",
            "沒有設定自己聲音的人設會用這個。人設的聲音在「人設」頁設定。",
            (
                Setting(
                    "tts", "voice", "聲線", "choice", "",
                    choices=_VOICE_CHOICES,
                ),
                Setting(
                    "tts", "rate", "語速微調", "text",
                    '例："+0%"、"-10%"。上面「聲音風格」的語速會再疊加。',
                ),
                Setting("tts", "pitch", "音高微調", "text", '例："+0Hz"、"-15Hz"。'),
                Setting("tts", "volume", "音量微調", "text", '例："+0%"、"+10%"。'),
            ),
        ),
        Group(
            "播放",
            "",
            (
                Setting(
                    "tts", "timeout_seconds", "合成逾時", "float",
                    "EdgeTTS 這麼久沒回應就算失敗。",
                    minimum=5, maximum=120, step=1, unit="秒",
                ),
                Setting(
                    "audio", "playback_timeout_seconds", "播放逾時", "float",
                    "一段語音最長播這麼久。",
                    minimum=10, maximum=600, step=10, unit="秒",
                ),
                Setting(
                    "audio", "ffmpeg_path", "FFmpeg 路徑", "text",
                    "留 ffmpeg 代表用系統 PATH 裡的那一個。",
                    restart=True,
                ),
            ),
            advanced=True,
        ),
    )),
    Page("listen", "聽力", (
        Group(
            "語音辨識",
            "把頻道裡的語音轉成文字，全部在本機執行。",
            (
                Setting(
                    "copilot", "stt_engine", "辨識引擎", "choice",
                    "paraformer：串流、反應快。sensevoice：有標點、CPU 較省，"
                    "第一次要多下載約 240 MB。",
                    choices=tuple((engine, engine) for engine in STT_ENGINES),
                    restart=True,
                ),
                Setting(
                    "copilot", "endpoint_silence_ms", "斷句靜音", "int",
                    "講完話後靜音這麼久就算一句結束。太短會切斷句子，太長會變慢。",
                    minimum=200, maximum=2000, step=50, unit="毫秒",
                    restart=True,
                ),
            ),
        ),
    )),
    Page("model", "本機模型", (
        Group(
            "回覆模型",
            "改完要重新啟動 SilentTalk。換模型時啟動會自動下載。",
            (
                Setting(
                    "copilot", "enabled", "啟用 Copilot", "bool",
                    "關掉就只剩打字和快捷句的 TTS。",
                    restart=True,
                ),
                Setting(
                    "copilot", "llm_model", "模型檔", "choice", "",
                    choices=(
                        ("gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf", "Gemma 4 E4B（預設，約 2.7 GB VRAM）"),
                        ("gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf", "Gemma 4 E2B（較輕較快，約 1.4 GB VRAM）"),
                        ("Qwen3.5-4B-Q4_K_M.gguf", "Qwen3.5 4B（品質相近，約 2.9 GB VRAM）"),
                        ("Qwen3-1.7B-Q4_K_M.gguf", "Qwen3 1.7B（較弱，CPU 稍快）"),
                    ),
                    restart=True,
                ),
                Setting(
                    "copilot", "llm_gpu_layers", "GPU 層數", "int",
                    "0＝完全用 CPU（慢約 3 倍但仍可用）；99＝盡量放進 GPU。",
                    minimum=0, maximum=99, step=1, restart=True,
                ),
                Setting(
                    "copilot", "llm_low_vram", "省 VRAM 模式", "bool",
                    "8-bit KV cache＋flash attention，省約 0.2–0.3 GB。驅動不支援時自動退回。",
                    restart=True,
                ),
                Setting(
                    "copilot", "llm_context_size", "模型 context", "int",
                    "模型一次能看的 token 數；調大更耗 VRAM。",
                    minimum=1024, maximum=16384, step=512, restart=True,
                ),
                Setting(
                    "copilot", "llama_port", "llama-server 連接埠", "int",
                    "和其他程式衝突時才需要改。",
                    minimum=1024, maximum=65535, step=1, restart=True,
                ),
            ),
        ),
    )),
    Page("keys", "快捷鍵", (
        Group(
            "全域快捷鍵",
            "在遊戲裡也有效。只支援 F1–F12，改完要重新啟動。",
            (
                Setting(
                    "app", "speak_hotkey", "叫出視窗", "choice", "",
                    choices=tuple((f"F{n}", f"F{n}") for n in range(1, 13)),
                    restart=True,
                ),
                Setting(
                    "app", "stop_hotkey", "停止播放", "choice", "",
                    choices=tuple((f"F{n}", f"F{n}") for n in range(1, 13)),
                    restart=True,
                ),
            ),
        ),
    )),
)


def all_settings() -> tuple[Setting, ...]:
    return tuple(
        setting for page in PAGES for group in page.groups for setting in group.settings
    )
