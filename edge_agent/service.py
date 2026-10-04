from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from edge_agent.chat import THINKING_PROFILES, NativeChat, decode_attachments
from edge_agent.deploy import download, ensure_projector
from edge_agent.models import hub_catalog, list_models, model_filename, model_metadata
from edge_agent.permissions import (
    DEFAULT_PERMISSIONS,
    bridge_rules,
    profile_for,
    validate_permissions,
    workspace_allowed,
)
from edge_agent.runtime import (
    CONTROL_PORT,
    CORE_ROOT,
    DEFAULT_AGENT_MODEL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_WORKING_CONTEXT_SIZE,
    GENERAL_CONTEXT_SIZE,
    GENERAL_PORT,
    MIN_AGENT_CONTEXT_SIZE,
    MODEL_DIR,
    build_server_args,
    find_llama_server,
)
from edge_agent.terminal import NativeTerminal
from edge_agent.web import NATIVE_CHAT_HTML

DEFAULT_CONFIG: dict[str, object] = {
    "thinking_effort": "off",
    "agent": True,
    "model": DEFAULT_AGENT_MODEL,
    "lora": "",
    "mmproj": "",
    "context_size": GENERAL_CONTEXT_SIZE,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "working_context_size": DEFAULT_WORKING_CONTEXT_SIZE,
    "gpu_layers": -1,
    "low_vram": True,
    "permissions": DEFAULT_PERMISSIONS,
}


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _listening_pid(port: int) -> int | None:
    """Return the exact Windows PID listening on a reserved core port."""
    if os.name != "nt":
        return None
    listing = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        check=False,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = listing.stdout.decode(errors="replace")
    endpoint = re.compile(rf"^(?:\[.*\]|[^:]+):{port}$")
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) >= 5
            and fields[3].upper() == "LISTENING"
            and endpoint.match(fields[1])
            and fields[-1].isdigit()
        ):
            return int(fields[-1])
    return None


def _process_image(pid: int) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32_768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return Path(buffer.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _clear_reserved_port(port: int) -> None:
    """Recover only an orphaned llama-server on an EdgeAgent-owned model port."""
    pid = _listening_pid(port)
    if pid is None:
        return
    image = _process_image(pid)
    if image is None or image.name.lower() != "llama-server.exe":
        raise RuntimeError(f"reserved EdgeAgent port {port} is occupied by PID {pid}")
    import ctypes

    terminate = 0x0001
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(terminate | synchronize, False, pid)
    if not handle:
        raise RuntimeError(f"cannot recover orphaned llama-server PID {pid}")
    try:
        if not ctypes.windll.kernel32.TerminateProcess(handle, 1):
            raise RuntimeError(f"cannot terminate orphaned llama-server PID {pid}")
        ctypes.windll.kernel32.WaitForSingleObject(handle, 5_000)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    if _listening_pid(port) is not None:
        raise RuntimeError(
            f"orphaned llama-server PID {pid} did not release port {port}"
        )


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
        self.process: subprocess.Popen[bytes] | None = None
        self.mode = "starting"
        self.port = GENERAL_PORT
        self.owner_pid: int | None = None
        self.owner_dead_since: float | None = None
        self.handoff_until = 0.0
        self.error = ""
        self.loading = False
        self.config_path = self.core_dir / "config.json"
        self.config = self._load_config()
        self.chat = NativeChat(model_port=GENERAL_PORT, core_dir=self.core_dir)
        self.terminal = NativeTerminal()
        self.active_model = ""
        self.agent_slot: int | None = None
        self.chat_in_progress = False
        self.active_context_size = 0
        self.capabilities = {"vision": False, "audio": False}
        self.model_job_lock = threading.Lock()
        self.model_job: dict[str, object] = {"state": "idle"}

    def _load_config(self) -> dict[str, object]:
        if not self.config_path.is_file():
            return dict(DEFAULT_CONFIG)
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return dict(DEFAULT_CONFIG)
            # Older releases allowed a window fully consumed by Pi's 4096-token
            # safety reserve. Upgrade that one legacy field without resetting
            # the selected model, adapter or other preferences.
            context = value.get("context_size")
            if type(context) is int and 4096 <= context < MIN_AGENT_CONTEXT_SIZE:
                value["context_size"] = MIN_AGENT_CONTEXT_SIZE
            return self._validated_config(value)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return dict(DEFAULT_CONFIG)

    @staticmethod
    def _validated_config(value: dict[str, object]) -> dict[str, object]:
        unknown = set(value) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"unknown config fields: {', '.join(sorted(unknown))}")
        merged = {**DEFAULT_CONFIG, **value}
        effort = merged["thinking_effort"]
        if not isinstance(effort, str) or effort not in THINKING_PROFILES:
            raise ValueError("invalid thinking_effort")
        agent = merged["agent"]
        if not isinstance(agent, bool):
            raise TypeError("agent must be a boolean")
        model_filename(merged["model"])
        if not isinstance(merged["lora"], str):
            raise TypeError("lora must be a filename or an empty string")
        if merged["lora"]:
            model_filename(merged["lora"])
        if not isinstance(merged["mmproj"], str):
            raise TypeError("mmproj must be a filename or an empty string")
        if merged["mmproj"]:
            model_filename(merged["mmproj"])
        EdgeAgent._bounded_int(merged, "context_size", MIN_AGENT_CONTEXT_SIZE, 262144)
        EdgeAgent._bounded_int(merged, "max_output_tokens", 512, 131072)
        EdgeAgent._bounded_int(merged, "working_context_size", 8192, 262144)
        EdgeAgent._bounded_int(merged, "gpu_layers", -1, 999)
        if not isinstance(merged["low_vram"], bool):
            raise TypeError("low_vram must be a boolean")
        return {
            **merged,
            "permissions": validate_permissions(merged["permissions"]),
        }

    def update_config(self, value: dict[str, object]) -> dict[str, object]:
        if set(value) & {
            "model",
            "lora",
            "mmproj",
            "context_size",
            "max_output_tokens",
            "working_context_size",
            "gpu_layers",
            "low_vram",
        }:
            raise ValueError("請透過模型切換套用推論設定")
        with self.lock:
            return self._save_config({**self.config, **value})

    def _save_config(self, value: dict[str, object]) -> dict[str, object]:
        config = self._validated_config(value)
        temporary = self.config_path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.config_path)
        self.config = config
        return dict(config)

    def models_status(self) -> dict[str, object]:
        # Capture completion before scanning; a download finishing mid-scan
        # must not report done with a model list from before publication.
        job = dict(self.model_job)
        return {
            "models": list_models(self.model_dir),
            "config": dict(self.config),
            "active_model": self.active_model,
            "capabilities": dict(self.capabilities),
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
        if not self.model_job_lock.acquire(blocking=False):
            raise PermissionError("模型下載或切換正在進行")
        try:
            if action == "switch":
                model_filename(payload.get("model"))
                if set(payload) - {
                    "model",
                    "lora",
                    "mmproj",
                    "context_size",
                    "max_output_tokens",
                    "working_context_size",
                    "gpu_layers",
                    "low_vram",
                }:
                    raise ValueError("unknown model settings")
                self._validated_config({**self.config, **payload})
            elif action == "download":
                model_filename(Path(self._required_string(payload, "filename")).name)
                self._required_string(payload, "repository")
            else:
                raise ValueError("unknown model action")
            self.model_job = {
                "id": uuid4().hex,
                "state": "running",
                "action": action,
                "received": 0,
                "total": 0,
                "error": "",
            }
            threading.Thread(
                target=self._run_model_job,
                args=(action, dict(payload)),
                name="model-" + action,
                daemon=True,
            ).start()
            return dict(self.model_job)
        except Exception:
            self.model_job_lock.release()
            raise

    def _run_model_job(self, action: str, payload: dict[str, object]) -> None:
        try:
            if action == "download":
                catalog = hub_catalog(payload["repository"])
                entry = next(
                    (
                        entry
                        for entry in catalog["files"]
                        if entry["filename"] == payload["filename"]
                    ),
                    None,
                )
                if entry is None:
                    raise ValueError("找不到可驗證的單檔 GGUF")
                name = model_filename(Path(str(entry["filename"])).name)
                if entry.get("projector"):
                    repository = str(payload["repository"]).split("/")[-1]
                    name = "mmproj-" + repository + "-" + name.removeprefix("mmproj-")
                target = self.model_dir / name
                if target.exists():
                    raise ValueError("此檔名已存在；請直接選擇已下載的模型")

                def progress(received: int, total: int) -> None:
                    self.model_job = {
                        **self.model_job,
                        "received": received,
                        "total": total,
                        "filename": name,
                    }

                download(
                    str(entry["url"]), target, str(entry["sha256"]), progress=progress
                )
                metadata = model_metadata(target)
                if metadata["adapter"] or metadata["split"]:
                    raise ValueError(
                        "下載完成，但此檔是配件或分片，不能作為獨立回覆模型"
                    )
            else:
                self.select_model(payload)
            self.model_job = {**self.model_job, "state": "done"}
        except Exception as exc:  # noqa: BLE001 - report background failures to UI
            self.model_job = {**self.model_job, "state": "failed", "error": str(exc)}
        finally:
            self.model_job_lock.release()

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
            candidate = self._validated_config(selection)
            metadata = model_metadata(self._model_path(str(candidate["model"])))
            if metadata["adapter"] or metadata["split"] or metadata["projector"]:
                raise ValueError("請選擇單檔回覆模型，不能選 LoRA 或分片")
            self._adapter_path(metadata, str(candidate["lora"]))
            if candidate["mmproj"]:
                self._projector_path(str(candidate["mmproj"]))
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

    def list_directories(self, value: object = None) -> dict[str, object]:
        if value is None or value == "":
            value = str(Path.home())
        if not isinstance(value, str):
            raise TypeError("path must be a string")
        current = Path(value).expanduser().resolve()
        if not current.is_dir():
            raise ValueError(f"directory does not exist: {current}")
        try:
            directories = sorted(
                (
                    {"name": child.name, "path": str(child.resolve())}
                    for child in current.iterdir()
                    if child.is_dir()
                ),
                key=lambda item: str(item["name"]).casefold(),
            )
        except OSError as exc:
            raise ValueError(f"cannot read directory: {current}") from exc
        parent = current.parent
        return {
            "current": str(current),
            "parent": str(parent) if parent != current else None,
            "directories": directories,
        }

    def workspace_info(self, directory: str) -> dict[str, object]:
        path = Path(directory).resolve()
        if not path.is_dir():
            raise ValueError("workspace directory does not exist")
        branch = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return {
            "path": str(path),
            "git": branch.returncode == 0,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
        }

    def create_worktree(self, directory: str) -> dict[str, object]:
        info = self.workspace_info(directory)
        if not info["git"]:
            raise ValueError("open a Git project before creating a worktree")
        token = uuid4().hex[:12]
        branch = "edge-agent/" + token
        destination = self.core_dir / ".worktrees" / token
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "git",
                "-C",
                str(info["path"]),
                "worktree",
                "add",
                "-b",
                branch,
                str(destination),
                "HEAD",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise ValueError("worktree creation failed: " + result.stderr.strip())
        return {"path": str(destination), "branch": branch, "git": True}

    def status(self) -> dict[str, object]:
        process = self.process
        ready = (
            not self.loading
            and process is not None
            and process.poll() is None
            and self._model_ready()
        )
        return {
            "service": "edge-agent",
            "mode": self.mode,
            "ready": ready,
            "port": self.port,
            "url": f"http://127.0.0.1:{CONTROL_PORT}/",
            "model_url": f"http://127.0.0.1:{self.port}",
            "owner_pid": self.owner_pid,
            "agent_slot": self.agent_slot,
            "agent_busy": self.chat_in_progress,
            "error": self.error,
            "model": self.active_model,
            "context_size": self.active_context_size,
            "capabilities": dict(self.capabilities),
        }

    def _model_ready(self) -> bool:
        if not self.active_model:
            return False
        try:
            return self._model_state().get("value") == "loaded"
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def _model_state(self) -> dict[str, object]:
        models = self._model_request("/models", timeout=2).get("data", [])
        return next(
            (item.get("status", {}) for item in models if item.get("id") == "local"), {}
        )

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
                and _pid_alive(self.owner_pid)
                and time.monotonic() >= self.handoff_until
            ):
                raise PermissionError("SilentTalk still owns character mode")
            if (
                mode == "base"
                and self.mode == "character"
                and self.owner_pid is not None
                and _pid_alive(self.owner_pid)
                and owner_pid != self.owner_pid
            ):
                raise PermissionError("SilentTalk still owns character mode")
            if (
                mode == self.mode
                and (mode != "character" or character is None)
                and self.process is not None
                and self.process.poll() is None
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
        if mode == "base":
            model_path = self._model_path(str(self.config["model"]))
            metadata = model_metadata(model_path)
            if metadata["adapter"] or metadata["split"] or metadata["projector"]:
                raise ValueError("selected file is not a standalone model")
            lora_path = self._adapter_path(metadata, str(self.config["lora"]))
            context_size = int(self.config["context_size"])
            limit = metadata["context_length"]
            if isinstance(limit, int) and limit > 0:
                context_size = min(context_size, limit)
            if context_size < MIN_AGENT_CONTEXT_SIZE:
                raise ValueError("此模型的 context 少於 8192，無法支援 EdgeAgent")
            port = GENERAL_PORT
            gpu_layers = int(self.config["gpu_layers"])
            low_vram = bool(self.config["low_vram"])
            projector_path = (
                self._projector_path(str(self.config["mmproj"]))
                if self.config["mmproj"]
                else ensure_projector(model_path, str(metadata["architecture"]))
            )
        elif mode == "character":
            if character is None:
                raise ValueError("character configuration required")
            model_path = self._model_path(self._required_string(character, "model"))
            lora_name = self._required_string(character, "lora")
            lora_path = self._adapter_path(model_metadata(model_path), lora_name)
            context_size = self._bounded_int(character, "context_size", 1_024, 32_768)
            port = self._bounded_int(character, "port", 1_024, 65_535)
            if port in (CONTROL_PORT, GENERAL_PORT):
                raise ValueError("character port conflicts with EdgeAgent")
            gpu_layers = self._bounded_int(character, "gpu_layers", 0, 999)
            low_vram = character.get("low_vram") is True
            # Character leases continue to use their existing text-only contract.
            projector_path = None
        else:
            raise ValueError(f"unknown EdgeAgent mode: {mode}")

        shared = (
            mode == "character"
            and model_path.name == self.config["model"]
            and (lora_path.name if lora_path else "") == self.config["lora"]
        )
        role_slots = 2 if mode == "character" and lora_path else 1
        agent_slot = role_slots if shared else (0 if mode == "base" else None)
        if shared:
            context_size = max(context_size, int(self.config["context_size"]))
        # The upstream router owns model processes, loading and hot replacement.
        # Keep one public inference endpoint for the Agent and leased clients.
        args = build_server_args(
            find_llama_server(),
            model_path,
            port=GENERAL_PORT,
            context_size=context_size,
            threads=max(2, min(6, (os.cpu_count() or 4) // 2)),
            gpu_layers=gpu_layers,
            low_vram=low_vram,
            lora_path=lora_path,
            projector_path=projector_path,
            slots=role_slots + (1 if shared else 0),
            prompt_cache_mib=256 if mode == "base" else None,
        )
        preset = self.core_dir / ".runtime/models.ini"
        preset.parent.mkdir(parents=True, exist_ok=True)
        lines = ["version = 1", "", "[local]"]
        index = 1
        while index < len(args):
            key = args[index].removeprefix("--")
            index += 1
            value = "true"
            if index < len(args) and not args[index].startswith("--"):
                value = args[index].replace("\\", "/")
                index += 1
            if key not in {"host", "port"}:
                lines.append(f"{key} = {value}")
        temporary = preset.with_suffix(".ini.part")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(preset)
        if self.process is None or self.process.poll() is not None:
            _clear_reserved_port(GENERAL_PORT)
            self.process = subprocess.Popen(
                [
                    find_llama_server(),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(GENERAL_PORT),
                    "--models-preset",
                    str(preset),
                    "--models-max",
                    "1",
                    "--no-models-autoload",
                ],
                cwd=self.core_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.monotonic() + 30
            while not _health_ok(GENERAL_PORT):
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"model router exited with {self.process.returncode}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("model router did not become ready")
                time.sleep(0.1)
        self._model_request("/models?reload=1", timeout=60)
        if self._model_state().get("value") not in {"loaded", "loading"}:
            result = self._model_request(
                "/models/load", {"model": "local"}, timeout=300
            )
            if result.get("success") is not True:
                raise RuntimeError("model router rejected loading the selected model")
        deadline = time.monotonic() + 300
        while True:
            state = self._model_state()
            if state.get("failed"):
                raise RuntimeError(
                    f"model failed to load (exit {state.get('exit_code')})"
                )
            if state.get("value") == "loaded":
                break
            if self.process.poll() is not None:
                raise RuntimeError("model router exited during loading")
            if time.monotonic() >= deadline:
                raise TimeoutError("selected model did not become ready")
            time.sleep(0.1)
        self.mode, self.port = mode, GENERAL_PORT
        properties = self._model_request("/props?model=local")
        if mode == "base" or shared:
            actual_context = properties.get("default_generation_settings", {}).get(
                "n_ctx"
            )
            if actual_context != context_size:
                raise RuntimeError(
                    f"引擎實際 context {actual_context} 與要求的 {context_size} 不符"
                )
        modalities = properties.get("modalities", {})
        self.capabilities = {
            "vision": isinstance(modalities, dict) and modalities.get("vision") is True,
            "audio": isinstance(modalities, dict) and modalities.get("audio") is True,
        }
        self.active_model, self.active_context_size = model_path.name, context_size
        self.agent_slot = agent_slot
        self.owner_pid = owner_pid if mode == "character" else None
        self.owner_dead_since = None
        self.handoff_until = 0.0
        if mode == "base" or shared:
            self.chat.configure_context(
                context_size,
                model_path.name,
                self.capabilities,
                int(self.config["max_output_tokens"]),
                int(self.config["working_context_size"]),
            )
            self.chat.slot_id = agent_slot
            self.chat.lora_enabled = lora_path is not None

    def _projector_path(self, filename: str) -> Path:
        path = self._model_path(filename)
        metadata = model_metadata(path)
        if not metadata["projector"] or metadata["split"]:
            raise ValueError("多模態配件必須是單檔 projector GGUF")
        return path

    @staticmethod
    def _model_request(
        endpoint: str, payload: dict[str, object] | None = None, *, timeout: float = 5
    ) -> dict[str, object]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{GENERAL_PORT}" + endpoint,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc
        if not isinstance(result, dict):
            raise TypeError("invalid model router response")
        return result

    def _adapter_path(self, base: dict[str, object], filename: str) -> Path | None:
        if not filename:
            return None
        path = self._model_path(filename)
        adapter = model_metadata(path)
        if not adapter["adapter"] or adapter["split"]:
            raise ValueError("selected LoRA must be a single adapter GGUF")
        if adapter["architecture"] != base["architecture"]:
            raise ValueError("LoRA architecture does not match the selected base model")
        return path

    def _model_path(self, filename: str) -> Path:
        model_filename(filename)
        path = (self.model_dir / filename).resolve()
        if path.parent != self.model_dir or not path.is_file():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _required_string(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        return value

    @staticmethod
    def _bounded_int(
        payload: dict[str, object], key: str, minimum: int, maximum: int
    ) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{key} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value

    def _stop_child(self) -> None:
        process = self.process
        self.process = None
        self.active_model = ""
        self.active_context_size = 0
        self.agent_slot = None
        if process is None or process.poll() is not None:
            return
        try:
            self._model_request("/models/unload", {"model": "local"}, timeout=15)
        except (OSError, RuntimeError, TypeError):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def monitor_owner(self) -> None:
        while True:
            time.sleep(1)
            with self.lock:
                dead = (
                    self.mode == "character"
                    and self.owner_pid is not None
                    and not _pid_alive(self.owner_pid)
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
        self.chat.cancel_all()
        with self.lock:
            if (
                self.mode == "character"
                and self.owner_pid is not None
                and _pid_alive(self.owner_pid)
            ):
                raise PermissionError("SilentTalk still owns character mode")
            self.chat.close()
            self.terminal.close_all()
            self.loading = True
            self._stop_child()
            self.mode = "stopped"
            self.port = GENERAL_PORT
            self.owner_pid = None
            self.owner_dead_since = None
            self.handoff_until = 0.0
            self.loading = False
            return self.status()


class Handler(BaseHTTPRequestHandler):
    core: EdgeAgent

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json(HTTPStatus.OK, self.core.status())
        elif self.path == "/config":
            self._json(HTTPStatus.OK, self.core.config)
        elif self.path == "/models":
            self._json(HTTPStatus.OK, self.core.models_status())
        elif self.path in ("/", "/index.html"):
            self._html(NATIVE_CHAT_HTML)
        elif self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        origin = self.headers.get("Origin")
        allowed_origins = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        if (origin is not None and origin not in allowed_origins) or self.headers.get(
            "Sec-Fetch-Site"
        ) == "cross-site":
            self._json(
                HTTPStatus.FORBIDDEN, {"error": "cross-origin actions are not allowed"}
            )
            return
        try:
            payload = self._payload()
            if self.path == "/models/catalog":
                self._json(HTTPStatus.OK, hub_catalog(payload.get("repository")))
                return
            if self.path in {"/models/download", "/models/switch"}:
                result = self.core.start_model_job(
                    self.path.rsplit("/", 1)[-1], payload
                )
                self._json(HTTPStatus.ACCEPTED, result)
                return
            if self.path == "/chat":
                self._chat(payload)
                return
            if self.path == "/terminal":
                self._terminal(payload)
                return
            if self.path == "/terminal/close":
                self.core.terminal.close(self._session_id(payload))
                self._json(HTTPStatus.OK, {"closed": True})
                return
            if self.path == "/chat/reset":
                session_id = self._session_id(payload)
                self.core.chat.reset(session_id)
                self.core.terminal.close(session_id)
                self._json(HTTPStatus.OK, {"reset": True})
                return
            if self.path == "/chat/cancel":
                session_id = self._session_id(payload)
                self.core.chat.cancel(session_id)
                self._json(HTTPStatus.OK, {"cancelled": True})
                return
            if self.path == "/chat/approve":
                session_id = self._session_id(payload)
                call_id = payload.get("call_id")
                approved = payload.get("approved")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("valid call_id required")
                if not isinstance(approved, bool):
                    raise TypeError("approved must be a boolean")
                scope = payload.get("scope", "once")
                if scope not in {"once", "turn"}:
                    raise ValueError("invalid approval scope")
                accepted = self.core.chat.resolve_approval(
                    session_id, call_id, approved, str(scope)
                )
                self._json(HTTPStatus.OK, {"accepted": accepted})
                return
            if self.path == "/config":
                result = self.core.update_config(payload)
                self._json(HTTPStatus.OK, result)
                return
            if self.path == "/directories":
                result = self.core.list_directories(payload.get("path"))
                self._json(HTTPStatus.OK, result)
                return
            if self.path in {"/workspace/info", "/workspace/worktree"}:
                directory = payload.get("path")
                if not isinstance(directory, str) or not directory:
                    raise ValueError("workspace path required")
                result = (
                    self.core.workspace_info(directory)
                    if self.path == "/workspace/info"
                    else self.core.create_worktree(directory)
                )
                self._json(HTTPStatus.OK, result)
                return
            stop_control_server = False
            owner = payload.get("owner_pid")
            owner_pid = (
                owner
                if isinstance(owner, int) and not isinstance(owner, bool)
                else None
            )
            if self.path == "/character":
                if owner_pid is None or not _pid_alive(owner_pid):
                    raise ValueError("live owner_pid required")
                character = payload.get("character")
                if not isinstance(character, dict):
                    raise ValueError("character configuration required")
                result = self.core.switch("character", owner_pid, character)
            elif self.path == "/handoff":
                result = self.core.hold_handoff()
            elif self.path == "/base":
                result = self.core.switch("base", owner_pid)
            elif self.path == "/shutdown":
                result = self.core.stop_service()
                stop_control_server = True
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except PermissionError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc), "ready": False})
            return
        except (TypeError, ValueError, FileNotFoundError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "ready": False})
            return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary must return JSON
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc), "ready": False},
            )
            return
        self._json(HTTPStatus.OK, result)
        if stop_control_server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _terminal(self, payload: dict[str, object]) -> None:
        session_id = self._session_id(payload)
        command = payload.get("command")
        directory = payload.get("working_dir")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("non-empty terminal command required")
        if len(command) > 250_000:
            raise ValueError("terminal command is too long")
        if not isinstance(directory, str) or not directory:
            raise ValueError("open a workspace before using terminal")
        terminal = self.core.terminal.session(session_id, Path(directory))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for event, data in terminal.run(command):
                self._event(event, data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.core.terminal.close(session_id)
        except (OSError, ValueError) as exc:
            self._event("error", {"message": str(exc)})

    def _chat(self, payload: dict[str, object]) -> None:
        session_id = self._session_id(payload)
        text = payload.get("text")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if len(text) > 250_000:
            raise ValueError("message is too long")
        context, media = decode_attachments(
            payload.get("attachments", []), self.core.capabilities
        )
        if not text.strip() and not context and not media:
            raise ValueError("text or attachments required")
        text = text.strip() or "分析附件"
        if context:
            text += "\n\n" + context
        if len(text) > 250_000:
            raise ValueError("message and attachments are too long")
        thinking_effort = payload.get(
            "thinking_effort", self.core.config["thinking_effort"]
        )
        if not isinstance(thinking_effort, str):
            raise TypeError("thinking_effort must be a string")
        agent = payload.get("agent", self.core.config["agent"])
        if not isinstance(agent, bool):
            raise TypeError("agent must be a boolean")
        plan = payload.get("plan", False)
        if not isinstance(plan, bool):
            raise TypeError("plan must be a boolean")
        spec = payload.get("spec", False)
        if not isinstance(spec, bool):
            raise TypeError("spec must be a boolean")
        profile = profile_for(
            self.core.config["permissions"], payload.get("client", "ui")
        )
        working_dir_value = payload.get("working_dir")
        if working_dir_value is None or working_dir_value == "":
            if agent:
                raise ValueError("open a workspace folder before using Agent")
            working_dir = Path.home()
        elif isinstance(working_dir_value, str):
            working_dir = Path(working_dir_value).expanduser()
        else:
            raise TypeError("working_dir must be a string")
        if agent and not workspace_allowed(profile, working_dir):
            raise PermissionError("此資料夾不在權限設定允許的工作區內")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            with self.core.lock:
                status = self.core.status()
                if status["ready"] is not True or self.core.agent_slot is None:
                    raise RuntimeError("EdgeAgent is currently leased by SilentTalk")
                if self.core.chat_in_progress:
                    raise PermissionError("Agent 工作正在進行；完成或停止後再派工")
                for item in media:
                    capability = (
                        "audio"
                        if str(item["mimeType"]).startswith("audio/")
                        else "vision"
                    )
                    if not self.core.capabilities[capability]:
                        raise ValueError("目前模型不支援此附件，請更換模型")
                self.core.chat_in_progress = True
            try:
                for event, data in self.core.chat.stream_reply(
                    session_id,
                    text.strip(),
                    thinking_effort=thinking_effort,
                    agent=agent,
                    working_dir=working_dir,
                    plan=plan,
                    spec=spec,
                    media=media,
                    permissions=bridge_rules(profile),
                ):
                    self._event(event, data)
            finally:
                with self.core.lock:
                    self.core.chat_in_progress = False
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.core.chat.cancel(session_id)
        except Exception as exc:  # noqa: BLE001 - stream errors must reach the browser
            self._event("error", {"message": str(exc)})

    def _event(self, event: str, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        body = f"event: {event}\ndata: {data}\n\n".encode()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise ConnectionAbortedError("chat client disconnected") from None

    def _payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 28_000_000:
            raise ValueError("request is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise TypeError("JSON object required")
        return value

    @staticmethod
    def _session_id(payload: dict[str, object]) -> str:
        value = payload.get("session_id")
        if not isinstance(value, str) or not 8 <= len(value) <= 100:
            raise ValueError("valid session_id required")
        return value

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, value: str) -> None:
        body = value.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main() -> int:
    from edge_agent.runtime import activate_deployment_tools

    activate_deployment_tools()
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-dir", type=Path, default=CORE_ROOT)
    args = parser.parse_args()
    core = EdgeAgent(args.core_dir.resolve())
    Handler.core = core
    server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
    threading.Thread(
        target=core.monitor_owner, name="edge-agent-owner", daemon=True
    ).start()
    threading.Thread(
        target=lambda: core.switch("base"), name="edge-agent-bootstrap", daemon=True
    ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        core._stop_child()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
