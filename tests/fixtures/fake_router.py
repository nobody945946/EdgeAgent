"""A stand-in for llama-server in router mode (llama.cpp b11193), for the
router and monitor tests: no model, no GPU. It says what the real one says on
its output, answers /models, /models/load, /models/unload, /props, and pushes
model events on GET /models/sse (chunked, as cpp-httplib sends them).

Run as: fake_router.py --port N --models-preset PATH [...]. FAKE_ROUTER sets
what it does: "listens" (default), "dies" (exits 3 before listening), "hangs"
(never listens), "fails" (a load ends with the worker exiting 1).
FAKE_LOAD_SECONDS: how long a load takes (default 0.3).
Test-only routes: POST /test/crash (the worker dies: unloaded, exit 139),
POST /test/drop (ends the event streams, the router keeps running), GET
/test/counts (requests per path).
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def say(line: str) -> None:
    print(f"0.00.000.000 I srv  {line}", file=sys.stderr, flush=True)


arguments = sys.argv[1:]
port = int(arguments[arguments.index("--port") + 1])
preset = Path(arguments[arguments.index("--models-preset") + 1])
mode = os.environ.get("FAKE_ROUTER", "listens")
load_seconds = float(os.environ.get("FAKE_LOAD_SECONDS", "0.3"))

say("llama_server: starting server in router mode. models will be automatically loaded on-demand")
if mode == "dies":
    say("llama_server: couldn't bind HTTP server socket, hostname: 127.0.0.1")
    sys.exit(3)
if mode == "hangs":
    time.sleep(600)
    sys.exit(0)

lock = threading.Lock()
state: dict[str, object] = {"value": "unloaded"}
streams: list[queue.Queue] = []
counts: Counter[str] = Counter()


def broadcast(event: str, model: str, data: dict | None = None) -> None:
    payload = {"model": model, "event": event}
    if data is not None:
        payload["data"] = data
    with lock:
        for stream in streams:
            stream.put(payload)


def set_state(value: dict[str, object], event: str = "status_change") -> None:
    with lock:
        state.clear()
        state.update(value)
    data = {"status": value["value"]}
    if value["value"] == "unloaded":
        data["exit_code"] = value.get("exit_code", 0)
    broadcast(event, "local", data)


def finish_load() -> None:
    if mode == "fails":
        say("operator(): instance name=local exited with status 1")
        set_state({"value": "unloaded", "failed": True, "exit_code": 1})
    else:
        set_state({"value": "loaded"})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        return

    def _json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        with lock:
            counts[path] += 1
        if path == "/models/sse":
            self._sse()
        elif path == "/models":
            if "reload=1" in self.path:
                broadcast("models_reload", "*")
            with lock:
                status = dict(state)
            self._json({"data": [{"id": "local", "status": status}], "object": "list"})
        elif path == "/props":
            context = int(os.environ.get("FAKE_CONTEXT", "0")) or next(
                (int(line.split("=")[1]) for line in preset.read_text().splitlines()
                 if line.startswith("ctx-size")), 4096)
            self._json({"default_generation_settings": {"n_ctx": context}, "modalities": {}})
        elif path == "/health":
            self._json({"status": "ok"})
        elif path == "/test/counts":
            with lock:
                self._json(dict(counts))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        with lock:
            counts[path] += 1
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path == "/models/load":
            say("load(): spawning server instance with name=local on port 40000")
            set_state({"value": "loading"}, event="model_status")
            threading.Timer(load_seconds, finish_load).start()
            self._json({"success": True})
        elif path == "/models/unload":
            set_state({"value": "unloaded", "exit_code": 0})
            self._json({"success": True})
        elif path == "/test/crash":
            say("operator(): instance name=local exited with status 139")
            set_state({"value": "unloaded", "failed": True, "exit_code": 139})
            self._json({"success": True})
        elif path == "/test/drop":
            with lock:
                for stream in streams:
                    stream.put(None)
            self._json({"success": True})
        else:
            self._json({"error": "not found"}, 404)

    def _sse(self) -> None:
        events: queue.Queue = queue.Queue()
        with lock:
            streams.append(events)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.flush()
        try:
            while True:
                event = events.get()
                if event is None:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    self.close_connection = True
                    return
                data = ("data: " + json.dumps(event) + "\n\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with lock:
                streams.remove(events)


server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
server.daemon_threads = True
time.sleep(0.2)  # a router takes a moment: nothing may ask it before it says so
say(f"llama_server: listening on http://127.0.0.1:{port}")
server.serve_forever()
