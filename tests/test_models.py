from __future__ import annotations

import hashlib
import io
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from edge_agent import api, model_jobs, models
from edge_agent.core import EdgeAgent
from edge_agent.runtime import DEFAULT_MODEL


def gguf(*, context=8192, adapter=False, split=1, architecture="qwen2", extra=None):
    entries = {
        "general.architecture": architecture,
        "general.name": "Fixture",
        architecture + ".context_length": context,
        "split.count": split,
        "tokenizer.chat_template": "{% if tools %}tool_calls{% endif %}",
    }
    if adapter:
        entries["general.type"] = "adapter"
    entries.update(extra or {})

    def string(value):
        data = value.encode()
        return struct.pack("<Q", len(data)) + data

    result = b"GGUF" + struct.pack("<IQQ", 3, 1, len(entries))
    for key, value in entries.items():
        result += string(key)
        if isinstance(value, str):
            result += struct.pack("<I", 8) + string(value)
        elif isinstance(value, float):
            result += struct.pack("<If", 6, value)
        else:
            result += struct.pack("<II", 4, value)
    return result


@pytest.fixture
def core(tmp_path, monkeypatch):
    manager = EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / DEFAULT_MODEL).write_bytes(gguf(context=98304))
    (manager.model_dir / "replacement.gguf").write_bytes(gguf())
    starts = []

    def start(mode, owner, character):
        starts.append(manager.config["model"])
        manager.router.process = SimpleNamespace(
            poll=lambda: None, terminate=lambda: None, wait=lambda *args: 0
        )
        manager.mode = mode
        manager.active_model = manager.config["model"]
        manager.active_context_size = manager.config["context_size"]
        manager.chat.configure_context(manager.active_context_size)

    monkeypatch.setattr(manager, "_start_child", start)
    monkeypatch.setattr(manager, "_model_ready", lambda: True)
    start("base", None, None)
    return manager, starts


def test_discovery_marks_adapters_split_files_and_corruption(tmp_path):
    (tmp_path / "reply.gguf").write_bytes(gguf())
    (tmp_path / "lora.gguf").write_bytes(gguf(adapter=True))
    (tmp_path / "split.gguf").write_bytes(gguf(split=2))
    (tmp_path / "broken.gguf").write_bytes(b"not gguf")
    (tmp_path / "mmproj.gguf").write_bytes(gguf(architecture="clip"))
    listing = {item["filename"]: item for item in models.list_models(tmp_path)}
    assert listing["reply.gguf"]["context_length"] == 8192
    assert listing["reply.gguf"]["tool_template"] is True
    assert listing["lora.gguf"]["adapter"] is True
    assert listing["split.gguf"]["split"] is True
    assert listing["broken.gguf"]["error"]
    assert listing["mmproj.gguf"]["projector"] is True


@pytest.mark.parametrize("name", ["../x.gguf", "..\\x.gguf", "C:x.gguf", "x.exe", ""])
def test_model_names_cannot_escape_model_directory(name):
    with pytest.raises(ValueError):
        models.model_filename(name)


def test_model_metadata_cache_tracks_actual_file_changes(tmp_path):
    path = tmp_path / "fixture.gguf"
    path.write_bytes(gguf(context=8192))
    first = models.model_metadata(path)
    first["context_length"] = 1
    assert models.model_metadata(path)["context_length"] == 8192
    path.write_bytes(gguf(context=16384, architecture="gemma4"))
    assert models.model_metadata(path)["context_length"] == 16384


def test_switch_uses_model_context_limit_and_retains_ram_sessions(core):
    manager, starts = core
    session = manager.chat.session("memory-fixture")
    manager.select_model({"model": "replacement.gguf"})
    assert manager.active_model == "replacement.gguf"
    assert manager.config["context_size"] == 8192
    assert manager.chat.session("memory-fixture") is session
    assert manager.chat.context_size == 8192
    assert EdgeAgent(manager.core_dir).config == manager.config
    assert starts == [DEFAULT_MODEL, "replacement.gguf"]


def test_bad_model_is_rejected_before_stopping_current_model(core):
    manager, starts = core
    (manager.model_dir / "bad.gguf").write_bytes(b"bad")
    with pytest.raises(ValueError):
        manager.select_model({"model": "bad.gguf"})
    assert manager.router.process is not None
    assert starts == [DEFAULT_MODEL]


def test_optional_user_adapter_can_be_selected_and_cleared(core):
    manager, _ = core
    (manager.model_dir / "mine.gguf").write_bytes(gguf(adapter=True))
    manager.select_model({"model": DEFAULT_MODEL, "lora": "mine.gguf"})
    assert manager.config["lora"] == "mine.gguf"
    manager.select_model({"model": "replacement.gguf"})
    assert manager.config["lora"] == ""


def test_wrong_adapter_rejected_before_interrupting_current_model(core):
    manager, starts = core
    (manager.model_dir / "wrong.gguf").write_bytes(
        gguf(adapter=True, architecture="gemma4")
    )
    with pytest.raises(ValueError, match="architecture"):
        manager.select_model(
            {"model": DEFAULT_MODEL, "lora": "wrong.gguf"}
        )
    with pytest.raises(ValueError, match="adapter"):
        manager.select_model(
            {"model": DEFAULT_MODEL, "lora": "replacement.gguf"}
        )
    assert starts == [DEFAULT_MODEL]


def test_loader_failure_restores_model_and_does_not_save_bad_selection(
    core, monkeypatch
):
    manager, _ = core
    previous = dict(manager.config)
    start = manager._start_child

    def fail(mode, owner, character):
        if manager.config["model"] == "replacement.gguf":
            raise RuntimeError("out of memory")
        start(mode, owner, character)

    monkeypatch.setattr(manager, "_start_child", fail)
    with pytest.raises(RuntimeError, match="out of memory"):
        manager.select_model({"model": "replacement.gguf"})
    assert manager.config == previous
    assert manager.status()["ready"] is True
    assert manager.active_model == DEFAULT_MODEL
    assert not manager.loading
    assert not manager.config_path.exists()


def test_switch_rejects_character_lease_and_active_work(core):
    manager, _ = core
    manager.mode = "character"
    with pytest.raises(PermissionError, match="SilentTalk"):
        manager.select_model({"model": "replacement.gguf"})
    manager.mode = "base"
    entered, release = threading.Event(), threading.Event()

    def work():
        with manager.lock:
            entered.set()
            release.wait(5)

    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(5)
    try:
        with pytest.raises(PermissionError, match="工作"):
            manager.select_model({"model": "replacement.gguf"})
    finally:
        release.set()
        worker.join()


def test_hub_catalog_pins_commit_and_checksum(monkeypatch):
    data = {
        "sha": "a" * 40,
        "siblings": [
            {
                "rfilename": "weights/model.gguf",
                "size": 123,
                "lfs": {"sha256": "b" * 64},
            },
            {"rfilename": "mmproj.gguf", "lfs": {"sha256": "b" * 64}},
            {"rfilename": "model-00001-of-00002.gguf", "lfs": {"sha256": "b" * 64}},
        ],
    }
    monkeypatch.setattr(
        models.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(data).encode()),
    )
    catalog = models.hub_catalog("owner/repo")
    assert len(catalog["files"]) == 2
    assert catalog["files"][1]["projector"] is True
    assert "/resolve/" + "a" * 40 + "/" in catalog["files"][0]["url"]
    assert catalog["files"][0]["sha256"] == "b" * 64
    with pytest.raises(ValueError):
        models.hub_catalog("http://localhost/arbitrary")


def test_background_download_verifies_file_and_reports_progress(core, monkeypatch):
    manager, _ = core
    body = gguf()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    monkeypatch.setattr(
        model_jobs,
        "hub_catalog",
        lambda repo: {
            "files": [
                {
                    "filename": "new.gguf",
                    "url": f"http://127.0.0.1:{server.server_port}/weights",
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            ]
        },
    )
    try:
        manager.start_model_job(
            "download", {"repository": "owner/repo", "filename": "new.gguf"}
        )
        assert manager.jobs.lock.acquire(timeout=5)
        manager.jobs.lock.release()
        assert manager.jobs.current["state"] == "done"
        assert manager.jobs.current["received"] == manager.jobs.current["total"] == len(body)
        assert (manager.model_dir / "new.gguf").read_bytes() == body
        assert manager.active_model == DEFAULT_MODEL
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_existing_config_migrates_without_chat_or_workspace_fields(tmp_path):
    (tmp_path / "config.json").write_text('{"agent":false,"thinking_effort":"off"}')
    config = EdgeAgent(tmp_path).config
    assert config["model"] == DEFAULT_MODEL
    assert config["agent"] is False


def test_model_listing_cannot_finish_with_stale_download_inventory(core, monkeypatch):
    manager, _ = core
    manager.jobs.current = {"state": "running", "action": "download"}

    def scan(directory):
        manager.jobs.current = {"state": "done", "action": "download"}
        return []

    monkeypatch.setattr("edge_agent.core.list_models", scan)
    assert manager.models_status()["job"]["state"] == "running"


def test_model_manager_opens_even_if_inference_cannot_start(monkeypatch):
    status = {"ready": False, "mode": "starting", "error": "missing model"}
    monkeypatch.setattr(api, "service_status", lambda: status)
    assert api.ensure_service(require_ready=False) == status


def test_parallel_model_jobs_are_rejected(core):
    manager, _ = core
    manager.jobs.lock.acquire()
    try:
        with pytest.raises(PermissionError):
            manager.start_model_job("switch", {"model": "replacement.gguf"})
    finally:
        manager.jobs.lock.release()


def test_an_adapter_carries_its_own_sampling_per_mode(tmp_path):
    stamped = tmp_path / "agent.gguf"
    stamped.write_bytes(gguf(adapter=True, extra={"edgeagent.sampling.off.temp": 0.2,
                                                  "edgeagent.sampling.thinking.temp": 1.0}))
    plain = tmp_path / "plain.gguf"
    plain.write_bytes(gguf(adapter=True))
    assert models.model_metadata(stamped)["sampling"] == {"off": 0.2, "thinking": 1.0}
    assert models.model_metadata(plain)["sampling"] == {}
