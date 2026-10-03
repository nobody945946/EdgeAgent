"""Cut training tasks from open-source repositories (never from bench repos).

Two kinds, both checked by the repository's own tests:
  bugfix     one small AST mutation inside a function (comparison, boolean,
             off-by-one, negated condition, swapped return) that makes 1-10
             tests fail;
  implement  a function body replaced by NotImplementedError (docstring kept)
             that makes 1-15 tests fail.
Every candidate runs in the training sandbox; only candidates whose failure
set is in range, with no collection errors, become tasks. The request text is
written later by an open-weight teacher from the stored spec (`describe`).

    python bench/make_tasks.py cut --repos boltons,parse --per-repo 120
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import re
import shutil
import subprocess
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
DATA = ROOT / ".runtime/agentdata"
REPOS = DATA / "repos"
TASKS = DATA / "tasks"
IMAGE = "edgeagent-train:1"
SKIP_DIRS = {"tests", "test", "docs", "doc", "examples", "benchmarks", "scripts", "misc",
             "tools", ".github", "build", "dist"}
COMPARE_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
                ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
                ast.In: ast.NotIn, ast.NotIn: ast.In}


def is_test(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) or \
        parts[-1].startswith("test_") or parts[-1].endswith("_test.py") or parts[-1] == "conftest.py"


def source_files(repo: Path) -> list[Path]:
    found = []
    for path in sorted(repo.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        if is_test(rel) or path.name in ("setup.py", "conftest.py", "noxfile.py") \
                or rel.count("/") == 0 and path.name.startswith(("_version", "version")):
            continue
        found.append(path)
    return found


def offsets(text: str) -> list[int]:
    """Character offset of each line start (1-based line numbers)."""
    starts, total = [0, 0], 0
    for line in text.splitlines(keepends=True):
        total += len(line)
        starts.append(total)
    return starts


def span(text: str, starts: list[int], node: ast.AST) -> tuple[int, int]:
    """Character span of a node; ast columns are UTF-8 byte offsets."""
    def char(line: int, col: int) -> int:
        row = text[starts[line]:starts[line + 1]]
        return starts[line] + len(row.encode("utf-8")[:col].decode("utf-8", "ignore"))
    return char(node.lineno, node.col_offset), char(node.end_lineno, node.end_col_offset)


def mutations(func: ast.FunctionDef) -> list[tuple[ast.AST, ast.AST, str]]:
    """(original node, replacement node, operator name) inside one function."""
    found = []
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                   ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE_SWAP:
            new = ast.Compare(node.left, [COMPARE_SWAP[type(node.ops[0])]()], node.comparators)
            found.append((node, new, "compare"))
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            other = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            found.append((node, ast.BoolOp(other, node.values), "boolop"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)) \
                and isinstance(node.right, ast.Constant) and node.right.value == 1:
            found.append((node, node.left, "off_by_one"))
        elif isinstance(node, (ast.If, ast.While)) and not isinstance(node.test, ast.UnaryOp):
            found.append((node.test, ast.UnaryOp(ast.Not(), node.test), "negate"))
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, bool):
            found.append((node.value, ast.Constant(not node.value.value), "return_bool"))
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            bound = node.slice.upper or node.slice.lower
            if isinstance(bound, ast.Name):
                shifted = ast.BinOp(bound, ast.Sub(), ast.Constant(1))
                found.append((bound, shifted, "slice_bound"))
    return found


def functions(tree: ast.Module) -> list[ast.FunctionDef]:
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__"):
            result.append(node)
    return result


def candidates(repo: Path, rng: random.Random) -> list[dict]:
    found = []
    for path in source_files(repo):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        starts = offsets(text)
        rel = path.relative_to(repo).as_posix()
        for func in functions(tree):
            sites = mutations(func)
            rng.shuffle(sites)
            for original, replacement, operator in sites[:2]:
                a, b = span(text, starts, original)
                new = ast.unparse(replacement)
                if isinstance(original, ast.expr) and not new.startswith("("):
                    new = f"({new})"
                found.append({"kind": "bugfix", "file": rel, "function": func.name,
                              "operator": operator, "text": text[:a] + new + text[b:]})
            body = func.body
            doc = ast.get_docstring(func) is not None
            stmts = body[1:] if doc else body
            if stmts and len(stmts) >= 2 and func.end_lineno - func.lineno >= 5:
                a, _ = span(text, starts, stmts[0])
                _, b = span(text, starts, stmts[-1])
                indent = " " * stmts[0].col_offset
                stub = "raise NotImplementedError"
                line_start = text.rfind("\n", 0, a) + 1
                found.append({"kind": "implement", "file": rel, "function": func.name,
                              "operator": "remove_body",
                              "text": text[:line_start] + indent + stub + text[b:]})
    rng.shuffle(found)
    return found


def run_tests(repo: Path, rel: str, text: str | None, timeout: int = 300) -> dict:
    """Run the repository's tests in the training sandbox, optionally with one
    file replaced. Returns failed test ids and whether collection broke."""
    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "file.py"
        mounts = ["-v", f"{repo}:/src:ro"]
        copy = "cp -r /src /tmp/work && rm -rf /tmp/work/.git"
        if text is not None:
            overlay.write_text(text, encoding="utf-8")
            mounts += ["-v", f"{overlay}:/overlay.py:ro"]
            copy += f" && cp /overlay.py '/tmp/work/{rel}'"
        script = (f"{copy} && cd /tmp/work && PYTHONPATH=/tmp/work/src:/tmp/work "
                  f"timeout {timeout} python -m pytest -q -p no:cacheprovider -o addopts= "
                  "--no-header -rfE 2>&1 | tail -80")
        out = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--tmpfs", "/tmp:rw,exec,size=1g", "--tmpfs", "/home/bench:rw,mode=1777",
             "-e", "HOME=/home/bench", "--user", "1012:1012", "--memory", "2g",
             "--pids-limit", "256", *mounts, IMAGE, "sh", "-c", script],
            capture_output=True, text=True, timeout=timeout + 60).stdout
    failed = sorted(set(re.findall(r"^FAILED (\S+?)(?: - |$)", out, re.M)))
    errors = re.findall(r"^ERROR (\S+)", out, re.M)
    summary = out.strip().splitlines()[-1] if out.strip() else ""
    return {"failed": failed, "errors": len(errors) + ("error" in summary and not failed),
            "summary": summary, "excerpt": "\n".join(
                line for line in out.splitlines() if line.startswith(("E ", "FAILED")))[:1500]}


def cut(names: list[str], per_repo: int, seed: int, workers: int) -> None:
    rng = random.Random(seed)
    TASKS.mkdir(parents=True, exist_ok=True)
    for name in names:
        repo = REPOS / name
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        clean = run_tests(repo, "", None)
        if clean["failed"] or clean["errors"]:
            print(f"{name}: tests not clean ({clean['summary']}), skipped")
            continue
        pool = candidates(repo, rng)
        wanted = {"bugfix": per_repo * 2 // 3, "implement": per_repo - per_repo * 2 // 3}
        made = {"bugfix": 0, "implement": 0}
        tests = sorted({p.relative_to(repo).as_posix() for p in repo.rglob("*.py")
                        if is_test(p.relative_to(repo).as_posix())})

        def check(candidate: dict, repo: Path = repo) -> tuple[dict, dict]:
            return candidate, run_tests(repo, candidate["file"], candidate["text"])

        index = 0
        with ThreadPoolExecutor(workers) as executor:
            while index < len(pool) and any(made[k] < wanted[k] for k in made):
                batch = [c for c in pool[index:index + workers * 2] if made[c["kind"]] < wanted[c["kind"]]]
                index += workers * 2
                for candidate, result in executor.map(check, batch):
                    kind = candidate["kind"]
                    limit = 10 if kind == "bugfix" else 15
                    if made[kind] >= wanted[kind] or result["errors"] or not 1 <= len(result["failed"]) <= limit:
                        continue
                    made[kind] += 1
                    write_task(name, base, candidate, result, tests, repo)
        print(f"{name}: {made} from {index} candidates", flush=True)


def write_task(name: str, base: str, candidate: dict, result: dict, tests: list[str],
               repo: Path) -> None:
    digest = hashlib.sha256(candidate["text"].encode("utf-8")).hexdigest()[:8]
    task_id = f"{name}-{candidate['kind']}-{candidate['function']}-{digest}"
    task_dir = TASKS / task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "workspace" / candidate["file"]).parent.mkdir(parents=True)
    (task_dir / "workspace" / candidate["file"]).write_text(candidate["text"], encoding="utf-8")
    (task_dir / "solution" / candidate["file"]).parent.mkdir(parents=True)
    shutil.copyfile(repo / candidate["file"], task_dir / "solution" / candidate["file"])
    files = sorted({test.split("::")[0] for test in result["failed"]})
    task = {
        "kind": "train-" + candidate["kind"], "repo": name, "base": base, "git": True,
        "image": IMAGE, "prompt": "",
        "spec": {"kind": candidate["kind"], "file": candidate["file"],
                 "function": candidate["function"], "operator": candidate["operator"],
                 "failing_tests": result["failed"], "error_excerpt": result["excerpt"]},
        "keep": tests, "check": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 "-o", "addopts=", *files],
        "timeout_s": 1200,
    }
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")


DESCRIBE_SYSTEM = (
    "你在幫忙產生寫程式助理的練習題。給你一份題目規格（哪個函式有問題、哪些測試失敗），"
    "請寫出一位台灣開發者實際會打給他的程式助理的一段請求，用台灣繁體中文。"
)
STYLES = ("很簡短，像在聊天室隨手打的", "有禮貌、交代清楚", "有點急、只描述症狀",
          "像在回報 issue，附上失敗的測試名稱", "口語，夾雜一兩個英文技術詞",
          "先說自己在做什麼專案，再說遇到的問題")


def describe_prompt(task: dict, style: str) -> str:
    spec = task["spec"]
    what = ("這個函式的實作被拿掉了，要請助理依照函式名稱、docstring 和測試把它實作出來"
            if spec["kind"] == "implement" else "這個函式有一個 bug，導致下面的測試失敗")
    return f"""題目規格：
- 專案：{task["repo"]}（Python 開源函式庫）
- 檔案：{spec["file"]}
- 函式：{spec["function"]}
- 狀況：{what}
- 失敗的測試：{", ".join(spec["failing_tests"][:6])}
- 錯誤訊息節錄：
{spec["error_excerpt"][:900]}

寫法：{style}。
規則：
1. 只描述現象和要做的事，不要說出要怎麼修、錯在哪一行或正確的程式碼。
2. 可以提檔名、函式名或測試名，但不一定全部都提；不要提到「題目」「規格」「練習」。
3. 一到四句，不要用條列。
只輸出 JSON：{{"request": "..."}}"""


def describe(url: str, workers: int, seed: int) -> None:
    """Fill each task's prompt with a request written by an open-weight
    teacher; the spec itself never reaches the agent."""
    rng = random.Random(seed)
    pending = []
    for path in sorted(TASKS.glob("*/task.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        if not task.get("prompt"):
            pending.append((path, task, rng.choice(STYLES)))

    def one(item: tuple[Path, dict, str]) -> bool:
        path, task, style = item
        body = json.dumps({
            "messages": [{"role": "system", "content": DESCRIBE_SYSTEM},
                         {"role": "user", "content": describe_prompt(task, style)}],
            "temperature": 0.9, "max_tokens": 400,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}).encode()
        request = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", body,
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                content = json.loads(response.read())["choices"][0]["message"]["content"]
            text = str(json.loads(content)["request"]).strip()
        except (OSError, ValueError, KeyError, TypeError):
            return False
        original = (REPOS / task["repo"] / task["spec"]["file"]).read_text(encoding="utf-8")
        mutated = task_dir_file(path) or ""
        # Reject requests that leak the fix: a line of the original code that
        # the mutation changed must not appear in the request.
        changed = set(original.splitlines()) - set(mutated.splitlines())
        if not 8 <= len(text) <= 400 or any(
                len(line.strip()) > 12 and line.strip() in text for line in changed):
            return False
        task["prompt"] = text
        task["prompt_style"] = style
        path.write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return True

    with ThreadPoolExecutor(workers) as executor:
        done = sum(executor.map(one, pending))
    print(f"described {done}/{len(pending)} tasks")


def task_dir_file(task_json: Path) -> str | None:
    task = json.loads(task_json.read_text(encoding="utf-8"))
    path = task_json.parent / "workspace" / task["spec"]["file"]
    return path.read_text(encoding="utf-8") if path.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("cut")
    c.add_argument("--repos", required=True)
    c.add_argument("--per-repo", type=int, default=120)
    c.add_argument("--seed", type=int, default=1)
    c.add_argument("--workers", type=int, default=12)
    d = sub.add_parser("describe")
    d.add_argument("--url", required=True, help="teacher llama-server base URL")
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--seed", type=int, default=3)
    args = parser.parse_args()
    if args.command == "describe":
        describe(args.url, args.workers, args.seed)
    if args.command == "cut":
        cut(args.repos.split(","), args.per_repo, args.seed, args.workers)


if __name__ == "__main__":
    main()
