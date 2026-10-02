from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from edge_agent import deploy, runtime


@pytest.fixture
def download_server():
    payload = b"verified-release-contents" * 1000
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            offset = int(self.headers.get("Range", "bytes=0-")[6:-1])
            seen.append(offset)
            resumed = self.path != "/ignore-range" and offset > 0
            self.send_response(206 if resumed else 200)
            data = payload[offset:] if resumed else payload
            self.send_header("Content-Length", str(len(data)))
            if resumed:
                self.send_header(
                    "Content-Range", f"bytes {offset}-{len(payload) - 1}/{len(payload)}"
                )
            self.end_headers()
            if self.path == "/truncated" and len(seen) == 1:
                self.wfile.write(data[:500])
                self.close_connection = True
            else:
                self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payload, seen
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("endpoint", ["/range", "/ignore-range"])
def test_download_resumes_or_restarts_without_duplicate_bytes(
    tmp_path, download_server, endpoint
):
    url, payload, seen = download_server
    target = tmp_path / "asset.bin"
    target.with_suffix(".bin.part").write_bytes(payload[:500])
    digest = hashlib.sha256(payload).hexdigest()
    deploy.download(url + endpoint, target, digest)
    assert target.read_bytes() == payload
    assert seen == [500]
    deploy.download(url + endpoint, target, digest)
    assert seen == [500]


def test_invalid_checksum_never_overwrites_existing_asset(tmp_path, download_server):
    url, _, _ = download_server
    target = tmp_path / "asset.bin"
    target.write_bytes(b"existing user asset")
    with pytest.raises(RuntimeError, match="rerun setup"):
        deploy.download(url, target, "0" * 64, attempts=1)
    assert target.read_bytes() == b"existing user asset"
    assert list(tmp_path.glob("asset.bin.part.invalid.*"))


def test_truncated_transfer_preserves_bytes_for_resume(tmp_path, download_server):
    url, payload, seen = download_server
    target = tmp_path / "asset.bin"
    deploy.download(url + "/truncated", target, hashlib.sha256(payload).hexdigest())
    assert target.read_bytes() == payload
    assert seen == [0, 500]
    assert not list(tmp_path.glob("*.invalid.*"))


def test_file_change_invalidates_verification_cache(tmp_path):
    target = tmp_path / "asset.bin"
    target.write_bytes(b"good")
    digest = hashlib.sha256(b"good").hexdigest()
    assert deploy.verified(target, digest)
    target.write_bytes(b"tampered")
    assert not deploy.verified(target, digest)


def test_extract_rejects_zip_traversal_before_writing(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("good.txt", "safe")
        bundle.writestr("../escaped.txt", "unsafe")
    with pytest.raises(RuntimeError, match="unsafe archive"):
        deploy.extract_zip(archive, tmp_path / "output")
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "output/good.txt").exists()


def test_runtime_uses_independently_installed_server(tmp_path, monkeypatch):
    server = tmp_path / ".runtime/llama/server/llama-server.exe"
    server.parent.mkdir(parents=True)
    server.write_bytes(b"fixture")
    (tmp_path / ".runtime/deployment.json").write_text(
        json.dumps({"server": ".runtime/llama/server/llama-server.exe"})
    )
    monkeypatch.setattr(runtime, "CORE_ROOT", tmp_path)
    assert runtime.find_llama_server() == str(server.resolve())


def test_runtime_rejects_external_server_in_deployment_marker(tmp_path, monkeypatch):
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/deployment.json").write_text(
        json.dumps({"server": "../../arbitrary.exe"})
    )
    monkeypatch.setattr(runtime, "CORE_ROOT", tmp_path)
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    monkeypatch.setattr(runtime, "_winget_llama_server", lambda: None)
    with pytest.raises(runtime.EdgeAgentError):
        runtime.find_llama_server()


def test_service_tools_find_portable_git_without_system_git(tmp_path, monkeypatch):
    directory = tmp_path / ".runtime/git/cmd"
    directory.mkdir(parents=True)
    monkeypatch.setenv("PATH", "system-only")
    runtime.activate_deployment_tools(tmp_path)
    assert str(directory) in runtime.os.environ["PATH"].split(runtime.os.pathsep)
    runtime.activate_deployment_tools(tmp_path)
    assert (
        runtime.os.environ["PATH"].split(runtime.os.pathsep).count(str(directory)) == 1
    )


def test_agent_install_does_not_require_private_toy_assets(tmp_path, monkeypatch):
    downloads = []
    monkeypatch.setattr(
        deploy, "download", lambda url, target, digest: downloads.append(target.name)
    )
    monkeypatch.setattr(deploy, "install_agent_runtime", lambda root: None)
    monkeypatch.setattr(
        deploy,
        "install_character_assets",
        lambda root: pytest.fail("toy must be opt-in"),
    )

    def extract(archive, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "llama-server.exe").touch()

    monkeypatch.setattr(deploy, "extract_zip", extract)
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, b"--models-preset", b""
        ),
    )
    assert deploy.install(tmp_path, "cpu")["backend"] == "cpu"
    assert downloads[0] == runtime.DEFAULT_AGENT_MODEL
    assert deploy.LORA_NAME not in downloads
