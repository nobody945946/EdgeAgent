"""Byte relay between a TCP port and a Unix socket.

The agent sandbox has no network. Its only way out is this relay to the host
model server, so tools inside the sandbox cannot reach anything else.
`--record DIR` keeps each chat-completion request body exactly as the Agent
sent it (system prompt, tools, messages): training data must be rendered
from what the model really saw. Bench tasks only; never user conversations.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
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


async def recorded(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                   record: Path, counter: itertools.count) -> None:
    """Forward one HTTP/1.1 request at a time, saving chat-completion bodies."""
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.split(b"\r\n"):
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value.strip())
            body = await reader.readexactly(length) if length else b""
            if b"/chat/completions" in head.split(b"\r\n", 1)[0]:
                (record / f"{next(counter):04d}.json").write_bytes(body)
            writer.write(head + body)
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def serve(listen: str, connect: str, record: Path | None = None) -> None:
    source, target = endpoint(listen), endpoint(connect)
    counter = itertools.count()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if target[0] == "tcp":
                up_reader, up_writer = await asyncio.open_connection(target[1], target[2])
            else:
                up_reader, up_writer = await asyncio.open_unix_connection(target[1])
        except OSError:
            writer.close()
            return
        upstream = (recorded(reader, up_writer, record, counter) if record
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
    args = parser.parse_args()
    if args.record:
        args.record.mkdir(parents=True, exist_ok=True)
    asyncio.run(serve(args.listen, args.connect, args.record))


if __name__ == "__main__":
    main()
