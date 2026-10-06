from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_agent import api, records, runtime, service


@pytest.mark.parametrize(
    "exception", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError]
)
def test_http_disconnect_ends_connection_without_server_traceback(
    monkeypatch, exception
):
    def disconnected(handler):
        raise exception("fixture")

    monkeypatch.setattr(service.BaseHTTPRequestHandler, "handle", disconnected)
    handler = object.__new__(service.Handler)
    handler.handle()
    assert handler.close_connection is True


def test_http_handler_keeps_unexpected_failures_visible(monkeypatch):
    def failed(handler):
        raise RuntimeError("fixture")

    monkeypatch.setattr(service.BaseHTTPRequestHandler, "handle", failed)
    with pytest.raises(RuntimeError, match="fixture"):
        object.__new__(service.Handler).handle()


def test_records_prepare_and_status_follow_folder_availability(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(OSError):
        records.prepare_record_folder(str(missing))
    assert not missing.exists()
    assert records.recording_status(False, str(tmp_path)) == "關閉"
    assert not records.save_session(str(tmp_path), "fixture", [])
    records.prepare_record_folder(str(tmp_path))
    assert records.recording_status(True, str(tmp_path)) == "記錄中"
    assert records.save_session(str(tmp_path), "fixture", [])
    (tmp_path / records.MARKER).unlink()
    assert records.recording_status(True, str(tmp_path)).startswith("暫停")
    assert not records.save_session(str(tmp_path), "missing-marker", [])
    assert not (tmp_path / "edgeagent" / "missing-marker.jsonl").exists()


def test_records_accept_a_local_ssd_folder(tmp_path):
    assert records.prepare_record_folder(str(tmp_path)) == tmp_path.resolve()
    assert records.save_session(
        str(tmp_path), "fixture", [{"type": "fixture", "text": "LOCAL_SSD_FIXTURE"}]
    )
    assert "LOCAL_SSD_FIXTURE" in (tmp_path / "edgeagent" / "fixture.jsonl").read_text(
        encoding="utf-8"
    )
    (tmp_path / records.MARKER).unlink()
    assert not records.save_session(str(tmp_path), "paused", [{"text": "PAUSED_FIXTURE"}])
    assert records.recording_status(True, str(tmp_path)).startswith("暫停")
    records.prepare_record_folder(str(tmp_path))
    assert records.recording_status(True, str(tmp_path)) == "記錄中"
    assert records.save_session(str(tmp_path), "resumed", [{"text": "RESUMED_FIXTURE"}])
    assert sorted(path.name for path in (tmp_path / "edgeagent").iterdir()) == [
        "fixture.jsonl", "resumed.jsonl"
    ]


def test_records_reject_a_file(tmp_path):
    file = tmp_path / "fixture.txt"
    file.write_text("fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="紀錄資料夾"):
        records.prepare_record_folder(str(file))
    assert not (tmp_path / records.MARKER).exists()


def test_live_character_lease_cannot_be_stolen_or_stopped(tmp_path, monkeypatch):
    manager = service.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.owner_pid = 123
    monkeypatch.setattr(service, "_pid_alive", lambda pid: True)
    cancellations = []
    monkeypatch.setattr(manager.chat, "cancel_all", lambda: cancellations.append(True))
    with pytest.raises(PermissionError):
        manager.switch("base")
    with pytest.raises(PermissionError):
        manager.stop_service()
    assert cancellations == []
    with pytest.raises(PermissionError):
        manager.switch("character", 456)


def test_character_takeover_reuses_running_server(tmp_path, monkeypatch):
    manager = service.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.port = 18088
    manager.owner_pid = 123
    manager.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(manager, "_model_ready", lambda: True)
    monkeypatch.setattr(service, "_pid_alive", lambda pid: False)
    status = manager.switch("character", 456)
    assert manager.owner_pid == 456
    assert status["ready"] is True


def test_slow_status_does_not_spawn_duplicate_manager(monkeypatch):
    statuses = iter([None, {"ready": True}])
    monkeypatch.setattr(api, "service_status", lambda: next(statuses))
    monkeypatch.setattr(api, "control_service_present", lambda: True)

    def unexpected_spawn(*args, **kwargs):
        pytest.fail("existing manager must not be spawned again")

    monkeypatch.setattr(api.subprocess, "Popen", unexpected_spawn)
    assert api.ensure_service()["ready"] is True


def test_restart_handoff_suspends_crash_restore(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.hold_handoff()
    assert manager.handoff_until > 0


def test_stop_releases_base_server(tmp_path, monkeypatch):
    manager = service.EdgeAgent(tmp_path)
    manager.mode = "base"
    manager.process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: None, wait=lambda *args, **kwargs: 0
    )
    monkeypatch.setattr(
        manager, "_model_request", lambda *args, **kwargs: {"success": True}
    )
    status = manager.stop_service()
    assert manager.process is None
    assert status["mode"] == "stopped"
    assert status["ready"] is False


def test_reserved_port_never_kills_an_unrelated_process(monkeypatch):
    monkeypatch.setattr(service, "_listening_pid", lambda port: 321)
    monkeypatch.setattr(
        service, "_process_image", lambda pid: Path("C:/Windows/other.exe")
    )
    with pytest.raises(RuntimeError, match="occupied by PID 321"):
        service._clear_reserved_port(18089)


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_base_server_is_one_slot_without_an_extra_agent_loop() -> None:
    args = runtime.build_server_args(
        "llama-server",
        Path("e4b.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=runtime.GENERAL_CONTEXT_SIZE,
        threads=4,
        gpu_layers=99,
        low_vram=True,
    )

    assert _value_after(args, "--ctx-size") == "131072"
    assert _value_after(args, "--parallel") == "1"
    assert "--kv-unified" not in args
    assert _value_after(args, "--load-mode") == "mlock"
    assert "--tools" not in args


def _lease_fixture(manager, monkeypatch, tmp_path, model_limit, reported):
    manager.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(service, "find_llama_server", lambda: "llama-server")
    monkeypatch.setattr(manager, "_model_path", lambda name: tmp_path / name)
    monkeypatch.setattr(
        service,
        "model_metadata",
        lambda path: {"architecture": "fixture", "adapter": False, "split": False,
                      "projector": False, "context_length": model_limit},
    )
    monkeypatch.setattr(
        manager, "_adapter_path", lambda base, name: tmp_path / name if name else None
    )
    monkeypatch.setattr(manager, "_model_state", lambda: {"value": "loaded"})
    monkeypatch.setattr(
        manager,
        "_model_request",
        lambda *args, **kwargs: {"default_generation_settings": {"n_ctx": reported},
                                 "modalities": {}},
    )


@pytest.mark.parametrize(
    "same_base,standalone_lora,leased_lora",
    [
        (True, "", ""),
        (False, "", ""),
        (True, "", "adapter.gguf"),
        (True, "agent.gguf", "role.gguf"),
        (True, "same.gguf", "same.gguf"),
        (False, "agent.gguf", "role.gguf"),
        (False, "agent.gguf", ""),
    ],
)
@pytest.mark.parametrize("model_limit", [131072, 98304])
def test_character_lease_shares_model_and_adapter_then_restores_preferences(
    tmp_path,
    monkeypatch,
    same_base,
    standalone_lora,
    leased_lora,
    model_limit,
):
    """One pool for both clients: EdgeAgent's own size (up to the model's
    limit), the character's part reserved, the rest the Agent's window; the
    server is placed by EdgeAgent (--fit), locked in RAM, its cache bounded.
    The character's adapter is the lease's (index 0); the Agent keeps its own
    when its model is the leased one, and each request names only its own."""
    manager = service.EdgeAgent(tmp_path)
    original = manager._save_config(
        {
            **manager.config,
            "model": "base.gguf",
            "lora": standalone_lora,
            "context_size": 131072,
        }
    )
    saved = manager.config_path.read_bytes()
    role_slots = 2 if leased_lora else 1
    pool = model_limit
    agent_window = pool - role_slots * 32768
    _lease_fixture(manager, monkeypatch, tmp_path, model_limit, pool)
    leased_model = "base.gguf" if same_base else "other.gguf"
    status = manager.switch(
        "character",
        123,
        {"model": leased_model, "lora": leased_lora, "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert f"parallel = {role_slots + 1}" in preset
    assert f"ctx-size = {pool}" in preset
    for line in ("kv-unified = true", "load-mode = mlock", "cache-ram = 256",
                 "ctx-checkpoints = 2", "n-gpu-layers = auto", "fit = on"):
        assert line in preset
    assert manager.agent_slot == manager.chat.slot_id == role_slots
    assert manager.chat.context_size == status["context_size"] == agent_window
    agent = standalone_lora if same_base else ""
    loaded = list(dict.fromkeys(name for name in (leased_lora, agent) if name))
    assert (f"lora = {','.join(str(tmp_path / name) for name in loaded)}" in preset) == bool(loaded)
    assert ("lora-init-without-apply = true" in preset) == bool(loaded)
    assert manager.chat.lora_id == (loaded.index(agent) if agent else None)
    assert status["model"] == leased_model
    assert status["lora"] == agent and status["character_lora"] == leased_lora
    assert manager.models_status()["active_lora"] == agent
    assert manager.config == original
    assert manager.config_path.read_bytes() == saved

    restored = manager.switch("base", 123)
    assert restored["model"] == original["model"]
    assert restored["lora"] == original["lora"]
    assert manager.agent_slot == manager.chat.slot_id == 0
    assert manager.chat.lora_id == (0 if standalone_lora else None)
    assert manager.config == original
    assert manager.config_path.read_bytes() == saved


def test_a_lease_brings_the_bases_projector_kept_off_the_gpu(tmp_path, monkeypatch):
    """Images posted in a chat reach the Agent during a lease (OCR)."""
    manager = service.EdgeAgent(tmp_path)
    _lease_fixture(manager, monkeypatch, tmp_path, 131072, 131072)
    projector = tmp_path / "mmproj-fixture.gguf"
    monkeypatch.setattr(service, "ensure_projector", lambda path, architecture: projector)
    manager.switch(
        "character", 123,
        {"model": "base.gguf", "lora": "", "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert f"mmproj = {projector.as_posix()}" in preset
    assert "no-mmproj-offload = true" in preset


def test_a_pool_the_character_fills_leases_without_the_agent(tmp_path, monkeypatch):
    """A 16K setting leaves the Agent nothing once the character's 32K is
    reserved: the character still gets its part, the Agent waits."""
    manager = service.EdgeAgent(tmp_path)
    manager._save_config({**manager.config, "context_size": 16384})
    _lease_fixture(manager, monkeypatch, tmp_path, 131072, 32768)
    manager.switch(
        "character", 123,
        {"model": "base.gguf", "lora": "", "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert "parallel = 1" in preset and "ctx-size = 32768" in preset
    assert "kv-unified" not in preset
    assert manager.agent_slot is None
    assert manager.status()["agent_slot"] is None


def test_busy_stream_cannot_switch_models_but_does_not_hold_service_lock(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    manager.chat_in_progress = True
    with pytest.raises(PermissionError, match="工作正在進行"):
        manager.switch("character", 123, {})
    with pytest.raises(PermissionError, match="工作正在進行"):
        manager.select_model({})
    assert manager.lock.acquire(blocking=False)
    manager.lock.release()


def test_multimodal_projector_uses_canonical_server_and_low_vram_policy():
    args = runtime.build_server_args(
        "llama-server",
        Path("base.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=4096,
        threads=4,
        gpu_layers=99,
        low_vram=True,
        projector_path=Path("mmproj.gguf"),
    )
    assert _value_after(args, "--mmproj") == "mmproj.gguf"
    assert "--no-mmproj-offload" in args
    assert _value_after(args, "--parallel") == "1"


@pytest.mark.parametrize("gpu_layers", [0, 33, 99])
def test_manual_device_split_disables_automatic_fitting(gpu_layers):
    args = runtime.build_server_args(
        "llama-server", Path("base.gguf"), port=runtime.GENERAL_PORT,
        context_size=98304, threads=4, gpu_layers=gpu_layers, low_vram=True,
    )
    assert _value_after(args, "--n-gpu-layers") == str(gpu_layers)
    assert _value_after(args, "--ctx-size") == "98304"
    assert _value_after(args, "--fit") == "off"
    assert "--fit-ctx" not in args and "--fit-target" not in args
    assert _value_after(args, "--load-mode") == "mlock"


def test_automatic_device_fitting_preserves_large_context_and_cache_precision():
    args = runtime.build_server_args(
        "llama-server",
        Path("base.gguf"),
        port=runtime.GENERAL_PORT,
        context_size=131072,
        threads=4,
        gpu_layers=-1,
        low_vram=True,
    )
    assert _value_after(args, "--n-gpu-layers") == "auto"
    assert _value_after(args, "--ctx-size") == "131072"
    assert _value_after(args, "--fit-ctx") == "131072"
    assert _value_after(args, "--fit-target") == "512"
    assert _value_after(args, "--cache-type-k") == "q8_0"
    assert _value_after(args, "--cache-type-v") == "q8_0"
    assert _value_after(args, "--cache-ram") == "256"
    assert _value_after(args, "--ctx-checkpoints") == "2"


def test_character_server_gets_two_slots_and_lora() -> None:
    args = runtime.build_server_args(
        "llama-server",
        Path("e4b.gguf"),
        port=18088,
        context_size=32768,
        threads=4,
        gpu_layers=99,
        low_vram=True,
        lora_paths=[Path("character.gguf"), Path("agent.gguf")],
        slots=2,
    )

    # One pool both slots draw from, the model locked in RAM, the prompt
    # cache bounded in every mode (upstream keeps 8 GiB and 32 checkpoints).
    assert _value_after(args, "--ctx-size") == "32768"
    assert _value_after(args, "--parallel") == "2"
    assert "--kv-unified" in args
    assert _value_after(args, "--load-mode") == "mlock"
    assert _value_after(args, "--cache-ram") == "256"
    assert _value_after(args, "--ctx-checkpoints") == "2"
    # Both adapters loaded, none applied unless a request names it.
    assert _value_after(args, "--lora") == "character.gguf,agent.gguf"
    assert "--lora-init-without-apply" in args
    assert "--tools" not in args


def test_config_never_persists_workspace_or_chat(tmp_path: Path) -> None:
    manager = service.EdgeAgent(tmp_path)

    config = manager.update_config({"thinking_effort": "high", "agent": False})

    assert config == {
        **service.DEFAULT_CONFIG,
        "thinking_effort": "high",
        "agent": False,
    }
    saved = manager.config_path.read_text(encoding="utf-8")
    assert "working_dir" not in saved
    assert "messages" not in saved


def test_output_budget_is_validated_and_leased_client_is_separate(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    config = manager._validated_config(
        {"context_size": 32768, "max_output_tokens": 16384}
    )
    assert config["max_output_tokens"] == 16384
    for value in (True, 0, -1, 131073, "8192"):
        with pytest.raises((ValueError, TypeError)):
            manager._validated_config({"max_output_tokens": value})
    manager.chat.configure_context(32768, max_output_tokens=16384)
    manager.mode = "base"
    assert manager.models_status()["max_output_tokens"] == 16384
    manager.mode = "character"
    assert manager.models_status()["max_output_tokens"] is None
    with pytest.raises(ValueError):
        manager.update_config({"max_output_tokens": 8192})


def test_working_target_is_separate_from_physical_capacity_and_output(tmp_path):
    manager = service.EdgeAgent(tmp_path)
    for value in (True, 0, 4096, 262145, "32768"):
        with pytest.raises((ValueError, TypeError)):
            manager._validated_config({"working_context_size": value})
    with pytest.raises(ValueError):
        manager.update_config({"working_context_size": 32768})
    manager.chat.configure_context(
        131072, max_output_tokens=8192, working_context_size=32768
    )
    assert manager.chat.context_size == 131072
    assert manager.chat.max_output_tokens == 8192
    assert manager.chat.working_context_size == 32768
    manager.chat.configure_context(131072, working_context_size=131072)
    assert manager.chat.working_context_size == 131072
    manager.chat.configure_context(
        16384, max_output_tokens=8192, working_context_size=8192
    )
    assert manager.chat.working_context_size == 16384
    manager.mode = "character"
    assert manager.models_status()["working_context_size"] is None


def test_legacy_low_agent_context_migrates_without_resetting_other_settings(tmp_path):
    import json

    path = tmp_path / "config.json"
    old = {
        "model": "custom.gguf",
        "lora": "personal.gguf",
        "context_size": 4096,
        "gpu_layers": 12,
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    manager = service.EdgeAgent(tmp_path)
    assert manager.config["context_size"] == 8192
    assert manager.config["model"] == old["model"]
    assert manager.config["lora"] == old["lora"]
    assert manager.config["gpu_layers"] == old["gpu_layers"]
    assert json.loads(path.read_text(encoding="utf-8")) == old
    with pytest.raises(ValueError):
        manager._validated_config({"context_size": 4096})


def test_directory_picker_lists_real_unicode_directories(tmp_path: Path) -> None:
    manager = service.EdgeAgent(tmp_path)
    (tmp_path / "故事").mkdir()
    (tmp_path / "not-a-folder.txt").write_text("x", encoding="utf-8")

    listing = manager.list_directories(str(tmp_path))

    assert {item["name"] for item in listing["directories"]} == {"故事"}
    with pytest.raises(ValueError, match="directory does not exist"):
        manager.list_directories(str(tmp_path / "missing"))


def test_stop_cancels_approval_waits_before_waiting_for_core_lock(
    tmp_path: Path,
) -> None:
    manager = service.EdgeAgent(tmp_path)
    session = manager.chat.session("pending-work")
    session.approvals["pending-write"] = {"type": "fixture"}
    manager.stop_service()
    assert session.cancelled.is_set()
    assert session.approvals == {}


def test_listener_lookup_ignores_time_wait_pid_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = SimpleNamespace(
        stdout=b"  TCP    127.0.0.1:18089    127.0.0.1:65030    TIME_WAIT    0\r\n"
    )
    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: listing)

    assert service._listening_pid(18089) is None


def test_listener_lookup_returns_only_exact_listening_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=(
            b"  TCP    127.0.0.1:18087    0.0.0.0:0    LISTENING    111\r\n"
            b"  TCP    127.0.0.1:18089    0.0.0.0:0    LISTENING    222\r\n"
        ),
    )
    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: listing)

    assert service._listening_pid(18089) == 222


def test_worktree_is_real_and_original_workspace_is_preserved(tmp_path: Path) -> None:
    project = tmp_path / "專案"
    project.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )

    git("init", "-b", "copilot-story-mode")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (project / "code.py").write_text("value = 1\n", encoding="utf-8")
    git("add", "code.py")
    git("commit", "-m", "fixture")
    manager = service.EdgeAgent(tmp_path / "core")
    result = manager.create_worktree(str(project))
    worktree = Path(result["path"])
    assert worktree.is_dir()
    assert manager.workspace_info(str(worktree))["branch"] == result["branch"]
    assert (worktree / "code.py").read_text() == "value = 1\n"
    (worktree / "code.py").write_text("value = 2\n", encoding="utf-8")
    assert (project / "code.py").read_text() == "value = 1\n"


def test_router_acceptance_is_not_model_readiness(tmp_path, monkeypatch):
    from test_models import gguf

    manager = service.EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / service.DEFAULT_MODEL).write_bytes(gguf())
    manager.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(service, "find_llama_server", lambda: "llama-server")
    states = iter(
        [
            {"value": "unloaded"},
            {"value": "loading"},
            {"value": "unloaded", "failed": True, "exit_code": 1},
        ]
    )
    monkeypatch.setattr(manager, "_model_state", lambda: next(states))
    monkeypatch.setattr(
        manager, "_model_request", lambda *args, **kwargs: {"success": True}
    )
    with pytest.raises(RuntimeError, match="failed to load"):
        manager._start_child("base", None, None)
    assert manager.active_model == ""
    assert manager.mode != "base"
    assert not manager.config_path.exists()


@pytest.mark.parametrize("reported_context", [8192, 4096])
def test_router_keeps_parent_and_verifies_loaded_context(
    tmp_path, monkeypatch, reported_context
):
    from test_models import gguf

    manager = service.EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / service.DEFAULT_MODEL).write_bytes(gguf())
    parent = SimpleNamespace(poll=lambda: None)
    manager.process = parent
    monkeypatch.setattr(service, "find_llama_server", lambda: "llama-server")
    states = iter([{"value": "unloaded"}, {"value": "loading"}, {"value": "loaded"}])
    monkeypatch.setattr(manager, "_model_state", lambda: next(states))
    monkeypatch.setattr(
        manager,
        "_model_request",
        lambda path, *args, **kwargs: (
            {"default_generation_settings": {"n_ctx": reported_context}}
            if path.startswith("/props")
            else {"success": True}
        ),
    )
    if reported_context != 8192:
        with pytest.raises(RuntimeError, match="context"):
            manager._start_child("base", None, None)
        assert manager.active_model == ""
        return
    manager._start_child("base", None, None)
    assert manager.process is parent
    assert manager.active_model == service.DEFAULT_MODEL
    assert manager.chat.model == service.DEFAULT_MODEL
