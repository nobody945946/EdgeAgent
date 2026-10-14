"""Byte relay between a TCP port and a Unix socket.

The agent sandbox has no network. Its only way out is this relay to the host
model server, so tools inside the sandbox cannot reach anything else.
`--record DIR` keeps each chat-completion request body exactly as the Agent
sent it (system prompt, tools, messages): training data must be rendered
from what the model really saw. Bench tasks only; never user conversations.
`--replay FILE` (a JSON list of assistant messages, OpenAI wire form) answers
the first chat completions itself with those messages, word for word, and
forwards the rest: a teacher takes over a student's conversation at that
point (bench/run.py --takeover), the sandbox having really run every tool
call the student made before it.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
from pathlib import Path


def endpoint(spec: str) -> tuple[str, str, int]:
    kind, _, rest = spec.partition(":")
    if kind == "tcp":
        host, _, port = rest.rpartition(":")
        return kind, host, int(port)
    if kind == "unix":
        return kind, rest, 0
    raise SystemExit(f"unknown endpoint {spec!r}")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


def replayed(request: dict, message: dict) -> bytes:
    """An HTTP response that gives `message` as the model's answer to
    `request`, streamed the way llama-server streams one (its reasoning,
    text and tool calls as deltas, then the finish and the usage)."""
    def chunk(delta: dict, finish: str | None = None) -> dict:
        return {"id": "replay", "object": "chat.completion.chunk", "model": request.get("model", ""),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    calls = message.get("tool_calls") or []
    finish = "tool_calls" if calls else "stop"
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if request.get("stream"):
        chunks = [chunk({"role": "assistant"})]
        if message.get("reasoning_content"):
            chunks.append(chunk({"reasoning_content": message["reasoning_content"]}))
        if message.get("content"):
            chunks.append(chunk({"content": message["content"]}))
        for index, call in enumerate(calls):
            chunks.append(chunk({"tool_calls": [{"index": index, **call}]}))
        chunks.append(chunk({}, finish))
        chunks.append({"id": "replay", "object": "chat.completion.chunk", "choices": [], "usage": usage})
        body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        kind = "text/event-stream"
    else:
        body = json.dumps({"id": "replay", "object": "chat.completion", "model": request.get("model", ""),
                           "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                           "usage": usage}, ensure_ascii=False)
        kind = "application/json"
    data = body.encode("utf-8")
    return (f"HTTP/1.1 200 OK\r\nContent-Type: {kind}\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n").encode() + data


async def recorded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                   upstream: asyncio.StreamWriter, record: Path | None, counter: itertools.count,
                   replay: list[dict]) -> None:
    """Forward one HTTP/1.1 request at a time, saving chat-completion bodies;
    while `replay` holds answers, a chat completion gets the next one here
    instead (and the connection closes, as the response says)."""
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value.strip())
            body = await reader.readexactly(length) if length else b""
            completion = b"/chat/completions" in head.split(b"\r\n", 1)[0]
            if completion and record is not None:
                (record / f"{next(counter):04d}.json").write_bytes(body)
            if completion and replay:
                writer.write(replayed(json.loads(body), replay.pop(0)))
                await writer.drain()
                break
            upstream.write(head + body)
            await upstream.drain()
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        upstream.close()
        writer.close()


async def serve(listen: str, connect: str, record: Path | None = None,
                replay: list[dict] | None = None) -> None:
    source, target = endpoint(listen), endpoint(connect)
    counter = itertools.count()
    replay = list(replay or [])

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if target[0] == "tcp":
                up_reader, up_writer = await asyncio.open_connection(target[1], target[2])
            else:
                up_reader, up_writer = await asyncio.open_unix_connection(target[1])
        except OSError:
            writer.close()
            return
        upstream = (recorded(reader, writer, up_writer, record, counter, replay) if record or replay
                    else pipe(reader, up_writer))
        await asyncio.gather(upstream, pipe(up_reader, writer))

    if source[0] == "tcp":
        server = await asyncio.start_server(handle, source[1], source[2])
    else:
        if os.path.exists(source[1]):
            os.unlink(source[1])
        server = await asyncio.start_unix_server(handle, source[1])
        os.chmod(source[1], 0o666)
    print("ready", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", required=True, help="tcp:HOST:PORT or unix:PATH")
    parser.add_argument("--connect", required=True, help="tcp:HOST:PORT or unix:PATH")
    parser.add_argument("--record", type=Path, help="save chat-completion request bodies here")
    parser.add_argument("--replay", type=Path, help="a JSON list of assistant messages to answer first")
    args = parser.parse_args()
    if args.record:
        args.record.mkdir(parents=True, exist_ok=True)
    replay = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else None
    asyncio.run(serve(args.listen, args.connect, args.record, replay))


if __name__ == "__main__":
    main()
