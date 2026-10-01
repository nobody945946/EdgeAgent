from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

GENERAL_CONTEXT_SIZE = 98_304
CONTROL_PORT = 18_087
GENERAL_PORT = 18_089
DEFAULT_MODEL = "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
CORE_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = CORE_ROOT / "models"


class E4BCoreError(RuntimeError):
    """Raised when the local E4B core cannot satisfy a request."""


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
    agent_tools: bool = False,
) -> list[str]:
    """Build the one canonical llama-server command for every core mode."""
    slots = 2 if lora_path is not None else 1
    args = [
        executable,
        "--model",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(context_size * slots),
        "--threads",
        str(threads),
        "--n-gpu-layers",
        str(gpu_layers),
        "--parallel",
        str(slots),
        "--jinja",
    ]
    if lora_path is not None:
        args += ["--lora", str(lora_path)]
    if agent_tools:
        args += [
            "--tools",
            "all",
            "--cors-origins",
            "localhost",
            "--no-ui",
        ]
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
        raise E4BCoreError("llama-server.exe is not installed")
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
