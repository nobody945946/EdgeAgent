"""Byte relay between a TCP port and a Unix socket.

The agent sandbox has no network. Its only way out is this relay to the host
model server, so tools inside the sandbox cannot reach anything else.
"""

from __future__ import annotations

import argparse
import asyncio
import os


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


async def serve(listen: str, connect: str) -> None:
    source, target = endpoint(listen), endpoint(connect)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if target[0] == "tcp":
                up_reader, up_writer = await asyncio.open_connection(target[1], target[2])
            else:
                up_reader, up_writer = await asyncio.open_unix_connection(target[1])
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

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
    args = parser.parse_args()
    asyncio.run(serve(args.listen, args.connect))


if __name__ == "__main__":
    main()
