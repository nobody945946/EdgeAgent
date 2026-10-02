from __future__ import annotations

import threading
import time
from pathlib import Path

from edge_agent.terminal import NativeTerminal, TerminalSession


def test_terminal_preserves_cwd_variables_unicode_and_exit_codes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "終端工作"
    workspace.mkdir()
    (workspace / "子目錄").mkdir()
    terminal = TerminalSession(workspace)
    try:
        first = list(
            terminal.run(
                "$taskValue=37; Set-Location -LiteralPath '子目錄'; Write-Output '中文測試'"
            )
        )
        assert "中文測試" in "".join(str(value.get("text", "")) for _, value in first)
        assert first[-1][1]["exit_code"] == 0
        assert first[-1][1]["cwd"] == str(workspace / "子目錄")
        second = list(terminal.run("Write-Output $taskValue; cmd /c exit 7"))
        assert "37" in "".join(str(value.get("text", "")) for _, value in second)
        assert second[-1][1]["exit_code"] == 7
        failed = list(terminal.run("throw 'test failure'"))
        assert failed[-1][1]["exit_code"] == 1
        partial = list(
            terminal.run("python -c \"import sys; sys.stdout.write('partial')\"")
        )
        assert partial[-1][1]["exit_code"] == 0
        assert "partial" in "".join(str(value.get("text", "")) for _, value in partial)
    finally:
        terminal.close()
    assert terminal.process.poll() is not None


def test_terminal_stop_terminates_running_process(tmp_path: Path) -> None:
    manager = NativeTerminal()
    terminal = manager.session("stop-test", tmp_path)
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(terminal.run("Start-Sleep -Seconds 30"))
    )
    worker.start()
    time.sleep(0.5)
    manager.close("stop-test")
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert terminal.process.poll() is not None
    assert "stop-test" not in manager.sessions
