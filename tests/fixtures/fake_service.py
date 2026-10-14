"""A stand-in for the EdgeAgent service (python -m edge_agent.service) for the
launcher's tests (api.py): it says it is listening with the service's own
announce_listening, answers /status and pushes /status/events like
control.py, becomes ready FAKE_READY_AFTER seconds after it starts, and
exits shortly after POST /shutdown.

Run as: fake_service.py PORT REPO_ROOT --ready-pipe N (as the launcher
adds it). FAKE_SERVICE: "serves" (default),
"dies" (exits 2 before listening), "errors" (its status carries an error).
GET /test/counts: requests per path.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
sys.path.insert(0, sys.argv[2])
ready_pipe = int(sys.argv[sys.argv.index("--ready-pipe") + 1])
mode = os.environ.get("FAKE_SERVICE", "serves")
ready_after = float(os.environ.get("FAKE_READY_AFTER", "1.0"))

print("starting the fake service", flush=True)
if mode == "dies":
    sys.exit(2)

from edge_agent.service import announce_listening

lock = threading.Condition()
counts: Counter[str] = Counter()
ready = False


def status() -> dict[str, object]:
    return {"service": "edge-agent", "pid": os.getpid(), "ready": ready,
            "mode": "base" if ready else "starting",
            "error": "missing model" if mode == "errors" else ""}


def become_ready() -> None:
    global ready
    with lock:
        ready = True
        lock.notify_all()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def _json(self, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        with lock:
            counts[self.path] += 1
        if self.path == "/status":
            with lock:
                self._json(status())
        elif self.path == "/test/counts":
            with lock:
                self._json(dict(counts))
        elif self.path == "/status/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            self.close_connection = True
            with lock:
                last = status()
            self._event(last)
            with lock:
                lock.wait_for(lambda: ready)
                last = status()
            self._event(last)
            with lock:
                lock.wait_for(lambda: False)  # held open until the process ends
        else:
            self.send_error(404)

    def _event(self, value: dict[str, object]) -> None:
        self.wfile.write(f"event: status\ndata: {json.dumps(value)}\n\n".encode())
        self.wfile.flush()

    def do_POST(self) -> None:
        with lock:
            counts[self.path] += 1
        if self.path == "/shutdown":
            self._json({"mode": "stopped", "ready": False})
            threading.Timer(0.3, lambda: os._exit(0)).start()
        else:
            self.send_error(404)


server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
server.daemon_threads = True
announce_listening(port, ready_pipe)
threading.Timer(ready_after, become_ready).start()
server.serve_forever()
