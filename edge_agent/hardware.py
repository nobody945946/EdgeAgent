"""The machine the Agent runs on, in one sentence for its runtime section.

Asked "GPU or CPU?", the Agent probed the machine with PowerShell (2026-10-08:
on the dev box that was the bench sandbox, and the answers said Docker and an
RTX 3090); the host knows these facts, so it states them.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path


def nvidia_smi() -> str | None:
    """nvidia-smi on PATH, or where the Windows driver puts it."""
    command = shutil.which("nvidia-smi")
    if command:
        return command
    candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
    return str(candidate) if candidate.is_file() else None


@cache
def gpu() -> tuple[str, int] | None:
    """The first NVIDIA GPU's name and memory in MiB, or None."""
    command = nvidia_smi()
    if not command:
        return None
    try:
        result = subprocess.run([command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name, _, memory = result.stdout.strip().splitlines()[0].rpartition(",") if result.stdout.strip() else ("", "", "")
    try:
        return (name.strip(), int(memory)) if result.returncode == 0 and name else None
    except ValueError:
        return None


@cache
def ram_mib() -> int | None:
    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total // 2**20
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**20
    except (ValueError, OSError):
        return None


def describe(gpu_layers: int) -> str:
    """e.g. 「NVIDIA GeForce RTX 3050 Ti Laptop GPU（4 GB 顯示記憶體）、8 GB RAM；模型有 41 層放在顯示卡，其餘在 CPU」"""
    found, ram = gpu(), ram_mib()
    parts = [f"{found[0]}（{round(found[1] / 1024)} GB 顯示記憶體）" if found else "沒有 NVIDIA 顯示卡"]
    if ram:
        parts.append(f"{round(ram / 1024)} GB RAM")
    if not found or gpu_layers == 0:
        placement = "模型全部在 CPU 上跑"
    elif gpu_layers < 0:
        placement = "模型的層由 llama.cpp 自動分配到顯示卡和 CPU"
    elif gpu_layers >= 99:
        placement = "模型全部放在顯示卡"
    elif gpu_layers >= 99:
        placement = "模型全部放在顯示卡"
    else:
        placement = f"模型有 {gpu_layers} 層放在顯示卡，其餘在 CPU"
    return "、".join(parts) + "；" + placement
