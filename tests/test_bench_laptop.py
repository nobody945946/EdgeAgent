"""bench/laptop.py and bench/windows: the sandbox answers Windows' own
commands with the laptop the product runs on."""

from __future__ import annotations

import json
import os
import re
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
needs_image = pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(
    ["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode != 0,
    reason="needs the bench image (bench/run.py --build)")


def in_sandbox(tmp_path: Path, machine: dict, *command: str) -> list[str]:
    """Run a command in the agent's sandbox as bench/run.py mounts it, dressed
    as `machine`; its output lines."""
    import run

    (tmp_path / "machine.json").write_text(json.dumps(machine, ensure_ascii=False), encoding="utf-8")
    mounts = [arg for source, target, mode in run.code_mounts() for arg in ("-v", f"{source}:{target}:{mode}")]
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,exec",
         "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-e", "PYTHONPATH=/opt/edgeagent",
         "-e", "PSModulePath=/opt/bench/windows", "-e", "LAPTOP_FACTS=/facts/machine.json",
         *mounts, "-v", f"{tmp_path}:/facts:ro", IMAGE, *command],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=180)
    return result.stdout.splitlines()


@needs_image
def test_the_sandbox_powershell_answers_windows_commands_with_the_laptop(tmp_path: Path) -> None:
    machine = laptop.machine("train-F-019")
    commands = """
(Get-CimInstance Win32_VideoController).Name -join '|'
(Get-CimInstance -ClassName Win32_Processor).NumberOfLogicalProcessors
(Get-CimInstance -Query "SELECT * FROM Win32_LogicalDisk WHERE DeviceID = 'C:'").FreeSpace
@(Get-CimInstance Win32_Process -Filter "Name LIKE 'llama%'").Count
(Get-PhysicalDisk).BusType
try { Get-CimInstance Win32_Nope -ErrorAction Stop } catch { $_.Exception.Message.Trim() }
[bool](Get-Command Get-WmiObject -ErrorAction SilentlyContinue)
'Get-Process', 'Get-PSDrive', 'Get-ComputerInfo' | ForEach-Object { "$((Get-Command $_).CommandType) $((Get-Command $_).ModuleName)" }
(Get-Process -Name llama-server).Path
(Get-Process -Id $PID).Path
(Get-Process llama-server).WorkingSet64 -eq (Get-CimInstance Win32_Process -Filter "Name = 'llama-server.exe'").WorkingSetSize
@(Get-Process -Name *edge*).Count
try { Get-Process -Name nope -ErrorAction Stop } catch { $_.Exception.Message }
(Get-PSDrive C).Free
(Get-PSDrive -PSProvider FileSystem).Name -join '|'
try { Get-PSDrive D -ErrorAction Stop } catch { $_.Exception.Message }
(Get-ComputerInfo).CsName
(Get-ChildItem /opt/bench/windows -Directory).Name -join '|'
$started = Start-Process sleep -ArgumentList 30 -PassThru; (Get-Process -Id $started.Id).ProcessName; Stop-Process -Id $started.Id
"""
    (tmp_path / "try.ps1").write_text(commands, encoding="utf-8")
    lines = in_sandbox(tmp_path, machine, "pwsh", "-NoProfile", "-NonInteractive", "-File", "/facts/try.ps1")
    assert lines == [
        f"{machine['gpu']['integrated']}|{laptop.GPU}", str(machine["cpu"]["threads"]), str(machine["disk"]["c_free"]),
        "1", "NVMe", "無效的類別", "False",
        *["Cmdlet Microsoft.PowerShell.Management"] * 3,
        f"C:\\Users\\{machine['user']}\\EdgeAgent\\.runtime\\llama-b11193-cuda\\llama-server.exe",
        "C:\\Program Files\\PowerShell\\7\\pwsh.exe", "True", "3",
        'Cannot find a process with the name "nope". Verify the process name and call the cmdlet again.',
        str(machine["disk"]["c_free"]), "C|Temp", "Cannot find drive. A drive with the name 'D' does not exist.",
        machine["computer"], "bin|CimCmdlets|Storage", "sleep"]


@needs_image
def test_the_sandbox_management_module_is_powershells_own_plus_get_computerinfo(tmp_path: Path) -> None:
    def exports(text: str) -> set[str]:
        return set(re.findall(r"[\"']([A-Za-z]+-[A-Za-z]+)[\"']", text.split("CmdletsToExport")[1]))

    image = subprocess.run(["docker", "run", "--rm", IMAGE, "cat",
                            "/opt/pwsh/Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1"],
                           capture_output=True, text=True, check=True).stdout
    ours = (BENCH / "pshome/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1").read_text(
        encoding="utf-8")
    assert exports(ours) == exports(image) | {"Get-ComputerInfo"}


@needs_image
def test_a_read_only_task_reads_the_sandbox_laptop_as_it_reads_the_laptop(tmp_path: Path) -> None:
    commands = {
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 Name, Id": True,
        "Get-PSDrive C | Select-Object Used, Free": True,
        "Get-ComputerInfo -Property OsName": True,
        "Get-Process | Select-Object Name, CommandLine": False,
    }
    (tmp_path / "proof.py").write_text(
        "import json, sys\nfrom pathlib import Path\nfrom edge_agent.permissions import routine_shell\n"
        "print(json.dumps([routine_shell(c, Path('/tmp')) for c in json.loads(sys.argv[1])]))\n", encoding="utf-8")
    lines = in_sandbox(tmp_path, laptop.machine("train-F-019"), "python", "/facts/proof.py", json.dumps(list(commands)))
    assert json.loads(lines[-1]) == list(commands.values())


def test_memory_answers_are_graded_against_the_laptop_not_the_container(tmp_path: Path) -> None:
    import random

    import tool_tasks_chores

    task = tool_tasks_chores.gen_sysinfo(random.Random(0))
    assert f"total_kb = {laptop.TOTAL_KB}" in task.check and "/proc/meminfo" not in task.check
    check = (BENCH / "tasks" / "tool-sysinfo" / "check.py").read_text(encoding="utf-8")
    assert f"total_kb = {laptop.TOTAL_KB}" in check and "/proc/meminfo" not in check
    for answer, passed in (("記憶體總共 8 GB。", True), ("記憶體總共 125.6 GB。", False)):
        (tmp_path / "answer.json").write_text(json.dumps({"answer": answer, "calls": []}, ensure_ascii=False),
                                              encoding="utf-8")
        run = subprocess.run([sys.executable, str(BENCH / "tasks" / "tool-sysinfo" / "check.py")],
                             env={**os.environ, "BENCH_ANSWER": str(tmp_path / "answer.json")},
                             capture_output=True, text=True, check=False)
        assert (run.returncode == 0) is passed, run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_stand_in_drone_flies_like_a_vehicle() -> None:
    def session(*calls: tuple[str, dict], start: str = "") -> list[dict]:
        lines = "".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                    "params": {"name": name, "arguments": args}}) + "\n"
                        for i, (name, args) in enumerate(calls))
        run = subprocess.run(["node", str(BENCH / "mcp_eval" / "servers.mjs"), "drone"], input=lines,
                             capture_output=True, text=True, timeout=30, check=True,
                             env={**os.environ, "MCP_DATA_VARIANT": "train", "MCP_DRONE_START": start})
        return [json.loads(json.loads(line)["result"]["content"][0]["text"]) for line in run.stdout.splitlines()]

    early, armed, up, where, home = session(("takeoff", {"altitude_m": 15}), ("arm", {}),
                                             ("takeoff", {"altitude_m": 15}), ("get_position", {}),
                                             ("return_to_launch", {}))
    assert early["result"] == "DENIED" and "arm" in early["reason"].lower()
    assert armed["result"] == up["result"] == home["result"] == "ACCEPTED"
    assert where["relative_alt_m"] == 15
    grounded, = session(("return_to_launch", {}))
    assert grounded["result"] == "DENIED"
    back, status = session(("return_to_launch", {}), ("get_status", {}), start="airborne")
    assert back["result"] == "ACCEPTED" and status["armed"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_arming_is_part_of_a_takeoff_the_owner_asked_for(tmp_path: Path) -> None:
    import tool_tasks_mcp

    expected = [("drone", "takeoff", {"altitude_m": 15})]
    code = "import json, os, re, sys\nfrom pathlib import Path\n" \
        "A = json.loads(Path(os.environ['BENCH_ANSWER']).read_text(encoding='utf-8'))\n" \
        "ANSWER = A.get('answer', ''); CALLS = A.get('calls', []); errors = []\n" \
        "def need(c, why):\n    errors.append(why) if not c else None\n" \
        + tool_tasks_mcp.mcp_check(expected, [], {"drone": tool_tasks_mcp.MCP_SERVERS["drone"]}) \
        + "print('\\n'.join(errors) or 'ok'); sys.exit(1 if errors else 0)\n"
    (tmp_path / "check.py").write_text(code, encoding="utf-8")
    for calls, passed in (([("arm", {}), ("takeoff", {"altitude_m": 15})], True),
                          ([("takeoff", {"altitude_m": 15}), ("land", {})], False)):
        answer = {"answer": "", "calls": [{"name": f"mcp__drone__{n}", "arguments": a} for n, a in calls]}
        (tmp_path / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
        run = subprocess.run([sys.executable, str(tmp_path / "check.py")], capture_output=True, text=True,
                             check=False, env={**os.environ, "BENCH_ANSWER": str(tmp_path / "answer.json")})
        assert (run.returncode == 0) is passed, run.stdout
    for task in ("mcp-d-takeoff", "mcp-d-rtl"):
        assert 'wanted.add("arm")' in (BENCH / "tasks" / task / "check.py").read_text(encoding="utf-8")
    rtl = json.loads((BENCH / "tasks" / "mcp-d-rtl" / "task.json").read_text(encoding="utf-8"))
    assert rtl["mcp_servers"]["drone"]["env"]["MCP_DRONE_START"] == "airborne"
