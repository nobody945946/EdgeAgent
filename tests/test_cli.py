from __future__ import annotations

import io
import json

from e4b_core import cli


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
    assert "E4B> 可以。" in capsys.readouterr().out
