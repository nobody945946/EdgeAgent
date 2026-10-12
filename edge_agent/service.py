"""The core service process (`python -m edge_agent.service`): the control
server bound exclusively to its port, with the core it serves."""

from __future__ import annotations

import argparse
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from edge_agent.control import Handler
from edge_agent.core import EdgeAgent
from edge_agent.runtime import CONTROL_PORT, CORE_ROOT


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> int:
    from edge_agent.runtime import activate_deployment_tools

    activate_deployment_tools()
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=CORE_ROOT)
    args = parser.parse_args()
    core = EdgeAgent(args.core_dir.resolve())
    Handler.core = core
    server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
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
