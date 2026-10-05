"""python -m edge_agent.toolbox describe | run NAME | preview NAME  (params as JSON on stdin)

The Agent runs this from the workspace folder: describe prints every tool's
spec as JSON; run and preview read one JSON object of parameters on stdin and
print the result text (exit 0), or why the tool refused (exit 2).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from edge_agent.toolbox import REGISTRY, ToolError

SPEC_FIELDS = ("name", "summary", "keywords", "parameters", "kind", "destructive", "windows_only")


def describe() -> list[dict]:
    return [{field: getattr(tool, field) for field in SPEC_FIELDS} for tool in REGISTRY.values()]


def main(argv: list[str] | None = None) -> int:
    sys.stdin.reconfigure(encoding="utf-8-sig")  # a BOM from a Windows pipe is not part of the JSON
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    if args == ["describe"]:
        print(json.dumps(describe(), ensure_ascii=False, indent=1))
        return 0
    if len(args) != 2 or args[0] not in ("run", "preview"):
        print("用法：python -m edge_agent.toolbox describe | run 工具名稱 | preview 工具名稱（參數用 JSON 從 stdin 給）")
        return 2
    action, name = args
    tool = REGISTRY.get(name)
    if tool is None:
        print(f"沒有叫做「{name}」的工具。有：{'、'.join(REGISTRY)}")
        return 2
    raw = sys.stdin.read().strip()
    try:
        params = json.loads(raw) if raw else {}
    except ValueError as exc:
        print(f"參數不是有效的 JSON：{exc}")
        return 2
    fn = tool.run if action == "run" else tool.preview
    if fn is None:
        print(f"{name} 只讀取資料、不會改變任何東西，不需要預覽；直接用 run。")
        return 2
    try:
        print(fn(params, Path.cwd()))
    except ToolError as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
