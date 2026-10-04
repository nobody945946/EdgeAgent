"""Training rows from recorded bench runs (run.py --record), hard-gated.

A job becomes a row only if it passed with no grade problem (hidden tests
pass, nothing it must not touch was changed, no test tampering) and ended
normally. The row is the last request the Agent sent with its tools (the
exact system prompt, tool definitions and history the model saw; Pi's own
summary requests carry no tools and are skipped) plus the final answer, in
train_lora.py's format: {"messages", "tools", "train": "all"}.

Eval tasks (bench/tasks) never become training rows: only runs over
.runtime/agentdata/tasks are accepted.

One output holds one product version: every row's system prompt and tool
definitions must be the same (runs recorded under different product code
would teach two). Otherwise nothing is written (exit 3). The output gets a
sidecar <out>.version, the stamp SilentTalk's train_lora.py requires.

    python bench/dataset.py .runtime/bench/<run> [...] --out .runtime/agentdata/agent-rows.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_TASKS = {p.name for p in (ROOT / "bench/tasks").iterdir() if p.is_dir()}
sys.path.insert(0, "/home/clawd/SilentTalk")
try:
    from silenttalk.copilot.script import to_taiwan_traditional
except ImportError:  # no converter: keep the text as the teacher wrote it
    def to_taiwan_traditional(text: str) -> str:
        return text


def final_answer(trajectory: list[dict]) -> dict | None:
    """The Agent's last assistant message in wire format (text only: a
    finished job ends with its report, not a tool call)."""
    for message in reversed(trajectory):
        if message.get("role") != "assistant":
            continue
        items = message.get("content") or []
        if any(isinstance(i, dict) and i.get("type") == "toolCall" for i in items):
            return None
        text = "".join(i.get("text", "") for i in items if isinstance(i, dict) and i.get("type") == "text")
        return {"role": "assistant", "content": text} if text.strip() else None
    return None


def rows(run_dir: Path) -> tuple[list[dict], dict[str, int]]:
    kept, why = [], {}

    def skip(reason: str) -> None:
        why[reason] = why.get(reason, 0) + 1

    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        job = f"{result['task']}-r{result['rep']}"
        if result["task"] in EVAL_TASKS:
            skip("eval task")
            continue
        if not result["passed"] or result["grade"].get("problems") or result.get("outcome") != "done":
            skip("not a clean pass")
            continue
        records = sorted((run_dir / "records" / job).glob("*.json"))
        requests = [json.loads(p.read_text(encoding="utf-8")) for p in records]
        requests = [r for r in requests if r.get("tools")]
        trajectory_path = run_dir / "traj" / f"{job}.json"
        if not requests or not trajectory_path.is_file():
            skip("no recording")
            continue
        answer = final_answer(json.loads(trajectory_path.read_text(encoding="utf-8")))
        if answer is None:
            skip("no final answer")
            continue
        last = requests[-1]
        messages = [dict(m) for m in last["messages"]]
        for message in messages:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                message["content"] = to_taiwan_traditional(message["content"])
        kept.append({"messages": [*messages, answer], "tools": last["tools"], "train": "all",
                     "source": {"run": run_dir.name, "job": job, "kind": result.get("kind", "")}})
    return kept, why


# Pi adds the workspace's own AGENTS.md to the system prompt (the product does
# the same for the user's folders): per-project content, not a product version.
PROJECT_CONTEXT = re.compile(r"<project_context>.*?</project_context>\n*", re.DOTALL)


def product_version(row: dict) -> str:
    """The product as the model saw it: system prompt (without the
    workspace's own AGENTS.md) and tool definitions."""
    system = [PROJECT_CONTEXT.sub("", str(m["content"])) for m in row["messages"] if m.get("role") == "system"]
    blob = json.dumps([system, row["tools"]], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    total: list[dict] = []
    for run_dir in args.runs:
        kept, why = rows(run_dir)
        print(f"{run_dir.name}: {len(kept)} rows; skipped {why}")
        total += kept
    versions = Counter(product_version(row) for row in total)
    if len(versions) > 1:
        by_run = Counter((row["source"]["run"], product_version(row)) for row in total)
        print(f"REFUSED: rows from {len(versions)} product versions would mix: {dict(by_run)}")
        return 3
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in total:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with_project = sum(bool(PROJECT_CONTEXT.search(str(row["messages"][0]["content"]))) for row in total)
    stamp = {"producer": "edgeagent-dataset", "version": next(iter(versions), ""),
             "runs": [run.name for run in args.runs],
             "rows_with_project_agents_md": with_project}
    args.out.with_name(args.out.name + ".version").write_text(json.dumps(stamp), encoding="utf-8")
    print(f"{len(total)} rows -> {args.out} (product version {stamp['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
