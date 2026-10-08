"""run_checks: a project's own tests, or a given command, with a time limit.

It finds the project's test runner, so the model does not have to guess the
command, and says 「檢查通過」 only on exit code 0. A run past its time limit
is stopped together with every program it started.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from edge_agent.toolbox import (
    IS_WINDOWS,
    NO_WINDOW,
    Tool,
    ToolError,
    register,
    resolve_inside,
)
from edge_agent.toolbox.fs import cap, shown

COUNT_WORDS = {"passed": "通過", "failed": "失敗", "error": "錯誤", "errors": "錯誤", "skipped": "略過",
               "xfailed": "預期中的失敗", "xpassed": "意外通過", "deselected": "沒選到", "warning": "警告",
               "warnings": "警告"}
_COUNTS = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def _has_pytest(python: str) -> bool:
    try:
        return subprocess.run([python, "-c", "import pytest"], capture_output=True, timeout=30,
                              check=False, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _uses_pytest(folder: Path) -> bool:
    def contains(name: str, marker: str) -> bool:
        try:
            return marker in (folder / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    if (folder / "pytest.ini").is_file() or (folder / "conftest.py").is_file():
        return True
    if contains("pyproject.toml", "[tool.pytest") or contains("setup.cfg", "[tool:pytest]") \
            or contains("tox.ini", "[pytest]"):
        return True
    tests = folder / "tests"
    if tests.is_dir() and any(True for _ in tests.rglob("test_*.py")):
        return True
    return any(True for _ in folder.glob("test_*.py"))


def detect_runner(folder: Path) -> tuple[list[str], str]:
    """The project's own test command and what it was detected from."""
    if _uses_pytest(folder):
        python, note = sys.executable, "pytest"
        for venv in (".venv", "venv"):
            exe = folder / venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
            if exe.is_file():
                if _has_pytest(str(exe)):
                    python, note = str(exe), f"pytest，用專案的 {venv}"
                else:
                    note = f"pytest；專案的 {venv} 沒有裝 pytest，改用 EdgeAgent 的 Python"
                break
        return [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"], note
    package = folder / "package.json"
    if package.is_file():
        try:
            test = (json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}).get("test", "")
        except (OSError, ValueError, AttributeError):
            test = ""
        if test and "no test specified" not in test:
            npm = shutil.which("npm")
            if not npm:
                raise ToolError("package.json 有 test 指令，但這台電腦沒有 npm")
            return [npm, "test", "--silent"], "package.json 的 test 指令"
    for marker, tool, args in (("Cargo.toml", "cargo", ["test"]), ("go.mod", "go", ["test", "./..."])):
        if (folder / marker).is_file():
            exe = shutil.which(tool)
            if not exe:
                raise ToolError(f"有 {marker}，但這台電腦沒有 {tool}")
            return [exe, *args], marker
    raise ToolError("找不到這個專案的測試設定（pytest、package.json 的 test、Cargo.toml、go.mod 都沒有）；"
                    "請用 command 指定要跑的指令")


def _command_for(params: dict, folder: Path) -> tuple[list[str], str]:
    command = params["command"]
    if not command:
        return detect_runner(folder)
    if IS_WINDOWS:
        exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
        prefix = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
        return [exe, "-NoProfile", "-NonInteractive", "-Command", prefix + command], "指定的指令，用 PowerShell 執行"
    return ["/bin/sh", "-c", command], "指定的指令，用 sh 執行"


def _shown_command(argv: list[str], params: dict) -> str:
    if params["command"]:
        return params["command"]
    return subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)


def _kill_tree(proc: subprocess.Popen) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False,
                       creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    proc.kill()


def _decode_output(data: bytes) -> str:
    for codec in ("utf-8", "mbcs") if IS_WINDOWS else ("utf-8",):
        try:
            text = data.decode(codec)
            break
        except (UnicodeDecodeError, LookupError):
            pass
    else:
        text = data.decode("utf-8", errors="replace")
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    return "\n".join(line.split("\r")[-1] for line in text.split("\n"))  # progress bars: keep what stayed


@dataclass
class Outcome:
    code: int | None
    output: str
    seconds: float
    timed_out: bool
    left_running: bool = False  # the command ended, a program it started (a window, a server) did not


def execute(argv: list[str], cwd: Path, timeout: float) -> Outcome:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1", "FORCE_COLOR": "0",
           "PY_COLORS": "0", "GIT_TERMINAL_PROMPT": "0"}
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW} if IS_WINDOWS \
        else {"start_new_session": True}
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, **group)
    except OSError as exc:
        raise ToolError(f"沒辦法執行 {argv[0]}：{exc}") from exc
    # Wait for the command itself, not for its output pipe to close: a
    # program it starts (`code .` opening VS Code, a dev server) inherits the
    # pipe and keeps it open while it runs (2026-10-08: the tool waited until
    # the owner closed VS Code by hand, minutes later).
    chunks: list[bytes] = []

    def pump() -> None:
        assert proc.stdout is not None
        while chunk := proc.stdout.read1(65536):
            chunks.append(chunk)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    reader.join(timeout=2)  # what is still buffered; a program left running keeps the pipe
    return Outcome(None if timed_out else proc.returncode, _decode_output(b"".join(chunks)),
                   time.monotonic() - start, timed_out, left_running=reader.is_alive() and not timed_out)


def pytest_summary(output: str) -> list[str]:
    lines = output.splitlines()
    final = next((line for line in reversed(lines)
                  if re.search(r"\bin \d+(\.\d+)?s\b", line) and (_COUNTS.search(line) or "no tests ran" in line)),
                 "")
    result = []
    if final:
        counts = [f"{n} {COUNT_WORDS[word]}" for n, word in _COUNTS.findall(final)]
        result.append("結果：" + ("、".join(counts) if counts else "沒有跑到任何測試"))
    failed = re.findall(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", output, re.M)
    if failed:
        result.append(f"沒過的測試 {len(failed)} 個：")
        # Windows' pytest writes tests\test_b.py; the summary reads the same everywhere.
        result += cap([f"- {'失敗' if kind == 'FAILED' else '錯誤'} {test.replace(chr(92), '/')}"
                       + (f"：{why[:200]}" if why else "") for kind, test, why in failed], 25)
    if "No module named pytest" in output:
        result.append("這個 Python 沒有安裝 pytest。")
    return result


def _tail(text: str, size: int = 3000) -> str:
    text = text.strip("\n")
    if len(text) <= size:
        return text
    cut = text[-size:]
    return "…" + cut[cut.find("\n") + 1:] if "\n" in cut else "…" + cut


def _check_folder(params: dict, root: Path) -> Path:
    folder = resolve_inside(root, params["path"])
    if not folder.is_dir():
        raise ToolError(f"「{params['path']}」不是資料夾")
    return folder


def checks_preview(params: dict, root: Path) -> str:
    folder = _check_folder(params, root)
    argv, how = _command_for(params, folder)
    return (f"會在「{shown(root, folder)}」裡執行：{_shown_command(argv, params)}\n"
            f"（{how}；最多等 {params['timeout_s']} 秒，超過就連同它開的程式一起停止）")


def checks_run(params: dict, root: Path) -> str:
    folder = _check_folder(params, root)
    argv, how = _command_for(params, folder)
    outcome = execute(argv, folder, params["timeout_s"])
    if outcome.timed_out:
        head = f"檢查沒通過：超過 {params['timeout_s']} 秒還沒結束，已經停止（連同它開的程式）。"
    elif outcome.code == 0:
        head = "檢查通過（結束代碼 0）。"
    else:
        head = f"檢查沒通過（結束代碼 {outcome.code}）。"
    lines = [head, f"指令：{_shown_command(argv, params)}", f"資料夾：「{shown(root, folder)}」（{how}）",
             f"花了 {outcome.seconds:.1f} 秒"]
    if outcome.left_running:
        lines.append("指令本身已經結束；它開啟的程式（例如視窗程式或伺服器）還在執行，沒有被關掉。")
    if argv[1:3] == ["-m", "pytest"] or "pytest" in (params["command"] or ""):
        lines += pytest_summary(outcome.output)
    lines += ["輸出的最後部分：", _tail(outcome.output) or "（沒有輸出）"]
    return "\n".join(lines)


register(Tool(
    name="run_checks",
    summary="Run the project's tests (auto-detects pytest, npm test, cargo test, go test) or a given command.",
    keywords="測試 跑測試 檢查 驗證 單元測試 pytest npm test cargo go 確認有沒有壞 run tests check verify build CI",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string",
                        "description": "要執行的指令（Windows 用 PowerShell）；不填就自動找專案的測試指令"},
            "path": {"type": "string", "default": ".", "description": "專案資料夾，預設是工作區"},
            "timeout_s": {"type": "integer", "default": 600, "minimum": 1, "maximum": 7200,
                          "description": "最多等幾秒，超過就停止"},
        },
    },
    kind="run", destructive=False, windows_only=False,
    run=checks_run, preview=checks_preview,
))
