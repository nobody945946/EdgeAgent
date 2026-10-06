from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
    (tmp_path / ".runtime/tools").mkdir()
    monkeypatch.setenv("PATH", "system-only")
    runtime.activate_deployment_tools(tmp_path)
    assert str(directory) in runtime.os.environ["PATH"].split(runtime.os.pathsep)
    assert str(tmp_path / ".runtime/tools") in runtime.os.environ["PATH"].split(
        runtime.os.pathsep
    )
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
    # Only the public base the Agent and SilentTalk share is downloaded; the
    # Agent's adapter ships with the code and is copied next to it.
    assert downloads[0] == runtime.DEFAULT_MODEL
    assert runtime.QWEN_MODEL not in downloads
    assert not any("lora" in name.lower() or "agent-" in name for name in downloads)
    assert deploy.sha256(tmp_path / "models" / runtime.AGENT_ADAPTER) == deploy.ADAPTERS[runtime.AGENT_ADAPTER]


def test_a_damaged_bundled_adapter_is_copied_again(tmp_path):
    target = tmp_path / "models" / runtime.AGENT_ADAPTER
    target.parent.mkdir()
    target.write_bytes(b"not an adapter")
    deploy.install_adapters(tmp_path)
    assert deploy.sha256(target) == deploy.ADAPTERS[runtime.AGENT_ADAPTER]
    assert not target.with_name(target.name + ".partial").exists()


def test_search_deployment_repairs_missing_and_corrupt_tools(tmp_path, monkeypatch):
    probes = []

    def download(url, target, digest):
        assert digest == deploy.SEARCH_TOOLS["rg" if "ripgrep" in url else "fd"][1]
        tool = "rg" if "ripgrep" in url else "fd"
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w") as bundle:
            bundle.writestr("release/" + tool + ".exe", tool.encode())

    monkeypatch.setattr(deploy, "download", download)
    monkeypatch.setattr(
        deploy.subprocess, "run", lambda args, **kwargs: probes.append(args)
    )
    deploy.install_search_tools(tmp_path)
    destination = tmp_path / ".runtime/tools"
    assert (destination / "rg.exe").read_bytes() == b"rg"
    (destination / "rg.exe").write_bytes(b"corrupt")
    (destination / "fd.exe").unlink()
    deploy.install_search_tools(tmp_path)
    assert (destination / "rg.exe").read_bytes() == b"rg"
    assert (destination / "fd.exe").read_bytes() == b"fd"
    assert len(probes) == 4


def test_public_projector_is_pinned_and_belongs_to_exact_shipped_base(
    tmp_path, monkeypatch
):
    downloads = []
    monkeypatch.setattr(
        deploy,
        "download",
        lambda url, target, digest: downloads.append((url, target, digest)),
    )
    path = tmp_path / runtime.DEFAULT_MODEL
    assert deploy.ensure_projector(path, "qwen35") is None
    assert deploy.ensure_projector(tmp_path / "custom.gguf", "gemma4") is None
    target = deploy.ensure_projector(path, "gemma4")
    assert target.name == "mmproj-gemma-4-E4B-it-Q8_0.gguf"
    assert len(downloads) == 1
    assert "b8093469224f83f5c38f691eb906c380e9e63114" in downloads[0][0]
    assert (
        downloads[0][2]
        == "197f49a93027f9843772bd24a6a9e0be2a32a788de5a3def330e9c585d86edd1"
    )


def test_missing_engine_file_is_repaired_with_existing_install_receipt(
    tmp_path, monkeypatch
):
    def download(url, target, digest):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".zip":
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("llama-server.exe", b"fixture executable")
                archive.writestr("required.dll", b"fixture library")
        else:
            target.write_bytes(b"model fixture")

    monkeypatch.setattr(deploy, "download", download)
    monkeypatch.setattr(deploy, "install_agent_runtime", lambda root: None)
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, b"--models-preset", b""
        ),
    )
    first = deploy.install(tmp_path, "cpu")
    engine = tmp_path / first["server"]
    library = engine.parent / "required.dll"
    library.unlink()
    deploy.install(tmp_path, "cpu")
    assert library.read_bytes() == b"fixture library"


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_matching_sdk_receipt_does_not_hide_broken_dependency(
    tmp_path, monkeypatch, repair_succeeds
):
    monkeypatch.setattr(deploy, "install_search_tools", lambda root: None)
    node = tmp_path / f".runtime/node/node-v{runtime.NODE_VERSION}-win-x64/node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"fixture")
    npm = node.parent / "node_modules/npm/bin/npm-cli.js"
    npm.parent.mkdir(parents=True)
    npm.write_bytes(b"fixture")
    destination = tmp_path / ".runtime/pi"
    package = destination / "node_modules/@earendil-works/pi-coding-agent/dist/index.js"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"fixture")
    receipt = destination / ".installed.sha256"
    lock = Path(deploy.__file__).parent / "pi-runtime-lock.json"
    receipt.write_text(deploy.sha256(lock))
    calls = []
    repaired = False

    def run(arguments, **kwargs):
        nonlocal repaired
        calls.append(arguments)
        if "ci" in arguments:
            repaired = True
            receipt.unlink()
        elif "--input-type=module" in arguments:
            if not repaired or not repair_succeeds:
                raise subprocess.CalledProcessError(1, arguments)
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(deploy, "download", lambda *args: None)
    monkeypatch.setattr(deploy.subprocess, "run", run)
    if repair_succeeds:
        deploy.install_agent_runtime(tmp_path)
        assert receipt.read_text() == deploy.sha256(lock)
        deploy.install_agent_runtime(tmp_path)
    else:
        with pytest.raises(subprocess.CalledProcessError):
            deploy.install_agent_runtime(tmp_path)
        assert not receipt.exists()
    assert sum("ci" in arguments for arguments in calls) == 1
    assert sum("--input-type=module" in arguments for arguments in calls) == (
        3 if repair_succeeds else 2
    )
