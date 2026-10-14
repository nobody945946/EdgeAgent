"""The core service process (`python -m edge_agent.service`): the control
server bound exclusively to its port, with the core it serves."""

from __future__ import annotations

import argparse
import os
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from edge_agent.control import Handler
from edge_agent.core import EdgeAgent
from edge_agent.runtime import CONTROL_PORT, CORE_ROOT, SERVICE_LISTENING


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def announce_listening(port: int, pipe: int | None) -> None:
    """Tell the launcher (api.ensure_service) the listener exists: one line on
    the pipe it handed over for this (an fd on Linux, a handle on Windows),
    then the pipe closes. Started some other way (no pipe): nothing to say."""
    if pipe is None:
        return
    if os.name == "nt":
        import msvcrt

        pipe = msvcrt.open_osfhandle(pipe, os.O_WRONLY)
    try:
        with open(pipe, "wb") as launcher:
            launcher.write(f"{SERVICE_LISTENING} http://127.0.0.1:{port}\n".encode())
    except OSError:
        pass  # the launcher has gone: nobody to tell


def main() -> int:
    from edge_agent.runtime import activate_deployment_tools

    activate_deployment_tools()
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=CORE_ROOT)
    # From api.ensure_service: where to say the port listens.
    parser.add_argument("--ready-pipe", type=int)
    args = parser.parse_args()
    core = EdgeAgent(args.core_dir.resolve())
    Handler.core = core
    server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
    announce_listening(CONTROL_PORT, args.ready_pipe)
    threading.Thread(
        target=core.monitor, name="edge-agent-monitor", daemon=True
    ).start()
    threading.Thread(
        target=lambda: core.switch("base"), name="edge-agent-bootstrap", daemon=True
    ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        core._stop_child()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
