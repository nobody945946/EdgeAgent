"""code_outline: the functions and classes of code files, or the code a query is about.

Python is read with its own parser (a simple match where it does not
parse); JavaScript/TypeScript and PowerShell by line patterns. Dependency and
build folders are skipped.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from edge_agent.toolbox import Tool, register, resolve_inside
from edge_agent.toolbox.fs import is_hidden, list_files, shown

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
PS_RULE = re.compile(r"^\s*(function|filter|class)\s+([\w-]+)", re.IGNORECASE)


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
