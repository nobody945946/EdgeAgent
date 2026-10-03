"""Training rows from recorded bench runs (run.py --record), hard-gated.

A job becomes a row only if it passed with no grade problem (hidden tests
pass, nothing it must not touch was changed, no test tampering) and ended
normally. The row is the last request the Agent sent with its tools (the
exact system prompt, tool definitions and history the model saw; Pi's own
summary requests carry no tools and are skipped) plus the final answer, in
train_lora.py's format: {"messages", "tools", "train": "all"}.

Eval tasks (bench/tasks) never become training rows: only runs over
.runtime/agentdata/tasks are accepted.

    python bench/dataset.py .runtime/bench/<run> [...] --out .runtime/agentdata/agent-rows.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    total: list[dict] = []
    for run_dir in args.runs:
        kept, why = rows(run_dir)
        print(f"{run_dir.name}: {len(kept)} rows; skipped {why}")
        total += kept
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in total:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(total)} rows -> {args.out}")


if __name__ == "__main__":
    main()
