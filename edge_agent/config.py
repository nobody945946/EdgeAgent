"""config.json, the owner's standing preferences: its fields, defaults and
validation, and the checks for the values clients send."""

from __future__ import annotations

import json
from pathlib import Path

from edge_agent.mcp import validate_mcp_servers
from edge_agent.models import model_filename
from edge_agent.modes import THINKING_PROFILES
from edge_agent.permissions import DEFAULT_PERMISSIONS, validate_permissions
from edge_agent.runtime import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_WORKING_CONTEXT_SIZE,
    GENERAL_CONTEXT_SIZE,
    MIN_AGENT_CONTEXT_SIZE,
)

DEFAULT_CONFIG: dict[str, object] = {
    "thinking_effort": "off",
    "agent": True,
    "model": DEFAULT_MODEL,
    "lora": "",
    "mmproj": "",
    "context_size": GENERAL_CONTEXT_SIZE,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "working_context_size": DEFAULT_WORKING_CONTEXT_SIZE,
    "gpu_layers": -1,
    "low_vram": True,
    "permissions": DEFAULT_PERMISSIONS,
    # MCP servers the owner added (edge_agent/mcp.py); none by default.
    "mcp_servers": {},
    # Sessions kept in the user's selected records folder (records.py).
    "record_sessions": False,
    "record_folder": "",
}
# The inference settings: applied only by a model switch, never by a plain save.
MODEL_SETTINGS = frozenset(
    {
        "model",
        "lora",
        "mmproj",
        "context_size",
        "max_output_tokens",
        "working_context_size",
        "gpu_layers",
        "low_vram",
    }
)


def load_config(path: Path) -> dict[str, object]:
    if not path.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return dict(DEFAULT_CONFIG)
        # Older releases allowed a window fully consumed by Pi's 4096-token
        # safety reserve. Upgrade that one legacy field without resetting
        # the selected model, adapter or other preferences.
        context = value.get("context_size")
        if type(context) is int and 4096 <= context < MIN_AGENT_CONTEXT_SIZE:
            value["context_size"] = MIN_AGENT_CONTEXT_SIZE
        return validated_config(value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def validated_config(value: dict[str, object]) -> dict[str, object]:
    unknown = set(value) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(sorted(unknown))}")
    merged = {**DEFAULT_CONFIG, **value}
    effort = merged["thinking_effort"]
    if not isinstance(effort, str) or effort not in THINKING_PROFILES:
        raise ValueError("invalid thinking_effort")
    agent = merged["agent"]
    if not isinstance(agent, bool):
        raise TypeError("agent must be a boolean")
    model_filename(merged["model"])
    if not isinstance(merged["lora"], str):
        raise TypeError("lora must be a filename or an empty string")
    if merged["lora"]:
        model_filename(merged["lora"])
    if not isinstance(merged["mmproj"], str):
        raise TypeError("mmproj must be a filename or an empty string")
    if merged["mmproj"]:
        model_filename(merged["mmproj"])
    bounded_int(merged, "context_size", MIN_AGENT_CONTEXT_SIZE, 262144)
    bounded_int(merged, "max_output_tokens", 512, 131072)
    bounded_int(merged, "working_context_size", 8192, 262144)
    bounded_int(merged, "gpu_layers", -1, 999)
    if not isinstance(merged["low_vram"], bool):
        raise TypeError("low_vram must be a boolean")
    return {
        **merged,
        "permissions": validate_permissions(merged["permissions"]),
        "mcp_servers": validate_mcp_servers(merged["mcp_servers"]),
        "record_sessions": _flag(merged, "record_sessions"),
        "record_folder": _folder(merged, "record_folder"),
    }


def save_config(path: Path, value: dict[str, object]) -> dict[str, object]:
    """Validate and replace the file whole; returns what was saved."""
    config = validated_config(value)
    temporary = path.with_suffix(".json.part")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return config


def required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def bounded_int(
    payload: dict[str, object], key: str, minimum: int, maximum: int
) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _flag(payload: dict[str, object], key: str) -> bool:
    if not isinstance(payload.get(key), bool):
        raise TypeError(f"{key} must be a boolean")
    return bool(payload[key])


def _folder(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or len(value) > 260:
        raise TypeError(f"{key} must be a folder path")
    return value.strip()
