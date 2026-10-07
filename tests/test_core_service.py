"""The core's model lease (core.py): who may switch or stop the model, what
a lease loads, and the router loading it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_agent import api, core, load_plan
from edge_agent.runtime import DEFAULT_MODEL


def test_live_character_lease_cannot_be_stolen_or_stopped(tmp_path, monkeypatch):
    manager = core.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.owner_pid = 123
    monkeypatch.setattr(core, "pid_alive", lambda pid: True)
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
    manager = core.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.owner_pid = 123
    manager.router.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(manager, "_model_ready", lambda: True)
    monkeypatch.setattr(core, "pid_alive", lambda pid: False)
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
    manager = core.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.hold_handoff()
    assert manager.handoff_until > 0


def test_stop_releases_base_server(tmp_path, monkeypatch):
    manager = core.EdgeAgent(tmp_path)
    manager.mode = "base"
    manager.router.process = SimpleNamespace(
        poll=lambda: None, terminate=lambda: None, wait=lambda *args, **kwargs: 0
    )
    monkeypatch.setattr(
        manager.router, "request", lambda *args, **kwargs: {"success": True}
    )
    status = manager.stop_service()
    assert manager.router.process is None
    assert status["mode"] == "stopped"
    assert status["ready"] is False


def _lease_fixture(manager, monkeypatch, tmp_path, model_limit, reported):
    manager.router.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(load_plan, "find_llama_server", lambda: "llama-server")
    monkeypatch.setattr(load_plan, "model_file", lambda directory, name: tmp_path / name)
    monkeypatch.setattr(
        load_plan,
        "model_metadata",
        lambda path: {"architecture": "fixture", "adapter": False, "split": False,
                      "projector": False, "context_length": model_limit},
    )
    monkeypatch.setattr(
        load_plan,
        "adapter_file",
        lambda directory, base, name: tmp_path / name if name else None,
    )
    monkeypatch.setattr(manager.router, "state", lambda: {"value": "loaded"})
    monkeypatch.setattr(
        manager.router,
        "request",
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
    limit), borrowed serially without reducing the Agent window. The server
    is placed by EdgeAgent (--fit), mapped from disk, its cache bounded.
    The character's adapter is the lease's (index 0); the Agent keeps its own
    when its model is the leased one, and each request names only its own."""
    manager = core.EdgeAgent(tmp_path)
    original = manager._save_config(
        {
            **manager.config,
            "model": "base.gguf",
            "lora": standalone_lora,
            "context_size": 131072,
        }
    )
    saved = manager.config_path.read_bytes()
    pool = model_limit
    agent_window = pool
    _lease_fixture(manager, monkeypatch, tmp_path, model_limit, pool)
    leased_model = "base.gguf" if same_base else "other.gguf"
    status = manager.switch(
        "character",
        123,
        {"model": leased_model, "lora": leased_lora, "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert "parallel = 1" in preset
    assert "kv-unified" not in preset
    assert f"ctx-size = {pool}" in preset
    for line in ("load-mode = mmap", "lazy-mode = on", "cache-ram = 256",
                 "ctx-checkpoints = 2", "n-gpu-layers = auto", "fit = on"):
        assert line in preset
    assert manager.agent_slot == manager.chat.slot_id == 0
    assert manager.chat.context_size == status["context_size"] == agent_window
    agent = standalone_lora if same_base else ""
    loaded = list(dict.fromkeys(name for name in (leased_lora, agent) if name))
    assert (f"lora = {','.join((tmp_path / name).as_posix() for name in loaded)}" in preset) == bool(loaded)
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
    manager = core.EdgeAgent(tmp_path)
    _lease_fixture(manager, monkeypatch, tmp_path, 131072, 131072)
    projector = tmp_path / "mmproj-fixture.gguf"
    monkeypatch.setattr(load_plan, "ensure_projector", lambda path, architecture: projector)
    manager.switch(
        "character", 123,
        {"model": "base.gguf", "lora": "", "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert f"mmproj = {projector.as_posix()}" in preset
    assert "no-mmproj-offload = true" in preset


def test_a_small_pool_bounds_the_lease_and_keeps_the_agent(tmp_path, monkeypatch):
    manager = core.EdgeAgent(tmp_path)
    manager._save_config({**manager.config, "context_size": 16384})
    _lease_fixture(manager, monkeypatch, tmp_path, 131072, 16384)
    manager.switch(
        "character", 123,
        {"model": "base.gguf", "lora": "", "port": 18088, "context_size": 32768},
    )
    preset = (tmp_path / ".runtime/models.ini").read_text()
    assert "parallel = 1" in preset and "ctx-size = 16384" in preset
    assert "kv-unified" not in preset
    assert manager.agent_slot == 0
    assert manager.status()["context_size"] == 16384


def test_busy_stream_cannot_switch_models_but_does_not_hold_service_lock(tmp_path):
    manager = core.EdgeAgent(tmp_path)
    manager.chat_in_progress = True
    with pytest.raises(PermissionError, match="工作正在進行"):
        manager.switch("character", 123, {})
    with pytest.raises(PermissionError, match="工作正在進行"):
        manager.select_model({})
    assert manager.lock.acquire(blocking=False)
    manager.lock.release()


def test_stop_cancels_approval_waits_before_waiting_for_core_lock(
    tmp_path: Path,
) -> None:
    manager = core.EdgeAgent(tmp_path)
    session = manager.chat.session("pending-work")
    session.approvals["pending-write"] = {"type": "fixture"}
    manager.stop_service()
    assert session.cancelled.is_set()
    assert session.approvals == {}


def test_router_acceptance_is_not_model_readiness(tmp_path, monkeypatch):
    from test_models import gguf

    manager = core.EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / DEFAULT_MODEL).write_bytes(gguf())
    manager.router.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(load_plan, "find_llama_server", lambda: "llama-server")
    states = iter(
        [
            {"value": "unloaded"},
            {"value": "loading"},
            {"value": "unloaded", "failed": True, "exit_code": 1},
        ]
    )
    monkeypatch.setattr(manager.router, "state", lambda: next(states))
    monkeypatch.setattr(
        manager.router, "request", lambda *args, **kwargs: {"success": True}
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

    manager = core.EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / DEFAULT_MODEL).write_bytes(gguf())
    parent = SimpleNamespace(poll=lambda: None)
    manager.router.process = parent
    monkeypatch.setattr(load_plan, "find_llama_server", lambda: "llama-server")
    states = iter([{"value": "unloaded"}, {"value": "loading"}, {"value": "loaded"}])
    monkeypatch.setattr(manager.router, "state", lambda: next(states))
    monkeypatch.setattr(
        manager.router,
        "request",
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
    assert manager.router.process is parent
    assert manager.active_model == DEFAULT_MODEL
    assert manager.chat.model == DEFAULT_MODEL
