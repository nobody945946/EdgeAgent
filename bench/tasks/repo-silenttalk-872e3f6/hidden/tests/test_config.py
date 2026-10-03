from pathlib import Path

import pytest

from silenttalk.config import ConfigError, load_settings


def _write_config(
    directory: Path,
    engine: str = "edge",
) -> None:
    (directory / "config.toml").write_text(
        f"""
[app]
max_text_length = 50
[tts]
engine = "{engine}"
[discord]
[audio]
ffmpeg_path = "missing-ffmpeg-for-test"
""",
        encoding="utf-8",
    )


def test_loads_env_and_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    settings = load_settings(tmp_path)
    assert settings.discord.owner_user_id == 123456789
    assert settings.app.max_text_length == 50
    assert settings.tts.engine == "edge"
    assert settings.copilot.enabled is True
    assert settings.copilot.endpoint_silence_ms == 650
    assert settings.copilot.llm_context_size == 4096
    assert settings.copilot.llm_model == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert settings.copilot.llm_gpu_layers == 99
    assert settings.copilot.stt_engine == "paraformer"
    assert settings.validate_runtime() == ["找不到 FFmpeg：missing-ffmpeg-for-test"]


def test_loads_credentials_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    (tmp_path / ".env").write_text(
        "DISCORD_BOT_TOKEN=file-token\nOWNER_USER_ID=987654321\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)

    settings = load_settings(tmp_path)

    assert settings.discord.token == "file-token"
    assert settings.discord.owner_user_id == 987654321


def test_rejects_missing_owner_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    with pytest.raises(ConfigError, match="OWNER_USER_ID"):
        load_settings(tmp_path)


def test_rejects_missing_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("OWNER_USER_ID", "123")
    with pytest.raises(ConfigError, match="DISCORD_BOT_TOKEN"):
        load_settings(tmp_path)


def test_rejects_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="唯一設定檔"):
        load_settings(tmp_path)


def test_rejects_unimplemented_tts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, "cosyvoice")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123")
    with pytest.raises(ConfigError, match="只支援"):
        load_settings(tmp_path)


def test_rejects_duplicate_hotkeys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.toml").write_text(
        '[app]\nspeak_hotkey = "F8"\nstop_hotkey = "F8"\n'
        '[tts]\nengine = "edge"\n'
        "[discord]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123")
    with pytest.raises(ConfigError, match="不可相同"):
        load_settings(tmp_path)


def test_rejects_llm_model_path_outside_model_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('[copilot]\nllm_model = "../evil.gguf"\n')
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    with pytest.raises(ConfigError, match="llm_model"):
        load_settings(tmp_path)


def test_accepts_sensevoice_stt_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('[copilot]\nstt_engine = "sensevoice"\n')
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    assert load_settings(tmp_path).copilot.stt_engine == "sensevoice"


def test_rejects_unknown_stt_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('[copilot]\nstt_engine = "whisper"\n')
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    with pytest.raises(ConfigError, match="stt_engine"):
        load_settings(tmp_path)


@pytest.mark.parametrize(
    ("model", "lora", "uses"),
    [
        # The shipped default: E4B with its own adapter on every decision and line.
        (
            "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
            "character-e4b-lora-v3.q8_0.gguf",
            ("called", "active", "line"),
        ),
        # Switched to E2B on the settings page: the E2B adapter, on "called" only.
        (
            "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
            "character-e2b-lora-v1.q8_0.gguf",
            ("called",),
        ),
        # A model no bundled adapter fits: none.
        ("Qwen3.5-4B-Q4_K_M.gguf", "", ("called", "active", "line")),
    ],
)
def test_the_character_adapter_follows_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, lora: str, uses: tuple
) -> None:
    _write_config(tmp_path)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            f'[copilot]\nllm_model = "{model}"\n'
            'character_lora = "character-e4b-lora-v3.q8_0.gguf"\n'
            'character_lora_uses = "called,active,line"\n'
        )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    copilot = load_settings(tmp_path).copilot
    assert (copilot.character_lora, copilot.character_lora_uses) == (lora, uses)


def test_an_adapter_of_ones_own_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path)
    with (tmp_path / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[copilot]\nllm_model = "Qwen3.5-4B-Q4_K_M.gguf"\ncharacter_lora = "mine.gguf"\n'
        )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("OWNER_USER_ID", "123456789")
    assert load_settings(tmp_path).copilot.character_lora == "mine.gguf"
