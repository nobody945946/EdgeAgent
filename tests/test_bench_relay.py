"""bench/relay.py: the sandbox's only way to the model; it records what the
Agent sent and, for a takeover, answers the first completions itself."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import relay

# The relay listens on a Unix socket for the sandbox; the bench runs on the Linux dev box.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets: the bench is Linux-only")

STUDENT = {"role": "assistant", "content": None, "reasoning_content": "先看檔案",
           "tool_calls": [{"id": "c1", "type": "function",
                           "function": {"name": "read", "arguments": "{\"path\":\"/work/a.txt\"}"}}]}


async def _post(path: str, body: dict) -> bytes:
    reader, writer = await asyncio.open_unix_connection(path)
    data = json.dumps(body).encode()
    writer.write(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                 + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
    await writer.drain()
    out = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return out


def test_replayed_answers_come_first_then_the_model_and_every_request_is_recorded(tmp_path):
    asyncio.run(_replay(tmp_path))


async def _replay(tmp_path):
    seen = []

    async def model(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        length = int(next(line for line in head.split(b"\r\n")
                          if line.lower().startswith(b"content-length")).split(b":")[1])
        seen.append(json.loads(await reader.readexactly(length)))
        body = b'data: {"choices":[{"delta":{"content":"teacher"}}]}\n\ndata: [DONE]\n\n'
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        await writer.drain()
        writer.close()

    upstream = await asyncio.start_server(model, "127.0.0.1", 0)
    port = upstream.sockets[0].getsockname()[1]
    sock, record = tmp_path / "m.sock", tmp_path / "records"
    record.mkdir()
    task = asyncio.create_task(relay.serve(f"unix:{sock}", f"tcp:127.0.0.1:{port}", record, [STUDENT]))
    for _ in range(100):
        if sock.exists():
            break
        await asyncio.sleep(0.02)
    try:
        first = await _post(str(sock), {"model": "m", "stream": True, "messages": [{"role": "user", "content": "1"}]})
        head, _, body = first.partition(b"\r\n\r\n")
        assert b"200 OK" in head and b"text/event-stream" in head
        events = [json.loads(line[6:]) for line in body.decode().split("\n\n") if line.startswith("data: {")]
        deltas = [e["choices"][0]["delta"] for e in events if e.get("choices")]
        assert {"reasoning_content": "先看檔案"} in deltas
        assert deltas[-2]["tool_calls"][0]["function"]["name"] == "read"
        assert events[-2]["choices"][0]["finish_reason"] == "tool_calls" and "usage" in events[-1]
        assert seen == []  # the model never saw the replayed request
        second = await _post(str(sock), {"model": "m", "stream": True, "messages": [{"role": "user", "content": "2"}]})
        assert b"teacher" in second and seen[0]["messages"][0]["content"] == "2"
        assert sorted(p.name for p in record.iterdir()) == ["0000.json", "0001.json"]
    finally:
        task.cancel()
        upstream.close()
