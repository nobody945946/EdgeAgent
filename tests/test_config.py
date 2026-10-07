"""config.json (config.py): what is saved, how it is validated and migrated."""

from __future__ import annotations

from pathlib import Path

import pytest

from edge_agent.config import DEFAULT_CONFIG, validated_config
from edge_agent.core import EdgeAgent


def test_config_never_persists_workspace_or_chat(tmp_path: Path) -> None:
    manager = EdgeAgent(tmp_path)

    config = manager.update_config({"thinking_effort": "high", "agent": False})

    assert config == {
        **DEFAULT_CONFIG,
        "thinking_effort": "high",
        "agent": False,
    }
    saved = manager.config_path.read_text(encoding="utf-8")
    assert "working_dir" not in saved
    assert "messages" not in saved


def test_output_budget_is_validated_and_leased_client_is_separate(tmp_path):
    manager = EdgeAgent(tmp_path)
    config = validated_config(
        {"context_size": 32768, "max_output_tokens": 16384}
    )
    assert config["max_output_tokens"] == 16384
    for value in (True, 0, -1, 131073, "8192"):
        with pytest.raises((ValueError, TypeError)):
            validated_config({"max_output_tokens": value})
    manager.chat.configure_context(32768, max_output_tokens=16384)
    manager.mode = "base"
    assert manager.models_status()["max_output_tokens"] == 16384
    manager.mode = "character"
    assert manager.models_status()["max_output_tokens"] is None
    with pytest.raises(ValueError):
        manager.update_config({"max_output_tokens": 8192})


def test_working_target_is_separate_from_physical_capacity_and_output(tmp_path):
    manager = EdgeAgent(tmp_path)
    for value in (True, 0, 4096, 262145, "32768"):
        with pytest.raises((ValueError, TypeError)):
            validated_config({"working_context_size": value})
    with pytest.raises(ValueError):
        manager.update_config({"working_context_size": 32768})
    manager.chat.configure_context(
        131072, max_output_tokens=8192, working_context_size=32768
    )
    assert manager.chat.context_size == 131072
    assert manager.chat.max_output_tokens == 8192
    assert manager.chat.working_context_size == 32768
    manager.chat.configure_context(131072, working_context_size=131072)
    assert manager.chat.working_context_size == 131072
    manager.chat.configure_context(
        16384, max_output_tokens=8192, working_context_size=8192
    )
    assert manager.chat.working_context_size == 16384
    manager.mode = "character"
    assert manager.models_status()["working_context_size"] is None


def test_legacy_low_agent_context_migrates_without_resetting_other_settings(tmp_path):
    import json

    path = tmp_path / "config.json"
    old = {
        "model": "custom.gguf",
        "lora": "personal.gguf",
        "context_size": 4096,
        "gpu_layers": 12,
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    manager = EdgeAgent(tmp_path)
    assert manager.config["context_size"] == 8192
    assert manager.config["model"] == old["model"]
    assert manager.config["lora"] == old["lora"]
    assert manager.config["gpu_layers"] == old["gpu_layers"]
    assert json.loads(path.read_text(encoding="utf-8")) == old
    with pytest.raises(ValueError):
        validated_config({"context_size": 4096})
