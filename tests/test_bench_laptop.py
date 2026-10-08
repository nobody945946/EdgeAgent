"""bench/laptop.py and bench/windows: the sandbox answers Windows' own
commands with the laptop the product runs on."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import laptop


def test_each_task_has_its_own_laptop_with_the_gpu_and_ram_the_runtime_section_states() -> None:
    one, again, other = laptop.machine("train-F-019"), laptop.machine("train-F-019"), laptop.machine("train-A-004")
    assert one == again and one != other
    for machine in (one, other):
        assert machine["gpu"]["name"] in laptop.HARDWARE and machine["gpu"]["mib"] == 4096
        assert round(machine["memory"]["total_kb"] / 2**20) == laptop.RAM_GB
        assert machine["gpu"]["used_mib"] < machine["gpu"]["mib"]
        assert machine["disk"]["c_free"] < machine["disk"]["c_bytes"] < machine["disk"]["bytes"]
    assert laptop.HARDWARE.startswith("Windows、")


@pytest.fixture
def facts(tmp_path: Path, monkeypatch) -> dict:
    machine = laptop.machine("train-F-019")
    path = tmp_path / "machine.json"
    path.write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("LAPTOP_FACTS", str(path))
    return machine


def script(name: str, *args: str) -> str:
    # The scripts run in the sandbox's UTF-8 Linux; -X utf8 gives them that stdout on Windows CI too.
    return subprocess.run([sys.executable, "-X", "utf8", str(BENCH / "windows/bin" / name), *args], capture_output=True,
                          text=True, encoding="utf-8", check=True, env=os.environ).stdout


def test_nvidia_smi_systeminfo_and_hostname_answer_with_the_laptop(facts: dict) -> None:
    csv = script("nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv").splitlines()
    assert csv == ["name, memory.total [MiB], memory.used [MiB]",
                   f"{laptop.GPU}, 4096 MiB, {facts['gpu']['used_mib']} MiB"]
    assert script("nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits").strip() == \
        str(facts["gpu"]["used_mib"])
    assert "llama-server.exe" in script("nvidia-smi")
    info = script("systeminfo")
    assert f"主機名稱:                  {facts['computer']}" in info and facts["os"]["caption"] in info
    assert script("hostname").strip() == facts["computer"]


def test_the_runner_dresses_the_shell_as_the_laptop_and_hides_the_spec(tmp_path: Path, monkeypatch) -> None:
    import agent_runner

    machine = laptop.machine("train-F-019")
    monkeypatch.setattr(agent_runner, "FACTS", tmp_path / ".laptop/machine.json")
    for name in ("PSModulePath", "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "OS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    agent_runner.windows(machine)
    assert json.loads((tmp_path / ".laptop/machine.json").read_text(encoding="utf-8")) == machine
    assert os.environ["PATH"].split(os.pathsep)[0] == "/opt/bench/windows/bin"
    assert os.environ["PSModulePath"] == "/opt/bench/windows"
    assert os.environ["COMPUTERNAME"] == machine["computer"] and os.environ["OS"] == "Windows_NT"
    assert os.environ["NUMBER_OF_PROCESSORS"] == str(machine["cpu"]["threads"])


IMAGE = "edgeagent-bench:1"


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(
    ["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode != 0,
    reason="needs the bench image (bench/run.py --build)")
def test_the_sandbox_powershell_answers_windows_commands_with_the_laptop(tmp_path: Path) -> None:
    machine = laptop.machine("train-F-019")
    (tmp_path / "machine.json").write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
    commands = """
(Get-CimInstance Win32_VideoController).Name -join '|'
(Get-CimInstance -ClassName Win32_Processor).NumberOfLogicalProcessors
(Get-CimInstance -Query "SELECT * FROM Win32_LogicalDisk WHERE DeviceID = 'C:'").FreeSpace
@(Get-CimInstance Win32_Process -Filter "Name LIKE 'llama%'").Count
(Get-PhysicalDisk).BusType
try { Get-CimInstance Win32_Nope -ErrorAction Stop } catch { $_.Exception.Message.Trim() }
[bool](Get-Command Get-WmiObject -ErrorAction SilentlyContinue)
"""
    (tmp_path / "try.ps1").write_text(commands, encoding="utf-8")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec",
         "--user", f"{os.getuid()}:{os.getgid()}",
         "-e", "HOME=/tmp", "-e", "PSModulePath=/opt/bench/windows", "-e", "LAPTOP_FACTS=/facts/machine.json",
         "-v", f"{BENCH / 'windows'}:/opt/bench/windows:ro", "-v", f"{tmp_path}:/facts:ro",
         IMAGE, "pwsh", "-NoProfile", "-NonInteractive", "-File", "/facts/try.ps1"],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=120)
    assert result.stdout.splitlines() == [
        f"{machine['gpu']['integrated']}|{laptop.GPU}", str(machine["cpu"]["threads"]), str(machine["disk"]["c_free"]),
        "1", "NVMe", "無效的類別", "False"]
