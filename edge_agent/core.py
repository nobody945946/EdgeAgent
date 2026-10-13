"""The core service's state: which model and mode are loaded, who leases the
model, the Agent's own selection, and the status every client reads (and
the status stream pushes each time it changes)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from edge_agent import apis, hardware
from edge_agent.chat import NativeChat
from edge_agent.config import MODEL_SETTINGS, load_config, save_config, validated_config
from edge_agent.inference import InferenceGateway
from edge_agent.load_plan import plan_load
from edge_agent.model_jobs import ModelJobs
from edge_agent.models import (
    adapter_file,
    list_models,
    model_file,
    model_metadata,
    projector_file,
)
from edge_agent.processes import pid_alive
from edge_agent.records import recording_status
from edge_agent.router import ModelRouter
from edge_agent.runtime import (
    CONTROL_PORT,
    CORE_ROOT,
    MIN_AGENT_CONTEXT_SIZE,
    MODEL_DIR,
)
from edge_agent.terminal import NativeTerminal


class StatusChanges:
    """Wakes the status streams (GET /status/events) whenever something the
    status shows may have changed. Driven by the places that change it (a
    load, a lease, a task, the inference lanes): no timer."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._count = 0

    @property
    def count(self) -> int:
        with self._condition:
            return self._count

    def publish(self) -> None:
        with self._condition:
            self._count += 1
            self._condition.notify_all()

    def wait(self, seen: int, gone: threading.Event) -> None:
        """Until a change after `seen` (a count), or `gone` is set."""
        with self._condition:
            self._condition.wait_for(lambda: self._count != seen or gone.is_set())

    def wake(self) -> None:
        """Wake the waiters without a change (a stream whose client left)."""
        with self._condition:
            self._condition.notify_all()


class EdgeAgent:
    """Owns the model process; applications can only lease it through HTTP."""

    def __init__(self, core_dir: Path = CORE_ROOT) -> None:
        self.core_dir = core_dir.resolve()
        self.model_dir = (
            MODEL_DIR.resolve()
            if self.core_dir == CORE_ROOT.resolve()
            else (self.core_dir / "models").resolve()
        )
        self.lock = threading.RLock()
        self.router = ModelRouter(self.core_dir)
        self.mode = "starting"
        self.owner_pid: int | None = None
        self.owner_dead_since: float | None = None
        self.handoff_until = 0.0
        self.character: dict[str, object] | None = None  # the lease's, to load it again
        self.recover_after = 0.0
        self.error = ""
        self.loading = False
        self.config_path = self.core_dir / "config.json"
        self.config = load_config(self.config_path)
        self.changes = StatusChanges()
        self.inference = InferenceGateway(self.router.port, self.changes.publish)
        self.chat = NativeChat(model_port=CONTROL_PORT, core_dir=self.core_dir)
        self.chat.api_key = self.inference.access.agent_key
        self.terminal = NativeTerminal()
        self.active_model = ""
        self.active_lora = ""
        self.active_character_lora = ""
        self.agent_slot: int | None = None
        self.chat_in_progress = False
        self.active_context_size = 0
        self.capabilities = {"vision": False, "audio": False}
        self.jobs = ModelJobs()

    def update_config(self, value: dict[str, object]) -> dict[str, object]:
        if set(value) & MODEL_SETTINGS:
            raise ValueError("請透過模型切換套用推論設定")
        with self.lock:
            return self._save_config({**self.config, **value})

    def _save_config(self, value: dict[str, object]) -> dict[str, object]:
        self.config = save_config(self.config_path, value)
        self.chat.api_session = apis.for_session(self.config["apis"])
        return dict(self.config)

    def set_api_key(self, api_id: str, key: str) -> dict[str, bool]:
        """Store an API's key (edge_agent.apis); the Agent's next session gets it."""
        with self.lock:
            if api_id not in {api["id"] for api in self.config["apis"]}:
                raise ValueError(f"no registered api {api_id!r}")
            apis.set_key(api_id, key)
            self.chat.api_session = apis.for_session(self.config["apis"])
            return apis.has_key(self.config["apis"])

    def models_status(self) -> dict[str, object]:
        # Capture completion before scanning; a download finishing mid-scan
        # must not report done with a model list from before publication.
        job = dict(self.jobs.current)
        return {
            "models": list_models(self.model_dir),
            "config": dict(self.config),
            "active_model": self.active_model,
            "active_lora": self.active_lora,
            "capabilities": dict(self.capabilities),
            "recording_status": recording_status(
                bool(self.config["record_sessions"]), str(self.config["record_folder"])
            ),
            "context_size": self.active_context_size,
            "max_output_tokens": self.chat.max_output_tokens
            if self.mode == "base" or self.agent_slot is not None
            else None,
            "working_context_size": self.chat.working_context_size
            if self.mode == "base" or self.agent_slot is not None
            else None,
            "job": job,
            "mode": self.mode,
            "loading": self.loading,
            "error": self.error,
        }

    def start_model_job(
        self, action: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return self.jobs.start(
            action,
            payload,
            config=self.config,
            model_dir=self.model_dir,
            switch=self.select_model,
        )

    def select_model(self, payload: dict[str, object]) -> dict[str, object]:
        # Never interrupt an active turn or an application's character lease.
        if not self.lock.acquire(blocking=False):
            raise PermissionError("工作正在進行；完成或停止後再切換模型")
        try:
            if self.chat_in_progress:
                raise PermissionError("工作正在進行；完成或停止後再切換模型")
            if self.mode == "character":
                raise PermissionError("SilentTalk 正在使用模型；結束角色連線後再切換")
            selection = {**self.config, **payload}
            if (
                "model" in payload
                and payload["model"] != self.config["model"]
                and "lora" not in payload
            ):
                selection["lora"] = ""
            if (
                "model" in payload
                and payload["model"] != self.config["model"]
                and "mmproj" not in payload
            ):
                selection["mmproj"] = ""
            candidate = validated_config(selection)
            metadata = model_metadata(model_file(self.model_dir, str(candidate["model"])))
            if metadata["adapter"] or metadata["split"] or metadata["projector"]:
                raise ValueError("請選擇單檔回覆模型，不能選 LoRA 或分片")
            adapter_file(self.model_dir, metadata, str(candidate["lora"]))
            if candidate["mmproj"]:
                projector_file(self.model_dir, str(candidate["mmproj"]))
            limit = metadata["context_length"]
            if isinstance(limit, int) and limit > 0:
                candidate["context_size"] = min(int(candidate["context_size"]), limit)
            if int(candidate["context_size"]) < MIN_AGENT_CONTEXT_SIZE:
                raise ValueError(
                    "EdgeAgent 的 context 至少需要 8192 token，以保留 SDK 安全空間"
                )
            if candidate == self.config and self.status()["ready"]:
                return self.status()
            previous = dict(self.config)
            self.config = candidate
            with self._loading():
                try:
                    self._start_child("base", None, None)
                    self._save_config(candidate)
                except Exception as exc:
                    self.config = previous
                    try:
                        self._start_child("base", None, None)
                    except Exception as restore_exc:  # noqa: BLE001 - preserve both failures
                        self.error = f"切換失敗：{exc}；恢復失敗：{restore_exc}"
                    raise RuntimeError(f"模型切換失敗：{exc}") from exc
            return self.status()
        finally:
            self.lock.release()

    @contextmanager
    def _loading(self, error: str | None = "") -> Iterator[None]:
        """A model load or stop: the status says loading (with `error`; None
        keeps the one it has) until it ends, and the status streams hear both."""
        self.loading = True
        if error is not None:
            self.error = error
        self.changes.publish()
        try:
            yield
        finally:
            self.loading = False
            self.changes.publish()

    def status(self) -> dict[str, object]:
        """What every client reads (GET /status), and what the status stream
        (GET /status/events) sends each time it changes."""
        ready = (
            not self.loading
            and self.router.running()
            and self._model_ready()
        )
        admitted, refused = self.inference.access.character_admission()
        return {
            "service": "edge-agent",
            "mode": self.mode,
            "ready": ready,
            "port": CONTROL_PORT,
            "inference_policy": "two_lanes_voice_first",
            "inference_slots": 2 if self.inference.access.pool else 1,
            "url": f"http://127.0.0.1:{CONTROL_PORT}/",
            "model_url": f"http://127.0.0.1:{CONTROL_PORT}",
            "owner_pid": self.owner_pid,
            "agent_slot": self.agent_slot,
            "agent_busy": self.chat_in_progress,
            "inference_reserved": self.inference.access.agent_reserved,
            # Whether the gateway would take a character request now (the size
            # of the last one it turned away), and how many it turned away.
            "character_admitted": admitted,
            "character_refused": refused,
            "error": self.error,
            "model": self.active_model,
            "lora": self.active_lora,
            "character_lora": self.active_character_lora,
            "context_size": self.active_context_size,
            "capabilities": dict(self.capabilities),
        }

    def _model_ready(self) -> bool:
        if not self.active_model:
            return False
        try:
            return self.router.state().get("value") == "loaded"
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def switch(
        self,
        mode: str,
        owner_pid: int | None = None,
        character: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with self.lock:
            if self.chat_in_progress:
                raise PermissionError("工作正在進行；完成或停止後再切換模型")
            if (
                mode == "character"
                and self.mode == "character"
                and self.owner_pid is not None
                and owner_pid != self.owner_pid
                and pid_alive(self.owner_pid)
                and time.monotonic() >= self.handoff_until
            ):
                raise PermissionError("SilentTalk still owns character mode")
            if (
                mode == "base"
                and self.mode == "character"
                and self.owner_pid is not None
                and pid_alive(self.owner_pid)
                and owner_pid != self.owner_pid
            ):
                raise PermissionError("SilentTalk still owns character mode")
            if (
                mode == self.mode
                and (mode != "character" or character is None)
                and self.router.running()
                and self._model_ready()
            ):
                if mode == "character" and owner_pid is not None:
                    self.owner_pid = owner_pid
                    self.owner_dead_since = None
                    self.handoff_until = 0.0
                    self.changes.publish()
                return self.status()

            with self._loading():
                try:
                    self._start_child(mode, owner_pid, character)
                except Exception as exc:
                    self.error = str(exc)
                    if mode != "base":
                        try:
                            self._start_child("base", None, None)
                        except Exception as base_exc:  # noqa: BLE001 - preserve first failure
                            self.error += f"; base restore failed: {base_exc}"
                    raise
            return self.status()

    def _start_child(
        self,
        mode: str,
        owner_pid: int | None,
        character: dict[str, object] | None,
    ) -> None:
        with self.inference.access.maintenance():
            self._load_child(mode, owner_pid, character)

    def _load_child(
        self,
        mode: str,
        owner_pid: int | None,
        character: dict[str, object] | None,
    ) -> None:
        plan = plan_load(mode, self.config, character, self.model_dir, self.router.port)
        agent_context = plan.allocation.agent
        self.router.load(plan.args)
        self.mode = mode
        self.capabilities = self.router.capabilities(plan.context_size)
        # The full configured window belongs to the active Agent task.
        self.active_model, self.active_context_size = plan.model_path.name, agent_context
        self.active_lora = plan.agent_lora.name if plan.agent_lora else ""
        self.active_character_lora = (
            plan.character_lora.name if plan.character_lora else ""
        )
        self.owner_pid = owner_pid if mode == "character" else None
        self.character = character if mode == "character" else None
        self.owner_dead_since = None
        self.handoff_until = 0.0
        self.chat.configure_context(
            agent_context,
            plan.model_path.name,
            self.capabilities,
            int(self.config["max_output_tokens"]),
            int(self.config["working_context_size"]),
            hardware.describe(int(self.config["gpu_layers"])),
        )
        self.chat.api_session = apis.for_session(self.config["apis"])
        self.chat.lora_id = plan.loras.index(plan.agent_lora) if plan.agent_lora else None
        self.chat.sampling = plan.agent_sampling
        # Two slots in one pool when a character is leased (edge_agent.inference).
        self.inference.configure(self.owner_pid, len(plan.loras), self.chat.lora_id,
                                 plan.allocation.total if plan.allocation.slots > 1 else 0)
        self.agent_slot = self.chat.slot_id = self.inference.slots["agent"]

    def _stop_child(self) -> None:
        self.active_model = ""
        self.active_lora = ""
        self.active_character_lora = ""
        self.active_context_size = 0
        self.agent_slot = None
        self.router.stop()

    def monitor(self) -> None:
        """Once a second: a character lease whose application died goes back
        to base, and a model process that died on its own is loaded again."""
        while True:
            time.sleep(1)
            if self._model_died():
                self._reload_after_crash()
            with self.lock:
                dead = (
                    self.mode == "character"
                    and self.owner_pid is not None
                    and not pid_alive(self.owner_pid)
                )
                if dead and self.owner_dead_since is None:
                    self.owner_dead_since = time.monotonic()
                elif not dead:
                    self.owner_dead_since = None
                stale = (
                    dead
                    and self.owner_dead_since is not None
                    and time.monotonic() - self.owner_dead_since >= 3
                    and time.monotonic() >= self.handoff_until
                )
                owner_pid = self.owner_pid
            if stale:
                try:
                    self.switch("base", owner_pid)
                except (OSError, RuntimeError, ValueError):
                    time.sleep(2)

    def _model_died(self) -> bool:
        """The loaded model's process ended on its own: the router reports it
        failed, or the router itself is gone (2026-10-12: a llama-server
        worker that crashes leaves the router up and the model unloaded, and
        every request is answered "model is not loaded" until it is loaded
        again; the dev box's llama-server b11193 crashed twice so)."""
        with self.lock:
            if self.loading or not self.active_model or time.monotonic() < self.recover_after:
                return False
            if not self.router.running():
                return True
            try:
                return bool(self.router.state().get("failed"))
            except (OSError, ValueError, TypeError, RuntimeError):
                return False

    def _reload_after_crash(self) -> None:
        """The same mode, lease and adapters as before the crash; a failed
        reload is kept in the status and tried again 30 seconds later."""
        with self.lock, self._loading("模型程序意外結束，重新載入中"):
            try:
                self._start_child(self.mode, self.owner_pid, self.character)
                self.error = ""
            except Exception as exc:  # noqa: BLE001 - shown in the status, tried again
                self.error = f"模型程序意外結束，重新載入失敗：{exc}"
                self.recover_after = time.monotonic() + 30

    def hold_handoff(self, seconds: float = 30.0) -> dict[str, object]:
        with self.lock:
            if self.mode == "character":
                self.handoff_until = time.monotonic() + seconds
            return self.status()

    def stop_service(self) -> dict[str, object]:
        with self.lock:
            if (
                self.mode == "character"
                and self.owner_pid is not None
                and pid_alive(self.owner_pid)
            ):
                raise PermissionError("SilentTalk still owns character mode")
            self.chat.close()
            self.terminal.close_all()
            with self._loading(error=None):
                self._stop_child()
                self.mode = "stopped"
                self.owner_pid = None
                self.owner_dead_since = None
                self.handoff_until = 0.0
            return self.status()
