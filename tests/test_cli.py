from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from edge_agent import cli


@pytest.mark.skipif(os.name != "nt", reason="Windows global command")
def test_global_command_handles_unicode_paths_without_a_console(tmp_path, monkeypatch):
    root = tmp_path / "原生 EdgeAgent 空白路徑"
    root.mkdir()
    subprocess.run(
        [sys.executable, "-m", "venv", str(root / ".venv")], check=True, timeout=60
    )
    (root / "edge_agent").mkdir()
    (root / "edge_agent/cli.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli, "CORE_ROOT", root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "使用者 資料"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(cli, "_register_command_path", lambda links: None)
    command = cli.install_command()
    result = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            "call",
            str(command),
            "status",
            "空 白參數",
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == ["status", "空 白參數"]


def test_terminal_chat_streams_local_response_without_agent(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}
    responses = iter(("請用中文回答", "/exit"))

    class FakeResponse(io.BytesIO):
        pass

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            (
                'event: delta\ndata: {"text":"可以。"}\n\n'
                'event: done\ndata: {"thinking_effort":"low"}\n\n'
            ).encode()
        )

    monkeypatch.setattr(cli, "request_base", lambda: {"mode": "base"})
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    assert cli.main(["chat", "--thinking-effort", "low"]) == 0

    payload = captured["payload"]
    assert captured["url"] == "http://127.0.0.1:18087/chat"
    assert captured["timeout"] == 1800
    assert payload["text"] == "請用中文回答"
    assert payload["thinking_effort"] == "low"
    assert payload["agent"] is False
    assert len(payload["session_id"]) == 32
    assert "EdgeAgent> 可以。" in capsys.readouterr().out
