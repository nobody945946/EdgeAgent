"""Development chores: git at a glance, running a project's tests, finding code.

git runs with fixed safe options (no pager, no colour, real UTF-8 paths, no
optional locks) and read-only subcommands only. run_checks finds the
project's own test runner, so the model does not have to guess the command,
and says 「檢查通過」 only on exit code 0.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
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
from edge_agent.toolbox.fs import cap, is_hidden, list_files, shown

# ---------------------------------------------------------------- git_overview


def _git(repo: Path, *args: str, timeout: float = 60) -> tuple[int, str, str]:
    exe = shutil.which("git")
    if not exe:
        raise ToolError("這台電腦沒有 git")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}
    try:
        done = subprocess.run([exe, "--no-pager", "-c", "core.quotepath=off", "-c", "color.ui=never",
                               "-C", str(repo), *args], capture_output=True, timeout=timeout, env=env,
                              check=False, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ToolError(f"git 超過 {timeout:.0f} 秒沒有回應") from None
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _check_ref(ref: str) -> str:
    if ref.startswith("-") or re.search(r"\s|[\x00-\x1f]", ref):
        raise ToolError(f"ref「{ref}」不是合法的版本名稱（例如 HEAD、HEAD~1、main、abc1234、main..feature）")
    return ref


X_LABEL = {"M": "已暫存的修改", "A": "已暫存的新增", "D": "已暫存的刪除", "R": "已暫存的改名", "C": "已暫存的複製",
           "T": "已暫存的類型變更"}
Y_LABEL = {"M": "修改（未暫存）", "D": "刪除（未暫存）", "T": "類型變更（未暫存）"}
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _branch_text(line: str) -> str:
    if line.startswith("No commits yet on "):
        return f"分支：{line[len('No commits yet on '):]}（還沒有任何提交）"
    if line.startswith("HEAD (no branch)"):
        return "目前不在任何分支上（detached HEAD）"
    m = re.match(r"(.+?)(?:\.\.\.(\S+))?(?: \[(.+)\])?$", line)
    if not m:
        return f"分支：{line}"
    name, upstream, track = m.groups()
    text = f"分支：{name}" + (f"，追蹤 {upstream}" if upstream else "，沒有追蹤遠端分支")
    if track:
        track = track.replace("ahead", "領先").replace("behind", "落後").replace("gone", "遠端分支已經不在")
        text += f"（{track}）"
    return text


def git_status_text(out: str) -> str:
    """porcelain v1 -z --branch output, said in Chinese."""
    tokens = out.split("\0")
    head = tokens[0][3:] if tokens and tokens[0].startswith("## ") else ""
    entries: list[tuple[str, str]] = []
    i = 1 if head else 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if len(token) < 4:
            continue
        xy, path = token[:2], token[3:]
        if xy[0] in "RC" and i < len(tokens):
            path = f"{tokens[i]} → {path}"
            i += 1
        entries.append((xy, path))
    lines = [_branch_text(head)] if head else []
    if not entries:
        return "\n".join([*lines, "工作目錄是乾淨的：沒有修改，也沒有未追蹤的檔案。"])
    counts = {"已修改": 0, "新增": 0, "已刪除": 0, "改名": 0, "未追蹤": 0, "衝突": 0}
    staged = unstaged = 0
    listed = []
    for xy, path in entries:
        if xy == "??":
            counts["未追蹤"] += 1
            listed.append(f"- 未追蹤  {path}")
            continue
        if xy in CONFLICTS:
            counts["衝突"] += 1
            listed.append(f"- 衝突（要先解決）  {path}")
            continue
        x, y = xy
        counts["已修改"] += x in "MT" or y in "MT"
        counts["新增"] += x == "A"
        counts["已刪除"] += x == "D" or y == "D"
        counts["改名"] += x in "RC"
        staged += x != " "
        unstaged += y != " "
        labels = [X_LABEL[x]] if x in X_LABEL else []
        labels += [Y_LABEL[y]] if y in Y_LABEL else []
        listed.append(f"- {'、'.join(labels) or xy}  {path}")
    summary = "、".join(f"{k} {v}" for k, v in counts.items() if v)
    lines.append(f"有變動的檔案 {len(entries)} 個：{summary}（已暫存 {staged}、未暫存 {unstaged}）")
    return "\n".join([*lines, *cap(listed)])


def _capped_patch(text: str, max_lines: int = 200, budget: int = 3800) -> list[str]:
    lines = text.splitlines()
    kept, used = [], 0
    for line in lines[:max_lines]:
        line = line if len(line) <= 200 else line[:200] + "…"
        if used + len(line) > budget:
            break
        kept.append(line)
        used += len(line) + 1
    if len(kept) < len(lines):
        kept.append(f"（差異太長，只列前 {len(kept)} 行；共 {len(lines)} 行）")
    return kept


def git_run(params: dict, root: Path) -> str:
    repo = resolve_inside(root, params["repo"])
    if not repo.is_dir():
        raise ToolError(f"「{params['repo']}」不是資料夾")
    code, top, err = _git(repo, "rev-parse", "--show-toplevel")
    if code != 0:
        raise ToolError(f"「{shown(root, repo)}」不是 git 儲存庫（{_first_line(err)}）")
    what, limit = params["what"], params["limit"]
    ref = _check_ref(params["ref"]) if params["ref"] else None
    spec: list[str] = []
    if params["path"]:
        target = resolve_inside(root, params["path"], must_exist=False)
        spec = ["--", os.path.relpath(target, repo)]
    where = f"「{shown(root, repo)}」"

    def run(*args: str) -> str:
        code, out, err = _git(repo, *args)
        if code != 0:
            raise ToolError(f"git {args[0]} 失敗：{_first_line(err) or _first_line(out)}")
        return out

    if what == "status":
        return f"{where}的 git 狀態：\n" + git_status_text(run("status", "--porcelain=v1", "-z", "--branch", *spec))
    if what == "log":
        out = run("log", f"-n{limit}", "--date=format-local:%Y-%m-%d %H:%M",
                  "--pretty=format:%h%x09%ad%x09%an%x09%s", *([ref] if ref else []), *spec)
        rows = ["  ".join(line.split("\t")) for line in out.splitlines() if line]
        if not rows:
            return f"{where}沒有符合的提交。"
        return "\n".join([f"{where}最近 {len(rows)} 個提交（新的在上；縮寫、時間、作者、訊息）：", *rows])
    if what in ("diff", "staged_diff"):
        base = ["diff", *(["--cached"] if what == "staged_diff" else []), *([ref] if ref else [])]
        stat = run(*base, "--stat=100", *spec).rstrip()
        if not stat:
            which = "已暫存（staged）的" if what == "staged_diff" else "還沒暫存的"
            return f"{where}沒有{which}差異" + (f"（和 {ref} 比）" if ref else "") + "。"
        patch = run(*base, *spec)
        stat_lines = stat.splitlines()
        stat_lines = [*stat_lines[:-1][:40], *(["…"] if len(stat_lines) > 41 else []), stat_lines[-1]]
        title = "已暫存的差異" if what == "staged_diff" else "差異" + (f"（和 {ref} 比）" if ref else "（還沒暫存的）")
        return "\n".join([f"{where}的{title}：", "改了哪些檔案：", *stat_lines, "", *_capped_patch(patch)])
    if what == "show":
        target = ref or "HEAD"
        head = run("show", "--stat=100", "--date=format-local:%Y-%m-%d %H:%M",
                   "--format=提交 %H%n作者 %an <%ae>%n時間 %ad%n%n%B", target, *spec).rstrip()
        patch = run("show", "--format=", "--patch", target, *spec)
        head_lines = head.splitlines()
        return "\n".join([*head_lines[:60], *(["…"] if len(head_lines) > 60 else []), "",
                          *_capped_patch(patch, budget=3000)])
    out = run("for-each-ref", "--sort=-committerdate",
              "--format=%(HEAD)%09%(refname)%09%(refname:short)%09%(objectname:short)%09%(committerdate:short)"
              "%09%(upstream:short)%09%(upstream:track)%09%(contents:subject)", "refs/heads", "refs/remotes")
    rows = []
    for line in out.splitlines():
        current, full, name, sha, day, upstream, track, subject = (line.split("\t") + [""] * 8)[:8]
        if full.endswith("/HEAD"):
            continue
        kind = "本地" if full.startswith("refs/heads/") else "遠端"
        extra = f"，追蹤 {upstream}{(' ' + track) if track else ''}" if upstream else ""
        rows.append(f"{'* ' if current == '*' else '  '}{name}（{kind}）{sha} {day}{extra}  {subject[:80]}")
    if not rows:
        return f"{where}還沒有任何分支（沒有提交）。"
    return "\n".join([f"{where}的分支（* 是目前的分支，最近有提交的在上）：", *cap(rows, limit)])


register(Tool(
    name="git_overview",
    summary="Read-only git: status, recent log, diff, staged diff, one commit, or branches, summarised.",
    keywords="git 版本 狀態 改了什麼 差異 提交 紀錄 分支 commit log diff status branch staged show 版控",
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "default": ".", "description": "git 儲存庫的資料夾，預設是工作區"},
            "what": {"type": "string", "enum": ["status", "log", "diff", "staged_diff", "show", "branches"],
                     "description": "status 狀態、log 最近的提交、diff 還沒暫存的差異、staged_diff 已暫存的差異、"
                                    "show 某個提交的內容、branches 分支"},
            "ref": {"type": "string",
                    "description": "版本（log／diff／show 用），例如 HEAD~1、main、abc1234；show 不填就是 HEAD"},
            "path": {"type": "string", "description": "只看這個檔案或資料夾"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200,
                      "description": "log 列幾個提交、branches 列幾個分支"},
        },
        "required": ["what"],
    },
    kind="read", destructive=False, windows_only=False,
    run=git_run, preview=None,
))


# ---------------------------------------------------------------- run_checks

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
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # a grandchild outside the group still holds the pipe
            out = b""
    return Outcome(None if timed_out else proc.returncode, _decode_output(out or b""),
                   time.monotonic() - start, timed_out)


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


# ---------------------------------------------------------------- code_outline

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", "target", ".tox", ".eggs",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", "obj", "coverage", "site-packages"}
PY = {".py", ".pyw"}
JS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"}
PS = {".ps1", ".psm1"}
TEXT = PY | JS | PS | {".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".html", ".htm",
                       ".css", ".scss", ".xml", ".sql", ".sh", ".bat", ".cmd", ".psd1", ".java", ".c", ".h", ".cpp",
                       ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".kt", ".swift", ".lua", ".vue", ".svelte"}
MAX_FILE = 1024 * 1024
MAX_FILES = 3000

JS_RULES = (
    (re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("), "function"),
    (re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?"
                r"(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)"), "function"),
    (re.compile(r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)"), "export"),
    (re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)"), "type"),
    (re.compile(r"^\s*export\s*\{([^}]*)\}"), "export list"),
)
JS_METHOD = re.compile(r"^(\s+)(?:(?:public|private|protected|static|async|get|set|readonly|override)\s+)*"
                       r"\*?([A-Za-z_$#][\w$]*)\s*\([^;]*\)\s*(?::\s*[^{]+)?\{\s*$")
JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "with", "constructor"}
PS_RULE = re.compile(r"^\s*(function|filter|class)\s+([\w-]+)", re.I)


@dataclass
class Symbol:
    path: Path
    line: int
    kind: str
    name: str


def python_symbols(path: Path, source: str) -> tuple[list[Symbol], str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        found = [Symbol(path, n, m.group(1).split()[-1], m.group(2)) for n, line in enumerate(source.splitlines(), 1)
                 if (m := re.match(r"^\s*(async\s+def|def|class)\s+(\w+)", line))]
        return found, f"語法錯誤（第 {exc.lineno} 行），改用簡單比對"
    found: list[Symbol] = []

    def visit(body: list, prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                found.append(Symbol(path, node.lineno, "class", prefix + node.name))
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = ast.unparse(node.args)
                args = args if len(args) <= 70 else args[:70] + "…"
                kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                found.append(Symbol(path, node.lineno, kind, f"{prefix}{node.name}({args})"))
            elif isinstance(node, ast.If | ast.Try | ast.With):
                for part in ("body", "orelse", "finalbody"):
                    visit(getattr(node, part, []), prefix)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body, prefix)

    visit(tree.body, "")
    return found, ""


def js_symbols(path: Path, source: str) -> list[Symbol]:
    found: list[Symbol] = []
    classes: list[tuple[int, str]] = []  # (indent, name) of the classes we are inside
    for n, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while classes and indent <= classes[-1][0] and line.strip().startswith("}"):
            classes.pop()
        for rule, kind in JS_RULES:
            m = rule.match(line)
            if m:
                if kind == "export list":  # export { a, b as c }
                    found += [Symbol(path, n, "export", x.split(" as ")[-1].strip())
                              for x in m.group(1).split(",") if x.strip()]
                else:
                    found.append(Symbol(path, n, kind, m.group(1)))
                if kind == "class":
                    classes.append((indent, m.group(1)))
                break
        else:
            m = JS_METHOD.match(line)
            if m and classes and m.group(2) not in JS_KEYWORDS - {"constructor"}:
                found.append(Symbol(path, n, "method", f"{classes[-1][1]}.{m.group(2)}"))
    return found


def ps_symbols(path: Path, source: str) -> list[Symbol]:
    return [Symbol(path, n, m.group(1).lower(), m.group(2)) for n, line in enumerate(source.splitlines(), 1)
            if (m := PS_RULE.match(line))]


def _read_source(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8-sig", errors="replace")


def _symbols(path: Path, source: str) -> tuple[list[Symbol], str]:
    suffix = path.suffix.casefold()
    if suffix in PY:
        return python_symbols(path, source)
    if suffix in JS:
        return js_symbols(path, source), ""
    if suffix in PS:
        return ps_symbols(path, source), ""
    return [], ""


def _source_files(target: Path) -> tuple[list[Path], int]:
    if target.is_file():
        return [target], 0
    listing = list_files(target, recursive=True,
                         prune=lambda p: p.name in SKIP_DIRS or p.name.endswith(".egg-info") or is_hidden(p))
    files = [p for p in listing.files if p.suffix.casefold() in TEXT and not is_hidden(p)]
    return files[:MAX_FILES], max(0, len(files) - MAX_FILES)


def _cjk(text: str) -> bool:
    return any("㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in text)


def make_scorer(query: str):
    """Score text by query words; CJK words also by their two-character pieces."""
    words = [w.casefold() for w in query.split()]
    raw = " ".join(query.split()).casefold()
    pieces = {w: [w[i:i + 2] for i in range(len(w) - 1)] for w in words if _cjk(w) and len(w) >= 3}

    def score(text: str) -> float:
        t = text.casefold()
        total = sum(2.0 for w in words if w in t)
        for w, grams in pieces.items():
            if w not in t:
                hit = sum(1 for g in grams if g in t) / len(grams)
                total += hit if hit >= 0.5 else 0
        if len(words) > 1 and raw in t:
            total += 2
        return total

    return score


def outline_run(params: dict, root: Path) -> str:
    target = resolve_inside(root, params["path"])
    files, beyond = _source_files(target)
    notes = [f"檔案太多，只看前 {MAX_FILES} 個（還有 {beyond} 個）"] if beyond else []
    query = (params["query"] or "").strip()
    if not query:
        lines: list[str] = []
        budget, total, listed = 5000, 0, 0
        for path in files:
            source = _read_source(path) if path.suffix.casefold() in PY | JS | PS else None
            symbols, note = _symbols(path, source) if source is not None else ([], "")
            total += len(symbols)
            if not symbols or budget <= 0:
                continue
            lines.append(f"{shown(root, path)}（{len(symbols)} 個）" + (f"［{note}］" if note else ""))
            budget -= len(lines[-1]) + 1
            for s in symbols:
                entry = f"  {s.line:>5}  {s.kind} {s.name}"
                if len(entry) >= budget:
                    budget = 0
                    break
                lines.append(entry)
                budget -= len(entry) + 1
                listed += 1
        if not total:
            return f"「{shown(root, target)}」裡沒有找到 Python、JavaScript／TypeScript 或 PowerShell 的函式和類別。"
        head = f"「{shown(root, target)}」的大綱（行號、種類、名稱），共 {total} 個符號："
        tail = [f"…還有 {total - listed} 個符號沒列出（可以指定一個檔案，或用 query 搜尋）"] if total > listed else []
        return "\n".join([head, *lines, *tail, *notes])
    score = make_scorer(query)
    hits: list[tuple[float, int, str, int, str]] = []
    symbol_count = line_count = 0
    for path in files:
        source = _read_source(path)
        if source is None:
            continue
        rel = shown(root, path)
        symbol_lines = set()
        for s in _symbols(path, source)[0]:
            value = score(s.name)
            if value > 0:
                symbol_count += 1
                symbol_lines.add(s.line)
                hits.append((value + 1, 0, rel, s.line, f"{s.kind} {s.name}"))  # +1: a definition
        for n, line in enumerate(source.splitlines(), 1):
            if n in symbol_lines:
                continue
            value = score(line)
            if value > 0:
                line_count += 1
                text = line.strip()
                hits.append((value, 1, rel, n, text if len(text) <= 160 else text[:160] + "…"))
    if not hits:
        return f"在「{shown(root, target)}」裡找不到和「{query}」有關的符號或內容（看了 {len(files)} 個檔案）。"
    hits.sort(key=lambda h: (-h[0], h[1], h[2], h[3]))
    lines = [f"{rel}:{n}  {text}" for _, _, rel, n, text in hits[:30]]
    head = (f"在「{shown(root, target)}」裡找「{query}」：符合的符號 {symbol_count} 個、內容 {line_count} 行，"
            f"最相關的 {len(lines)} 個（路徑:行號  內容）：")
    return "\n".join([head, *lines, *notes])


register(Tool(
    name="code_outline",
    summary="Outline functions/classes of code files (Python, JS/TS, PowerShell) or rank code by a search query.",
    keywords="程式碼 大綱 函式 類別 在哪裡 找程式 搜尋 定義 結構 code outline functions classes symbols search "
             "find definition where is grep",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "檔案或資料夾（資料夾會略過 .git、node_modules、.venv 等）"},
            "query": {"type": "string",
                      "description": "要找的字（空白分隔，中文也可以）；有填就列出最相關的符號和程式行，不填就列大綱"},
        },
        "required": ["path"],
    },
    kind="read", destructive=False, windows_only=False,
    run=outline_run, preview=None,
))
