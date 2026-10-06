from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# E4B's own limit. On the laptop (4 GB GPU, 8 GB RAM) --fit keeps it by
# putting some layers on the CPU: slower, accepted by the owner (2026-10-05).
GENERAL_CONTEXT_SIZE = 131_072
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
# The history target before compaction: the whole window unless the owner
# trades history for faster prompt processing.
DEFAULT_WORKING_CONTEXT_SIZE = GENERAL_CONTEXT_SIZE
MIN_AGENT_CONTEXT_SIZE = 8_192
CONTROL_PORT = 18_087
GENERAL_PORT = 18_089
# The machine spirit's base: the character and the Agent are one model.
DEFAULT_MODEL = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
# Also shipped, for the owner to choose (writes code better, measured).
QWEN_MODEL = "Qwen3.5-4B-Q4_K_M.gguf"
# The adapter for DEFAULT_MODEL, one for both of its clients: the Agent's tool
# work (the 27B teacher's checked runs inside this framework, bench/) and the
# machine spirit's lines and chat-work questions (SilentTalk's character data).
# It ships in edge_agent/adapters and deploy copies it into models/.
AGENT_ADAPTER = "machine-spirit-v1.q8_0.gguf"
# Saved prompt states kept in RAM, and checkpoints per slot (sliding-window
# and recurrent models need them to reuse a prompt). Upstream defaults are
# 8 GiB and 32: more than the laptop's whole RAM.
PROMPT_CACHE_MIB = 256
CONTEXT_CHECKPOINTS = 2
NODE_VERSION = "22.23.3"
CORE_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = CORE_ROOT / "models"


class EdgeAgentError(RuntimeError):
    """Raised when the local EdgeAgent cannot satisfy a request."""


def activate_deployment_tools(root: Path = CORE_ROOT) -> None:
    paths = [root / ".runtime/tools", root / ".runtime/git/cmd"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.append(Path(local) / "EdgeAgent/GitHubCLI/2.102.0/bin")
    existing = os.environ.get("PATH", "").split(os.pathsep)
    additions = [
        str(path) for path in paths if path.is_dir() and str(path) not in existing
    ]
    os.environ["PATH"] = os.pathsep.join([*additions, *existing])


def build_server_args(
    executable: str,
    model_path: Path,
    *,
    port: int,
    context_size: int,
    threads: int,
    gpu_layers: int,
    low_vram: bool,
    lora_path: Path | None = None,
    projector_path: Path | None = None,
    slots: int = 1,
) -> list[str]:
    """Build the one canonical llama-server command for every core mode.

    `context_size` is one pool all slots share (the character's and the
    Agent's turns take what they need from it). Request locked model pages;
    the OS can still reject locking when physical memory is insufficient."""
    if slots not in (1, 2, 3):
        raise ValueError("slots must be 1, 2 or 3")
    args = [
        executable,
        "--model",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(context_size),
        "--threads",
        str(threads),
        "--n-gpu-layers",
        "auto" if gpu_layers == -1 else str(gpu_layers),
        "--parallel",
        str(slots),
        "--jinja",
        "--load-mode",
        "mlock",
        "--cache-ram",
        str(PROMPT_CACHE_MIB),
        "--ctx-checkpoints",
        str(CONTEXT_CHECKPOINTS),
    ]
    if slots > 1:
        args += ["--kv-unified"]
    if gpu_layers == -1:
        # Upstream owns device-memory fitting. Explicit context plus fit-ctx
        # preserves the chosen window while weights can move to CPU.
        args += ["--fit", "on", "--fit-ctx", str(context_size), "--fit-target", "512"]
    else:
        # Explicit GPU layers own the split; remaining layers run on CPU.
        # Do not allow upstream's default fitter to alter a manual placement.
        args += ["--fit", "off"]
    if lora_path is not None:
        args += ["--lora", str(lora_path)]
    if projector_path is not None:
        args += ["--mmproj", str(projector_path)]
        if low_vram:
            args += ["--no-mmproj-offload"]
    if low_vram:
        args += [
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q8_0",
            "--cache-type-v",
            "q8_0",
        ]
    return args


def find_llama_server() -> str:
    marker = CORE_ROOT / ".runtime/deployment.json"
    if marker.is_file():
        try:
            relative = json.loads(marker.read_text(encoding="utf-8"))["server"]
            executable = (CORE_ROOT / relative).resolve()
            if (
                executable.is_relative_to((CORE_ROOT / ".runtime").resolve())
                and executable.is_file()
            ):
                return str(executable)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    executable = (
        shutil.which("llama-server.exe")
        or shutil.which("llama-server")
        or _winget_llama_server()
    )
    if not executable:
        raise EdgeAgentError("llama-server.exe is not installed")
    return executable


def _winget_llama_server() -> str | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    found = sorted(packages.glob("ggml.llamacpp*/**/llama-server.exe"))
    return str(found[-1]) if found else None


def list_llama_devices(executable: str) -> list[str]:
    try:
        listing = subprocess.run(
            [executable, "--list-devices"],
            capture_output=True,
            check=False,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    output = (listing.stdout + listing.stderr).decode("utf-8", errors="replace")
    return [
        line.strip() for line in output.splitlines() if "MiB" in line and ":" in line
    ]
