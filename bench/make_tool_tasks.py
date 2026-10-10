"""Training tasks for the tool catalog and for who may ask what.

Each task is a workspace, a situation for the teacher and a program check;
the request the Agent sees is written later by an open-weight teacher
(`describe`), never by Claude (dev-box rule 5). Kinds:
  train-tool   a chore or question a catalog tool answers (organize, rename
               photos, convert text, find big or duplicate files, archive,
               merge tables, logs, backups, git, code search, calculation,
               system status, a small fix verified by run_checks);
  train-scope  requests the task's scope forbids (a public chat asking for the
               owner's files, to run code, other chats; a read-only task asked
               to change things) and allowed public work, plus instructions
               planted in files.
Tools held out for the eval (bench/tasks: date_calc, zip_files, unzip_file)
never appear here, so the eval measures tools training never showed. Names,
folders and numbers differ from the eval tasks.

Generators per kind live in tool_tasks_chores, tool_tasks_mcp and
tool_tasks_scope; tool_tasks_writer writes a task to disk.

    python bench/make_tool_tasks.py cut --per-kind 14
    python bench/make_tool_tasks.py describe --url http://127.0.0.1:18240
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_tasks_chores import (  # noqa: E402
    gen_archive,
    gen_backup,
    gen_calc,
    gen_convert,
    gen_duplicates,
    gen_find_code,
    gen_fix,
    gen_git,
    gen_large,
    gen_log,
    gen_merge,
    gen_organize,
    gen_rename_photos,
    gen_sysinfo,
)
from tool_tasks_mcp import gen_mcp  # noqa: E402
from tool_tasks_memory import gen_chat, gen_memory  # noqa: E402
from tool_tasks_scope import (  # noqa: E402
    gen_injection,
    gen_public_ok,
    gen_public_refuse,
    gen_read_refuse,
)

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".runtime/agentdata/tool-tasks"


GENERATORS = {
    "organize": gen_organize, "rename_photos": gen_rename_photos, "convert": gen_convert,
    "large": gen_large, "duplicates": gen_duplicates, "archive": gen_archive, "merge": gen_merge,
    "log": gen_log, "backup": gen_backup, "git": gen_git, "fix": gen_fix, "find_code": gen_find_code,
    "calc": gen_calc, "sysinfo": gen_sysinfo, "public_refuse": gen_public_refuse,
    "public_ok": gen_public_ok, "read_refuse": gen_read_refuse, "injection": gen_injection,
    "mcp": gen_mcp, "memory": gen_memory, "chat": gen_chat,
}


def cut(per_kind: int, seed: int) -> None:
    rng = random.Random(seed)
    TASKS.mkdir(parents=True, exist_ok=True)
    for kind, make in GENERATORS.items():
        for i in range(per_kind):
            make(rng).write(TASKS, f"{kind}-{i:02d}")
    print(f"{len(list(TASKS.glob('*/task.json')))} tasks in {TASKS}")


DESCRIBE_SYSTEM = (
    "你在幫忙產生電腦助理「機魂」的練習題。給你一段情境，請寫出那個人實際會打或說給機魂聽的一句話或一小段話，用台灣繁體中文。"
)
STYLES = ("很簡短，像在聊天室隨手打的", "有禮貌、交代清楚", "有點急", "口語，夾雜一兩個英文詞", "先說自己在幹嘛，再說要什麼",
          "像在跟朋友說話", "只講結果要什麼，不講原因")


def describe_prompt(spec: dict, style: str) -> str:
    must = "、".join(spec.get("must_mention") or []) or "（無）"
    return f"""情境：{spec["situation"]}

寫法：{style}。
規則：
1. 只寫那個人說的話本身，不要旁白，不要加引號。
2. 一定要提到這些字：{must}。
3. 不要說要用哪個工具或指令，不要教機魂怎麼做；不要提到「情境」「練習」「題目」。
4. 一到三句。
只輸出 JSON：{{"request": "..."}}"""


def describe(url: str, workers: int, seed: int) -> None:
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
                         {"role": "user", "content": describe_prompt(task["spec"], style)}],
            "temperature": 0.9, "max_tokens": 300, "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}).encode()
        request = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", body,
                                         {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                content = json.loads(response.read())["choices"][0]["message"]["content"]
            text = str(json.loads(content)["request"]).strip()
        except (OSError, ValueError, KeyError, TypeError):
            return False
        must = task["spec"].get("must_mention") or []
        for m in must:
            # A pattern quoted with its backslashes doubled or eaten by the
            # JSON is put back exactly; anything else missing refuses it.
            if m not in text and "\\" in m:
                text = re.sub(r"\\{2,}", lambda _: "\\", text)
                if m not in text and m.replace("\\", "") in text:
                    text = text.replace(m.replace("\\", ""), m)
        if not 4 <= len(text) <= 300 or not all(m in text for m in must) or any(
                tool in text for tool in ("tool", "工具", "run_checks", "archive_files")):
            return False
        task["prompt"], task["prompt_style"] = text, style
        path.write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return True

    for _ in range(3):  # a refused request is written again with another try
        with ThreadPoolExecutor(workers) as executor:
            done = sum(executor.map(one, pending))
        pending = [(p, t, s) for p, t, s in pending
                   if not json.loads(p.read_text(encoding="utf-8")).get("prompt")]
        print(f"described {done}; {len(pending)} left")
        if not pending:
            break


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("cut")
    c.add_argument("--per-kind", type=int, default=14)
    c.add_argument("--seed", type=int, default=11)
    d = sub.add_parser("describe")
    d.add_argument("--url", required=True, help="teacher llama-server base URL")
    d.add_argument("--workers", type=int, default=4)
    d.add_argument("--seed", type=int, default=5)
    args = parser.parse_args()
    if args.command == "cut":
        cut(args.per_kind, args.seed)
    else:
        describe(args.url, args.workers, args.seed)


if __name__ == "__main__":
    main()
