"""edge_agent.toolbox development chores: git_overview, run_checks, code_outline."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from edge_agent.toolbox import IS_WINDOWS, ToolError
from tests.toolbox_helpers import preview, run, snapshot, write

# ---------------------------------------------------------------- git_overview


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="no git here")
def test_git_overview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The workspace is no repository even when the test folder sits inside one
    # (the laptop's --basetemp is under the EdgeAgent checkout).
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    write(repo / "a.txt", "one\n")
    write(repo / "刪.txt", "x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "第一個提交")
    write(repo / "a.txt", "".join(f"line {i}\n" for i in range(600)))
    (repo / "刪.txt").unlink()
    write(repo / "新.txt", "n")
    write(repo / "staged.txt", "s")
    git(repo, "add", "staged.txt")
    out = run("git_overview", tmp_path, repo="repo", what="status")
    assert "分支：main" in out and "已修改 1" in out and "新增 1" in out and "已刪除 1" in out and "未追蹤 1" in out
    assert "刪.txt" in out and "新.txt" in out  # real UTF-8 names, not quoted octal
    assert "第一個提交" in run("git_overview", tmp_path, repo="repo", what="log")
    out = run("git_overview", tmp_path, repo="repo", what="diff")
    assert "a.txt" in out.split("\n")[2] and "差異太長，只列前" in out
    assert "staged.txt" in run("git_overview", tmp_path, repo="repo", what="staged_diff")
    assert "第一個提交" in run("git_overview", tmp_path, repo="repo", what="show")
    assert "* main" in run("git_overview", tmp_path, repo="repo", what="branches")
    with pytest.raises(ToolError, match="不是合法的版本名稱"):
        run("git_overview", tmp_path, repo="repo", what="diff", ref="--output=/tmp/x")
    with pytest.raises(ToolError, match="不是 git 儲存庫"):
        run("git_overview", tmp_path, repo=".", what="status")


# ---------------------------------------------------------------- run_checks


def test_run_checks_pass_and_fail(tmp_path: Path) -> None:
    write(tmp_path / "proj" / "tests" / "test_a.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    before = snapshot(tmp_path)
    plan = preview("run_checks", tmp_path, path="proj")
    assert "pytest" in plan and "會在「proj」裡執行" in plan and snapshot(tmp_path) == before
    out = run("run_checks", tmp_path, path="proj")
    assert out.startswith("檢查通過") and "1 通過" in out
    write(tmp_path / "proj" / "tests" / "test_b.py", "def test_bad():\n    assert 1 == 2, '數字不對'\n")
    out = run("run_checks", tmp_path, path="proj")
    assert out.startswith("檢查沒通過（結束代碼 1）") and "1 失敗、1 通過" in out
    assert "tests/test_b.py::test_bad：AssertionError: 數字不對" in out
    with pytest.raises(ToolError, match="找不到這個專案的測試設定"):
        run("run_checks", tmp_path, path=".")


def test_run_checks_returns_when_the_command_ends_though_a_program_it_started_runs_on(tmp_path: Path) -> None:
    """`code .` opening VS Code: the window inherits the output pipe and stays
    open; the tool answers when the command ends, and leaves the window open."""
    launcher = ("Start-Process powershell -ArgumentList '-Command','Start-Sleep 20' -NoNewWindow; 'opened'"
                if IS_WINDOWS else "sleep 20 & echo opened")
    started = time.monotonic()
    out = run("run_checks", tmp_path, command=launcher, timeout_s=60)
    assert time.monotonic() - started < 10
    assert "檢查通過" in out and "opened" in out
    if not IS_WINDOWS:
        assert "還在執行，沒有被關掉" in out


def test_run_checks_command_and_timeout(tmp_path: Path) -> None:
    out = run("run_checks", tmp_path, command="echo 你好")
    assert out.startswith("檢查通過") and "你好" in out
    assert run("run_checks", tmp_path, command="exit 3").startswith("檢查沒通過（結束代碼 3）")
    sleeper = "Start-Sleep 30" if IS_WINDOWS else "sleep 30 & sleep 30; wait"
    start = time.monotonic()
    out = run("run_checks", tmp_path, command=sleeper, timeout_s=1)
    assert out.startswith("檢查沒通過：超過 1 秒") and time.monotonic() - start < 20


# ---------------------------------------------------------------- code_outline


def test_code_outline_and_query(tmp_path: Path) -> None:
    write(tmp_path / "src" / "app.py", "class Loader:\n    def read(self, n):\n        return n\n\n"
                                       "    class Inner:\n        async def go(self): ...\n\n"
                                       "def load_config(path, *, strict=False):\n    '''讀取設定檔'''\n")
    write(tmp_path / "src" / "web.mjs", "export async function startServer(port) {}\nexport const ping = () => 1;\n"
                                        "class Cache {\n  get(key) {\n    return 1;\n  }\n}\n")
    write(tmp_path / "src" / "tool.ps1", "function Get-Report {\n}\n")
    write(tmp_path / "node_modules" / "dep.js", "function hidden() {}\n")
    out = run("code_outline", tmp_path, path="src")
    for expected in ("2  def Loader.read(self, n)", "6  async def Loader.Inner.go(self)", "8  def load_config(",
                     "1  function startServer", "2  function ping", "4  method Cache.get",
                     "1  function Get-Report"):
        assert expected in out, expected
    assert "hidden" not in run("code_outline", tmp_path, path=".")
    out = run("code_outline", tmp_path, path=".", query="load config")
    assert out.splitlines()[1].startswith(os.path.join("src", "app.py") + ":8")
    out = run("code_outline", tmp_path, path=".", query="設定")
    assert "app.py:9" in out
    assert "找不到" in run("code_outline", tmp_path, path=".", query="zzzz")
