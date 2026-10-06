"""Host-driven coding for a small model.

In a free agent loop E4B loses most real-repository tasks: it narrates
instead of acting, its exact-text edits miss, and with more tools it loops
(bench fw-v3: repo 2/44; with the agent adapter 0/44, 66 turns of read/ls).
Here the program does everything that needs no judgment: it reproduces the
failure, lists the functions the failure runs through, splices code in by
the syntax tree and runs the tests. The model makes only the two judgments:
which functions to change, and their new code. Several versions are sampled
and only the tests decide; when none passes, the closest one's new error
goes back to the model.

Python repositories tested with pytest, for now.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "site-packages", "build", "dist",
             "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", "docs", "doc"}
TEST_TIMEOUT = 300
MAX_CANDIDATES = 8
MAX_CHOSEN = 2


@dataclass
class Definition:
    """A function or method in the repository, by its lines."""

    file: str  # relative, with forward slashes
    name: str  # qualified: "Class.method" or "function"
    start: int  # first line, decorators included (1-based)
    end: int  # last line
    indent: str
    source: str

    @property
    def short(self) -> str:
        return self.name.rsplit(".", 1)[-1]


@dataclass
class TestRun:
    failed: list[str]
    errors: int
    output: str
    timed_out: bool = False

    @property
    def broken(self) -> set[str]:
        return set(self.failed)


@dataclass
class Result:
    outcome: str  # solved, unsolved, no_failure, error
    answer: str
    changed: list[str] = field(default_factory=list)
    rounds: int = 0
    samples: int = 0
    model_calls: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    trace: list[dict] = field(default_factory=list)


class Model:
    """An OpenAI-compatible chat endpoint (llama-server)."""

    def __init__(self, url: str, *, thinking: bool = False, timeout: float = 600):
        self.url = url.rstrip("/") + "/v1/chat/completions"
        self.thinking = thinking
        self.timeout = timeout
        self.calls = 0
        self.tokens = {"input": 0, "output": 0}

    def chat(self, messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 2048) -> str:
        body = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": self.thinking}}
        request = urllib.request.Request(self.url, json.dumps(body).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read())
        self.calls += 1
        usage = data.get("usage") or {}
        self.tokens["input"] += int(usage.get("prompt_tokens") or 0)
        self.tokens["output"] += int(usage.get("completion_tokens") or 0)
        return str(data["choices"][0]["message"].get("content") or "")


# ---------------------------------------------------------------- repository


def is_test(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) or \
        parts[-1].startswith("test_") or parts[-1].endswith("_test.py") or parts[-1] == "conftest.py"


def python_files(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        found.append(rel)
    return found


def _first_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)])


def definitions(root: Path, files: Iterable[str]) -> list[Definition]:
    """Every function and method (classes nested any depth) of `files`."""
    found: list[Definition] = []
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        _collect(tree.body, "", rel, text.splitlines(), found)
    return found


def _collect(nodes: list[ast.stmt], prefix: str, rel: str, lines: list[str], found: list[Definition]) -> None:
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _first_line(node), node.end_lineno or node.lineno
            found.append(Definition(rel, prefix + node.name, start, end, " " * node.col_offset,
                                    "\n".join(lines[start - 1:end])))
        elif isinstance(node, ast.ClassDef):
            _collect(node.body, f"{prefix}{node.name}.", rel, lines, found)


def run_tests(root: Path, targets: list[str], timeout: int = TEST_TIMEOUT) -> TestRun:
    """pytest on `targets` (everything when empty): failing ids and the tail
    of its report."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(root / "src"), str(root), os.environ.get("PYTHONPATH", "")]).strip(os.pathsep),
        PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
               "--no-header", "-rfE", "--tb=short", *targets]
    try:
        done = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return TestRun([], 0, out[-6000:], timed_out=True)
    out = done.stdout + done.stderr
    failed = sorted(set(re.findall(r"^FAILED (\S+?)(?: - |$)", out, re.M)))
    errors = re.findall(r"^ERROR (\S+)", out, re.M)
    collect_error = done.returncode not in (0, 1) and not failed
    return TestRun(failed + [e for e in errors if e not in failed],
                   len(errors) + int(collect_error), out[-6000:])


_TEST_PATH = re.compile(r"[\w./\\-]*test[\w./\\-]*\.py(?:::[\w\[\]./-]+)*")
_TEST_NAME = re.compile(r"\btest_\w+")


def test_targets(request: str, root: Path, test_files: list[str]) -> list[str]:
    """The tests the request names (files, ids or bare test names)."""
    targets = []
    for match in _TEST_PATH.findall(request):
        path = match.replace("\\", "/").split("::")[0]
        if (root / path).is_file():
            targets.append(match.replace("\\", "/"))
    names = set(_TEST_NAME.findall(request))
    if names:
        for rel in test_files:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(re.search(rf"def {re.escape(name)}\b", text) for name in names):
                if not any(t.split("::")[0] == rel for t in targets):
                    targets.append(rel)
    return targets


def test_source(root: Path, test_id: str, limit: int = 60) -> str:
    """The source of one failing test, by its pytest id."""
    path, *parts = test_id.split("::")
    name = re.sub(r"\[.*$", "", parts[-1]) if parts else ""
    try:
        text = (root / path).read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return ""
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = lines[_first_line(node) - 1:node.end_lineno]
            return "\n".join(body[:limit])
    return ""


# ---------------------------------------------------------------- localization


_FRAME = re.compile(r"^([\w./\\-]+\.py):(\d+): in (\S+)", re.M)


def candidates(defs: list[Definition], failure: str, tests: list[str], request: str) -> list[tuple[Definition, str]]:
    """The functions worth showing the model, best first, each with why."""
    scores: dict[int, tuple[float, str]] = {}

    def add(index: int, score: float, why: str) -> None:
        if index not in scores or scores[index][0] < score:
            scores[index] = (score, why)

    by_file: dict[str, list[int]] = {}
    for index, item in enumerate(defs):
        by_file.setdefault(item.file, []).append(index)
    frames = _FRAME.findall(failure)
    for depth, (path, line, _func) in enumerate(frames):
        rel = path.replace("\\", "/").lstrip("./")
        for index in by_file.get(rel, []):
            item = defs[index]
            if item.start <= int(line) <= item.end:
                add(index, 10 + depth * 0.1, "在錯誤的呼叫堆疊裡")
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", "\n".join(tests)))
    attributes = set(re.findall(r"\.([A-Za-z_]\w*)", "\n".join(tests)))
    words = set(re.findall(r"[A-Za-z_]\w+", request))
    for index, item in enumerate(defs):
        if "NotImplementedError" in item.source and (item.short in called | attributes | words):
            add(index, 12, "還沒實作（raise NotImplementedError），測試會用到")
        if item.short in called:
            add(index, 6, "失敗的測試直接呼叫它")
        elif item.short in attributes:
            add(index, 5, "失敗的測試用到它")
        if item.short in words or item.name in request:
            add(index, 7, "請求裡提到它")
    ranked = sorted(scores.items(), key=lambda pair: -pair[1][0])
    return [(defs[index], why) for index, (_score, why) in ranked[:MAX_CANDIDATES]]


# ---------------------------------------------------------------- the model's two judgments


SYSTEM = ("你是程式修復助手。主機會幫你跑測試、找出相關的函式、把你寫的程式換進檔案。"
          "你只做被要求的那一件事，照指定格式回答，不要解釋。")


def _signature(item: Definition) -> str:
    lines = item.source.splitlines()
    head = next((n for n, line in enumerate(lines) if line.lstrip().startswith(("def ", "async def "))), 0)
    signature = []
    for line in lines[head:head + 6]:
        signature.append(line.strip())
        if line.rstrip().endswith(":"):
            break
    return " ".join(signature)


def choose_prompt(request: str, failure: str, tests: list[str], listed: list[tuple[Definition, str]]) -> str:
    rows = "\n".join(f"{n}. {item.file}  {item.name}  — {why}\n   {_signature(item)}"
                     for n, (item, why) in enumerate(listed, 1))
    return f"""使用者的請求：
{request}

失敗的測試：
{_fence(chr(10).join(tests)[:2500])}

測試輸出（節錄）：
{_fence(_tail(failure, 2500))}

可能要改的函式：
{rows}

要讓測試通過，該改哪幾個函式？最多 {MAX_CHOSEN} 個，最可能的放前面。
只輸出 JSON：{{"edit": [編號, ...]}}"""


def repair_prompt(request: str, failure: str, tests: list[str], chosen: list[Definition],
                  helpers: list[Definition], previous: str = "") -> str:
    current = "\n\n".join(f"# {item.file}  {item.name}\n{textwrap.dedent(item.source)}" for item in chosen)
    related = "\n\n".join(f"# {item.file}  {item.name}\n{textwrap.dedent(item.source)}" for item in helpers)
    again = (f"\n上一次改過之後，測試還是失敗：\n{_fence(_tail(previous, 2000))}\n"
             if previous else "")
    return f"""使用者的請求：
{request}

失敗的測試：
{_fence(chr(10).join(tests)[:3000])}

測試輸出（節錄）：
{_fence(_tail(failure, 2500))}
{again}
要改的函式（現在的樣子）：
{_fence(current)}
{"它用到的其他函式（參考，不用改）：" + chr(10) + _fence(related) if related else ""}

寫出改好的完整函式，讓失敗的測試通過、其他行為不變。
- 每個函式都寫完整（def 那一行到結尾），不要省略，不要寫測試。
- 需要新的輔助函式或 import 可以一起寫在同一個區塊。
只輸出一個 ```python 區塊。"""


def _fence(text: str) -> str:
    return f"```\n{text.strip()}\n```"


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…\n" + text[-limit:]


def parse_choice(reply: str, count: int) -> list[int]:
    match = re.search(r"\{.*\}", reply, re.S)
    numbers: list[int] = []
    if match:
        try:
            numbers = [int(n) for n in json.loads(match.group(0)).get("edit", [])]
        except (ValueError, TypeError, AttributeError):
            numbers = []
    if not numbers:
        numbers = [int(n) for n in re.findall(r"\d+", reply)[:MAX_CHOSEN]]
    seen = []
    for number in numbers:
        if 1 <= number <= count and number - 1 not in seen:
            seen.append(number - 1)
    return seen[:MAX_CHOSEN]


def code_block(reply: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply, re.S)
    if blocks:
        return max(blocks, key=len)
    return reply.strip()


# ---------------------------------------------------------------- splicing


def splice(root: Path, chosen: list[Definition], code: str) -> dict[str, str] | None:
    """New file texts with the model's functions in place of the chosen ones
    (matched by name, methods re-indented), new helper functions placed before
    the top-level statement that holds the first one, and missing imports
    added. None when the code does not parse or replaces nothing."""
    try:
        tree = ast.parse(textwrap.dedent(code))
    except SyntaxError:
        return None
    source_lines = textwrap.dedent(code).splitlines()
    imports, functions = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append("\n".join(source_lines[node.lineno - 1:node.end_lineno]))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, "\n".join(source_lines[_first_line(node) - 1:node.end_lineno])))
        elif isinstance(node, ast.ClassDef):
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    text = "\n".join(source_lines[_first_line(inner) - 1:inner.end_lineno])
                    functions.append((inner.name, textwrap.dedent(text)))
    targets = {item.short: item for item in chosen}
    replaced = {name: text for name, text in functions if name in targets}
    if not replaced:
        return None
    helpers = [text for name, text in functions if name not in targets]
    texts: dict[str, str] = {}
    for file in sorted({targets[name].file for name in replaced}):
        lines = (root / file).read_text(encoding="utf-8").splitlines()
        edits = sorted((targets[name] for name in replaced if targets[name].file == file),
                       key=lambda item: -item.start)
        for item in edits:
            body = textwrap.indent(textwrap.dedent(replaced[item.short]), item.indent).splitlines()
            lines[item.start - 1:item.end] = body
        text = "\n".join(lines) + "\n"
        if helpers and file == targets[next(iter(replaced))].file:
            text = _insert_helpers(text, targets[next(iter(replaced))], helpers)
        if imports:
            text = _add_imports(text, imports)
        try:
            ast.parse(text)
        except SyntaxError:
            return None
        texts[file] = text
    return texts


def _insert_helpers(text: str, anchor: Definition, helpers: list[str]) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        start, end = _first_line(node), node.end_lineno or node.lineno
        if start <= anchor.start <= end:
            lines = text.splitlines()
            block = "\n\n\n".join(textwrap.dedent(h) for h in helpers).splitlines() + ["", ""]
            lines[start - 1:start - 1] = block
            return "\n".join(lines) + "\n"
    return text


def _add_imports(text: str, imports: list[str]) -> str:
    lines = text.splitlines()
    missing = [line for line in imports if line.strip() not in {existing.strip() for existing in lines}]
    if not missing:
        return text
    tree = ast.parse(text)
    after = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or (
                isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
                and after == 0):
            after = node.end_lineno or node.lineno
        elif after:
            break
    lines[after:after] = missing
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the loop


@dataclass
class Setup:
    """What the host found before asking the model anything."""

    targets: list[str]  # the tests the request names (empty: the whole suite)
    first: TestRun  # the reproduction
    goal: list[str]  # the failing tests to fix
    tests: list[str]  # their sources
    defs: list[Definition]
    listed: list[tuple[Definition, str]]  # the candidates shown to the model


def reproduce(root: Path, request: str) -> Setup:
    """Run the tests the request names (or all of them) and list the
    functions the failure runs through."""
    files = python_files(root)
    test_files = [f for f in files if is_test(f)]
    defs = definitions(root, [f for f in files if not is_test(f)])
    targets = test_targets(request, root, test_files)
    first = run_tests(root, targets)
    if not first.failed and targets:
        first, targets = run_tests(root, []), []
    goal = first.failed[:10]
    tests = [t for t in (test_source(root, test_id) for test_id in goal[:3]) if t]
    listed = candidates(defs, first.output, tests, request) if goal else []
    return Setup(targets, first, goal, tests, defs, listed)


def messages(prompt: str) -> list[dict]:
    """A step's request as the model gets it (training rows use the same)."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]


def solve(root: Path, request: str, model: Model, *, samples: int = 4, rounds: int = 3,
          temperature: float = 0.8, deadline: float | None = None,
          log: Callable[[str], None] = lambda _: None) -> Result:
    """Make the failing tests the request is about pass, changing only
    functions outside the tests. The answer reports only what the host saw."""
    started = time.monotonic()
    setup = reproduce(root, request)
    trace: list[dict] = [{"step": "reproduce", "targets": setup.targets, "failed": setup.first.failed[:20]}]
    if not setup.goal:
        return Result("no_failure", "沒有重現出失敗的測試，無法用測試確認修改；沒有改任何檔案。",
                      trace=trace)
    if not setup.listed:
        return Result("unsolved", "找不到和失敗測試相關的函式；沒有改任何檔案。", trace=trace)
    reply = model.chat(messages(choose_prompt(request, setup.first.output, setup.tests, setup.listed)),
                       max_tokens=200)
    chosen = [setup.listed[i][0] for i in parse_choice(reply, len(setup.listed))] or [setup.listed[0][0]]
    trace.append({"step": "choose", "listed": [f"{d.file}:{d.name}" for d, _ in setup.listed],
                  "chosen": [f"{d.file}:{d.name}" for d in chosen]})
    originals = {item.file: (root / item.file).read_text(encoding="utf-8") for item in chosen}
    goal, first = setup.goal, setup.first
    best: tuple[int, str] | None = None  # failures left, their output
    attempts = 0
    for round_number in range(1, rounds + 1):
        prompt = repair_prompt(request, first.output, setup.tests, chosen, _helpers(setup.defs, chosen),
                               previous=best[1] if best else "")
        for sample in range(samples):
            if deadline is not None and time.monotonic() > deadline:
                break
            attempts += 1
            reply = model.chat(messages(prompt),
                               temperature=0.0 if sample == 0 and round_number == 1 else temperature,
                               max_tokens=3000)
            texts = splice(root, chosen, code_block(reply))
            if texts is None:
                trace.append({"step": "repair", "round": round_number, "result": "unusable"})
                continue
            for file, text in texts.items():
                (root / file).write_text(text, encoding="utf-8")
            check = run_tests(root, sorted({t.split("::")[0] for t in goal} | set(setup.targets)))
            left = check.failed
            trace.append({"step": "repair", "round": round_number, "left": len(left),
                          "errors": check.errors})
            if not left and not check.errors and not check.timed_out:
                whole = run_tests(root, [])
                new = sorted(whole.broken - first.broken) if not whole.timed_out else []
                if not new:
                    log(f"solved in round {round_number}, sample {sample + 1}")
                    edited = [d for d in chosen if d.source not in texts.get(d.file, "")]
                    return Result("solved", _report(goal, edited or chosen, whole), sorted(texts), round_number,
                                  attempts, model.calls, dict(model.tokens), trace)
                left, check = new, whole
            if best is None or len(left) < best[0]:
                best = (len(left), check.output)
            for file, text in originals.items():
                (root / file).write_text(text, encoding="utf-8")
        if deadline is not None and time.monotonic() > deadline:
            break
    trace.append({"step": "give_up", "seconds": round(time.monotonic() - started, 1)})
    return Result("unsolved", f"試了 {attempts} 個版本都沒讓測試全部通過；檔案已還原，沒有留下修改。",
                  [], rounds, attempts, model.calls, dict(model.tokens), trace)


def _helpers(defs: list[Definition], chosen: list[Definition], limit: int = 3) -> list[Definition]:
    """Repository functions the chosen ones call, for reference."""
    called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", "\n".join(item.source for item in chosen)))
    names = {item.name for item in chosen}
    found = [d for d in defs if d.short in called and d.name not in names and len(d.source) < 3000]
    return found[:limit]


def _report(goal: list[str], chosen: list[Definition], whole: TestRun) -> str:
    where = "、".join(f"{item.file} 的 {item.name}" for item in chosen)
    still = f"；整套測試還有 {len(whole.failed)} 個原本就失敗的" if whole.failed else "；整套測試都通過"
    return f"改了 {where}。原本失敗的 {len(goal)} 個測試現在都通過{still}。"
