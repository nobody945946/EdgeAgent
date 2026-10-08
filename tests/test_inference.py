from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from edge_agent.inference import InferenceAccess, InferenceBusy, InferenceGateway


@contextmanager
def routed_model(port):
    gateway = InferenceGateway(port)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            gateway.forward(self, payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, gateway
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_the_character_speaks_while_the_agent_works():
    """Two slots in one pool: a task (tool runs, approvals, generating) no
    longer silences the character (owner, 2026-10-08: voice cannot wait)."""
    access = InferenceAccess()
    access.reset(65536)
    with access.agent_turn():
        with access.generation(agent=True, need=20000) as evict:
            assert evict is None
            with access.generation(agent=False, need=4000) as evict:
                assert evict is None
        # Between the Agent's requests too.
        with access.generation(agent=False, need=4000):
            pass
    # One character request at a time.
    with access.generation(agent=False, need=4000):
        with pytest.raises(InferenceBusy), access.generation(agent=False, need=10):
            pass


def test_a_new_agent_request_lets_the_character_finish_speaking(monkeypatch):
    access = InferenceAccess()
    access.reset(65536)
    speaking, done, entered = threading.Event(), threading.Event(), threading.Event()

    def character():
        with access.generation(agent=False, need=4000):
            speaking.set()
            assert done.wait(3)

    def agent():
        with access.agent_turn(), access.generation(agent=True, need=20000):
            entered.set()

    c = threading.Thread(target=character)
    c.start()
    assert speaking.wait(1)
    a = threading.Thread(target=agent)
    a.start()
    assert not entered.wait(0.3)
    done.set()
    assert entered.wait(2)
    a.join(2)
    c.join(2)
    # A character that keeps speaking past the deadline: the Agent runs beside it.
    import edge_agent.inference as inference
    monkeypatch.setattr(inference, "VOICE_FIRST_SECONDS", 0.2)
    with access.generation(agent=False, need=4000):
        with access.agent_turn(), access.generation(agent=True, need=20000) as evict:
            assert evict is None


def test_the_agent_always_gets_the_whole_pool():
    """64K is the Agent's floor: cells the character's cache holds are erased
    when the Agent needs them; the character waits, never the Agent."""
    access = InferenceAccess()
    access.reset(65536)
    with access.generation(agent=False, need=8000):
        pass
    access.settle("character", 6000)
    with access.agent_turn():
        with access.generation(agent=True, need=60000) as evict:
            assert evict == "character"
            access.evicted("character")
            assert access.cells == {"agent": 60000, "character": 0}
            # The pool is the Agent's now: the character waits for the task.
            with pytest.raises(InferenceBusy), access.generation(agent=False, need=8000):
                pass
        access.settle("agent", 59000)
        with pytest.raises(InferenceBusy), access.generation(agent=False, need=8000):
            pass
    # No task: the character may erase the Agent's idle cache.
    with access.generation(agent=False, need=8000) as evict:
        assert evict == "agent"
        access.evicted("agent")
    assert access.cells == {"agent": 0, "character": 8000}


def test_exception_and_reload_release_access_without_restarting_clients():
    access = InferenceAccess()
    with pytest.raises(RuntimeError), access.agent_turn():
        raise RuntimeError("fixture task failed")
    with pytest.raises(RuntimeError), access.generation(agent=False):
        raise RuntimeError("fixture encoder unrelated to this module")
    with pytest.raises(RuntimeError), access.maintenance():
        with pytest.raises(InferenceBusy), access.generation(agent=False):
            pass
        raise RuntimeError("fixture model load failed")
    with access.generation(agent=False):
        assert not access.agent_reserved
    with pytest.raises(PermissionError), access.generation(agent=True):
        pass


@contextmanager
def gateway_servers():
    requests = []
    access = InferenceGateway(1)
    access.configure(os.getpid(), 2, 1, pool=65536)

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            assert payload.get("model") == "local", "router POST requires the model in its JSON body"
            if self.path == "/apply-template":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"prompt": "x" * payload.get("fixture_tokens", 100)}).encode())
            elif self.path == "/tokenize":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"tokens": [1] * len(payload["content"])}).encode())
            elif payload.get("fixture_overflow"):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":{"code":"context_length_exceeded"}}')
            elif self.path.startswith("/slots/"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"n_erased":100}')
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
                self.end_headers()
                timings = {"cache_n": 0, "prompt_n": payload.get("fixture_tokens", 100), "predicted_n": 7}
                self.wfile.write(b'data: {"fixture":true}\n\ndata: [DONE]\n\n' if payload.get("stream")
                                 else json.dumps({"fixture": True, "timings": timings}).encode())

        def log_message(self, *args):
            pass

    class Client(BaseHTTPRequestHandler):
        def do_POST(self):
            access.forward(self, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    client = ThreadingHTTPServer(("127.0.0.1", 0), Client)
    access.upstream_url = f"http://127.0.0.1:{upstream.server_port}"
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, client)]
    for thread in threads:
        thread.start()
    try:
        yield access, f"http://127.0.0.1:{client.server_port}", requests
    finally:
        for server in (client, upstream):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(2)


def post(url, payload, headers):
    request = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", **headers})
    return urllib.request.urlopen(request, timeout=3)


def test_http_lanes_keep_their_slots_adapters_and_counted_cells():
    with gateway_servers() as (gateway, url, requests):
        role = {"X-Character-Owner": str(os.getpid())}
        agent = {"Authorization": f"Bearer {gateway.access.agent_key}"}
        with post(url, {"id_slot": 1, "lora": [{"id": 0, "scale": 1}], "max_tokens": 200}, role) as response:
            assert json.load(response)["fixture"] is True
        # The server's timings settle what the slot holds: 100 prompt + 7 generated.
        assert gateway.access.cells["character"] == 107
        with gateway.access.agent_turn():
            # The character keeps talking during a task.
            with post(url, {"lora": [{"id": 0, "scale": 1}], "max_tokens": 200}, role) as response:
                assert response.status == 200
            with post(url, {"stream": True, "lora": [{"id": 0, "scale": 1}], "max_tokens": 8192}, agent) as response:
                assert list(response) == [b'data: {"fixture":true}\n', b'\n', b'data: [DONE]\n', b'\n']
            # Counted before it runs: prompt 100 + 8192 output + margin.
            assert gateway.access.cells["agent"] == 100 + 8192 + 256
            with pytest.raises(urllib.error.HTTPError) as overflow:
                post(url, {"fixture_overflow": True, "max_tokens": 200}, agent)
            assert overflow.value.code == 400
            assert json.load(overflow.value)["error"]["code"] == "context_length_exceeded"
            # An Agent request that needs the character's cells erases them first.
            erased = sum(path == "/slots/0?action=erase&model=local" for path, _ in requests)
            with post(url, {"fixture_tokens": 61500, "max_tokens": 4000}, agent) as response:
                assert response.status == 200
            assert sum(path == "/slots/0?action=erase&model=local" for path, _ in requests) == erased + 1
            # The server says the Agent's cache holds 61,507: a short line still fits beside it,
            with post(url, {"lora": [{"id": 0, "scale": 1}], "max_tokens": 200}, role) as response:
                assert response.status == 200
            # a longer prompt waits while the task holds the pool.
            with pytest.raises(urllib.error.HTTPError) as busy:
                post(url, {"lora": [{"id": 0, "scale": 1}], "max_tokens": 200, "fixture_tokens": 4000}, role)
            assert busy.value.code == 423
            assert json.load(busy.value)["error"]["code"] == "inference_busy"
        # An Agent request that does not say how much it may generate is counted as the whole pool.
        with gateway.access.agent_turn(), post(url, {}, agent):
            assert gateway.access.cells["agent"] == 65536
        generated = [value for path, value in requests if path == "/v1/chat/completions"]
        # The character's slot is 0 (filled first by the server), the Agent's 1.
        assert [value["id_slot"] for value in generated] == [0, 0, 1, 1, 1, 0, 1]
        assert [value["lora"] for value in generated][:3] == [
            [{"id": 0, "scale": 1.0}, {"id": 1, "scale": 0.0}],
            [{"id": 0, "scale": 1.0}, {"id": 1, "scale": 0.0}],
            [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}],
        ]


def test_http_does_not_allow_a_character_to_take_the_agent_adapter():
    with gateway_servers() as (_, url, requests):
        for headers, payload, code in [
            ({}, {}, 403),
            ({"X-Character-Owner": str(os.getpid())}, {"lora": [{"id": 1, "scale": 1}]}, 400),
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                post(url, payload, headers)
            assert error.value.code == code
        assert requests == []
