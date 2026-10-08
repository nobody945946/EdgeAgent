"""The core service's state: which model and mode are loaded, who leases the
model, the Agent's own selection, and the status every client reads."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from edge_agent import hardware
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
        self.error = ""
        self.loading = False
        self.config_path = self.core_dir / "config.json"
        self.config = load_config(self.config_path)
        self.inference = InferenceGateway(self.router.port)
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
        return dict(self.config)

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
            self.loading = True
            self.error = ""
            self.config = candidate
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
            finally:
                self.loading = False
            return self.status()
        finally:
            self.lock.release()

    def status(self) -> dict[str, object]:
        ready = (
            not self.loading
            and self.router.running()
            and self._model_ready()
        )
        return {
            "service": "edge-agent",
            "mode": self.mode,
            "ready": ready,
            "port": CONTROL_PORT,
            "inference_policy": "agent_priority_idle_borrow",
            "inference_slots": 1,
            "url": f"http://127.0.0.1:{CONTROL_PORT}/",
            "model_url": f"http://127.0.0.1:{CONTROL_PORT}",
            "owner_pid": self.owner_pid,
            "agent_slot": self.agent_slot,
            "agent_busy": self.chat_in_progress,
            "inference_reserved": self.inference.access.agent_reserved,
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
                return self.status()

            self.loading = True
            self.error = ""
            try:
                self._start_child(mode, owner_pid, character)
            except Exception as exc:
                self.error = str(exc)
                self.loading = False
                if mode != "base":
                    try:
                        self._start_child("base", None, None)
                    except Exception as base_exc:  # noqa: BLE001 - preserve first failure
                        self.error += f"; base restore failed: {base_exc}"
                raise
            self.loading = False
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
        agent_slot = 0
        self.router.load(plan.args)
        self.mode = mode
        self.capabilities = self.router.capabilities(plan.context_size)
        # The full configured window belongs to the active Agent task.
        self.active_model, self.active_context_size = plan.model_path.name, agent_context
        self.active_lora = plan.agent_lora.name if plan.agent_lora else ""
        self.active_character_lora = (
            plan.character_lora.name if plan.character_lora else ""
        )
        self.agent_slot = agent_slot
        self.owner_pid = owner_pid if mode == "character" else None
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
        self.chat.slot_id = agent_slot
        self.chat.lora_id = plan.loras.index(plan.agent_lora) if plan.agent_lora else None
        self.inference.configure(self.owner_pid, len(plan.loras), self.chat.lora_id)

    def _stop_child(self) -> None:
        self.active_model = ""
        self.active_lora = ""
        self.active_character_lora = ""
        self.active_context_size = 0
        self.agent_slot = None
        self.router.stop()

    def monitor_owner(self) -> None:
        while True:
            time.sleep(1)
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
            self.loading = True
            self._stop_child()
            self.mode = "stopped"
            self.owner_pid = None
            self.owner_dead_since = None
            self.handoff_until = 0.0
            self.loading = False
            return self.status()
