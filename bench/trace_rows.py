"""Training rows from open agent trajectories in the mini-swe-agent harness
(nvidia/Open-SWE-Traces data/minisweagent; bench/mini_runner.py is the same
harness), hard-gated:

- the teacher is an open-weight model under a permissive license (metadata
  teacher_model.name): Qwen, DeepSeek, GLM. Never Claude or GPT (their terms
  forbid building competing models on their outputs) and never MiniMax (its
  license follows derived models);
- the trajectory resolved its task (resolved == 1) and ends by submitting;
- its repository is none of the benchmark's (--exclude-repos, SWE-bench
  Verified's list), so nothing the coding agent is tested on was trained.

Length is the trainer's to judge (SilentTalk train_lora.py drops rows over
--max-length and rows it cannot mark, and says how many). Rows are its
format: {"messages", "tools", "train": "all"}.

    python bench/trace_rows.py .runtime/agentdata/external/open-swe-traces/data/minisweagent/qwen36_27b \\
        --exclude-repos .runtime/swebench/verified-repos.json --out .runtime/agentdata/trace-rows-v1.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parent
OPEN_TEACHERS = re.compile(r"^(Qwen|DeepSeek|GLM)", re.IGNORECASE)
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def wire(messages: list[dict]) -> list[dict]:
    """The recorded messages in OpenAI wire form (what train_lora.py reads)."""
    out = []
    for message in messages:
        item = {"role": message["role"], "content": message.get("content") or ""}
        if message.get("tool_calls"):
            item["tool_calls"] = [{"id": call.get("id", ""), "type": "function",
                                   "function": {"name": call["function"]["name"],
                                                "arguments": call["function"]["arguments"]}}
                                  for call in message["tool_calls"]]
        out.append(item)
    return out


def submitted(messages: list[dict]) -> bool:
    last = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
    return bool(last and any(SUBMIT in (call["function"].get("arguments") or "")
                             for call in last.get("tool_calls") or []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exclude-repos", type=Path, required=True,
                        help="JSON list of owner/name repositories never to train on (the benchmark's)")
    parser.add_argument("--languages", default="python", help="comma list, or 'all'")
    parser.add_argument("--sample", type=int, default=0, help="keep this many rows at random (seed 0); 0: all")
    args = parser.parse_args()
    import pyarrow.parquet as pq

    languages = None if args.languages == "all" else set(args.languages.split(","))
    excluded = {r.lower() for r in json.loads(args.exclude_repos.read_text())}
    why: Counter = Counter()
    seen: set[str] = set()
    rows: list[str] = []
    for path in sorted(p for folder in args.folders for p in folder.rglob("*.parquet")):
        table = pq.read_table(path, columns=["instance_id", "repo", "language", "messages", "tools",
                                             "resolved", "metadata"])
        for row in table.to_pylist():
            teacher = ((row.get("metadata") or {}).get("teacher_model") or {}).get("name") or ""
            if not OPEN_TEACHERS.match(teacher):
                why[f"teacher {teacher or '?'}"] += 1
                continue
            if row["resolved"] != 1:
                why["not resolved"] += 1
                continue
            if languages and row["language"] not in languages:
                why["language"] += 1
                continue
            if row["repo"].lower() in excluded:
                why["benchmark repository"] += 1
                continue
            messages = wire(row["messages"])
            if not submitted(messages):
                why["did not submit"] += 1
                continue
            key = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
            if key in seen:
                why["duplicate"] += 1
                continue
            seen.add(key)
            rows.append(json.dumps({"messages": messages, "tools": [json.loads(t) for t in row["tools"]],
                                    "train": "all",
                                    "source": {"instance": row["instance_id"], "repo": row["repo"],
                                               "teacher": teacher}}, ensure_ascii=False))
    if args.sample and len(rows) > args.sample:
        why["not sampled"] += len(rows) - args.sample
        rows = random.Random(0).sample(rows, args.sample)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(row + "\n" for row in rows), encoding="utf-8")
    kept = len(rows)
    stamp = {"producer": "edgeagent-trace-rows",
             "version": hashlib.sha256((BENCH / "mini_templates.json").read_bytes()).hexdigest()[:16],
             "folders": [str(f) for f in args.folders], "excluded_repos": sorted(excluded),
             "sample": args.sample}
    args.out.with_name(args.out.name + ".version").write_text(json.dumps(stamp), encoding="utf-8")
    print(f"{kept} rows -> {args.out}; skipped {dict(why)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
