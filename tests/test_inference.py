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


def test_agent_reserves_the_whole_task_before_waiting_for_a_borrower():
    access = InferenceAccess()
    started, finish = threading.Event(), threading.Event()
    entered, task_done = threading.Event(), threading.Event()
    failures = []

    def borrower():
        try:
            with access.generation(agent=False):
                started.set()
                assert finish.wait(3)
        except BaseException as exc:
            failures.append(exc)

    def agent():
        try:
            with access.agent_turn():
                with access.generation(agent=True):
                    entered.set()
                # Idle between token generations is still part of this task.
                assert task_done.wait(3)
        except BaseException as exc:
            failures.append(exc)

    b = threading.Thread(target=borrower)
    a = threading.Thread(target=agent)
    b.start()
    assert started.wait(1)
    a.start()
    try:
        with access._condition:
            assert access._condition.wait_for(lambda: access._agent_reserved, timeout=1)
        assert not entered.is_set()
        with pytest.raises(InferenceBusy), access.generation(agent=False):
            pytest.fail("borrower overtook waiting Agent")
        finish.set()
        assert entered.wait(1)
        with pytest.raises(InferenceBusy), access.generation(agent=False):
            pytest.fail("borrower ran between Agent tool calls")
    finally:
        finish.set()
        task_done.set()
        a.join(3)
        b.join(3)
    assert not a.is_alive() and not b.is_alive() and not failures
    with access.generation(agent=False):
        assert not access.agent_reserved


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
    access.configure(os.getpid(), 2, 1)

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, payload))
            if payload.get("fixture_overflow"):
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
                self.wfile.write(b'data: {"fixture":true}\n\ndata: [DONE]\n\n' if payload.get("stream") else b'{"fixture":true}')

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


def test_http_handover_clears_only_kv_and_isolates_adapters_and_streams():
    with gateway_servers() as (gateway, url, requests):
        role = {"X-Character-Owner": str(os.getpid())}
        agent = {"Authorization": f"Bearer {gateway.access.agent_key}"}
        with post(url, {"id_slot": 1, "lora": [{"id": 0, "scale": 1}]}, role) as response:
            assert json.load(response) == {"fixture": True}
        with gateway.access.agent_turn():
            with pytest.raises(urllib.error.HTTPError) as busy:
                post(url, {}, role)
            assert busy.value.code == 423
            assert json.load(busy.value)["error"]["code"] == "inference_busy"
            with post(url, {"stream": True, "lora": [{"id": 0, "scale": 1}]}, agent) as response:
                assert list(response) == [b'data: {"fixture":true}\n', b'\n', b'data: [DONE]\n', b'\n']
            with pytest.raises(urllib.error.HTTPError) as overflow:
                post(url, {"fixture_overflow": True}, agent)
            assert overflow.value.code == 400
            assert json.load(overflow.value)["error"]["code"] == "context_length_exceeded"
        with post(url, {"lora": [{"id": 0, "scale": 0}]}, role) as response:
            assert response.status == 200
        assert sum(path.startswith("/slots/") for path, _ in requests) == 3
        generated = [value for path, value in requests if path == "/v1/chat/completions"]
        assert all(value["id_slot"] == 0 for value in generated)
        assert [value["lora"] for value in generated] == [
            [{"id": 0, "scale": 1.0}, {"id": 1, "scale": 0.0}],
            [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}],
            [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 1.0}],
            [{"id": 0, "scale": 0.0}, {"id": 1, "scale": 0.0}],
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
