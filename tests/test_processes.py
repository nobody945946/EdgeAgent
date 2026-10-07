"""Exact-PID process lookups (processes.py): a reserved port is never
cleared by killing an unrelated process."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge_agent import processes


def test_reserved_port_never_kills_an_unrelated_process(monkeypatch):
    monkeypatch.setattr(processes, "listening_pid", lambda port: 321)
    monkeypatch.setattr(
        processes, "_process_image", lambda pid: Path("C:/Windows/other.exe")
    )
    with pytest.raises(RuntimeError, match="occupied by PID 321"):
        processes.clear_reserved_port(18089)


def test_listener_lookup_ignores_time_wait_pid_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = SimpleNamespace(
        stdout=b"  TCP    127.0.0.1:18089    127.0.0.1:65030    TIME_WAIT    0\r\n"
    )
    monkeypatch.setattr(processes.subprocess, "run", lambda *args, **kwargs: listing)

    assert processes.listening_pid(18089) is None


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
    monkeypatch.setattr(processes.subprocess, "run", lambda *args, **kwargs: listing)

    assert processes.listening_pid(18089) == 222
