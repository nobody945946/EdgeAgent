from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_agent import api, runtime, service


def test_live_character_lease_cannot_be_stolen_or_stopped(tmp_path, monkeypatch):
    manager = service.EdgeAgent(tmp_path)
    manager.mode = "character"
    manager.owner_pid = 123
    monkeypatch.setattr(service, "_pid_alive", lambda pid: True)
    with pytest.raises(PermissionError):
        manager.switch("base")
    with pytest.raises(PermissionError):
        manager.stop_service()
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

    assert _value_after(args, "--ctx-size") == "16384"
    assert _value_after(args, "--parallel") == "1"
    assert "--tools" not in args


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


def test_character_server_gets_two_slots_and_lora() -> None:
    args = runtime.build_server_args(
        "llama-server",
        Path("e4b.gguf"),
        port=18088,
        context_size=32768,
        threads=4,
        gpu_layers=99,
        low_vram=True,
        lora_path=Path("character.gguf"),
        slots=2,
    )

    assert _value_after(args, "--ctx-size") == "65536"
    assert _value_after(args, "--parallel") == "2"
    assert _value_after(args, "--lora") == "character.gguf"
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
    (manager.model_dir / service.DEFAULT_AGENT_MODEL).write_bytes(gguf())
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


def test_router_keeps_parent_and_waits_for_loaded_model(tmp_path, monkeypatch):
    from test_models import gguf

    manager = service.EdgeAgent(tmp_path)
    manager.model_dir.mkdir()
    (manager.model_dir / service.DEFAULT_AGENT_MODEL).write_bytes(gguf())
    parent = SimpleNamespace(poll=lambda: None)
    manager.process = parent
    monkeypatch.setattr(service, "find_llama_server", lambda: "llama-server")
    states = iter([{"value": "unloaded"}, {"value": "loading"}, {"value": "loaded"}])
    monkeypatch.setattr(manager, "_model_state", lambda: next(states))
    monkeypatch.setattr(
        manager, "_model_request", lambda *args, **kwargs: {"success": True}
    )
    manager._start_child("base", None, None)
    assert manager.process is parent
    assert manager.active_model == service.DEFAULT_AGENT_MODEL
    assert manager.chat.model == service.DEFAULT_AGENT_MODEL
