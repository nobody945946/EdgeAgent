"""The browser UI page (web.py) as the control server serves it."""

from __future__ import annotations

from edge_agent import runtime, web
from edge_agent.web import NATIVE_CHAT_HTML


def test_model_help_uses_both_runtime_adapter_names():
    assert f"<code>{runtime.AGENT_ADAPTER}</code>" in web.NATIVE_CHAT_HTML
    assert f"<code>{runtime.CHARACTER_ADAPTER}</code>" in web.NATIVE_CHAT_HTML
    assert "@@AGENT_ADAPTER@@" not in web.NATIVE_CHAT_HTML
    assert "@@CHARACTER_ADAPTER@@" not in web.NATIVE_CHAT_HTML


def test_native_web_ui_handles_compaction_notice() -> None:
    assert "event: 'compacted'" not in NATIVE_CHAT_HTML
    assert "type==='compacted'" in NATIVE_CHAT_HTML
    assert 'id="active-model-label"' in NATIVE_CHAT_HTML
    assert 'id="effort"' in NATIVE_CHAT_HTML
    assert 'id="agent"' in NATIVE_CHAT_HTML
    assert "/chat/approve" in NATIVE_CHAT_HTML
    assert 'id="save"' in NATIVE_CHAT_HTML
    assert 'id="shutdown"' in NATIVE_CHAT_HTML
    assert 'id="browse"' in NATIVE_CHAT_HTML
    assert "fetch('/config')" in NATIVE_CHAT_HTML
    assert "fetch('/directories'" in NATIVE_CHAT_HTML
    assert "目前 Workspace" in NATIVE_CHAT_HTML
    assert "新的對話 context" in NATIVE_CHAT_HTML
    assert 'id="cwd" type="text" readonly' in NATIVE_CHAT_HTML
