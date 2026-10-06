"""Training rows from open agent trajectories in the mini-swe-agent harness
(nvidia/Open-SWE-Traces data/minisweagent; bench/mini_runner.py is the same
harness), hard-gated:

- the teacher is an open-weight model under a permissive license (metadata
  teacher_model.name): Qwen, DeepSeek, GLM. Never Claude or GPT (their terms
  forbid building competing models on their outputs) and never MiniMax (its
  license follows derived models);
- the trajectory resolved its task (resolved == 1) and ends by submitting;
- its repository is none of the held-out evaluation repositories
  (make_tasks.HELD_OUT) nor of the external benchmark (--exclude-repos, a
  JSON list such as SWE-bench Verified's), so no test is something trained;
- SilentTalk's train_lora.py can train it (its tokenize_row, the owner of
  what a trainable row is) within --max-tokens (longer rows are dropped,
  never cut).

Rows are train_lora.py's format: {"messages", "tools", "train": "all"}.

    python bench/trace_rows.py .runtime/agentdata/external/open-swe-traces/data/minisweagent/qwen36_27b \\
        --out .runtime/agentdata/trace-rows-v1.jsonl --tokenizer <hf model dir> --max-tokens 32768
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
from make_tasks import HELD_OUT  # noqa: E402

TRAINER = Path("/home/clawd/SilentTalk/scripts/dev")

OPEN_TEACHERS = re.compile(r"^(Qwen|DeepSeek|GLM)", re.IGNORECASE)
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def held_out(repo: str) -> bool:
    name = repo.rsplit("/", 1)[-1].lower()
    return name in HELD_OUT or name.removeprefix("python-") in HELD_OUT or f"python-{name}" in HELD_OUT


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


def convert(job: tuple[Path, Path, str, int, list[str] | None, list[str]]) -> tuple[list[str], dict]:
    """One parquet file -> its rows (JSON lines) and the reasons it skipped
    the others. Runs in a worker process."""
    path, tokenizer_dir, trainer, max_tokens, languages, excluded = job
    import pyarrow.parquet as pq
    from transformers import AutoTokenizer

    sys.path.insert(0, trainer)
    import train_lora

    tokenizer = _TOKENIZERS.get(str(tokenizer_dir)) or _TOKENIZERS.setdefault(
        str(tokenizer_dir), AutoTokenizer.from_pretrained(str(tokenizer_dir)))
    why: Counter = Counter()
    lines = []
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
        if held_out(row["repo"]) or row["repo"].lower() in excluded:
            why["held-out repository"] += 1
            continue
        messages = wire(row["messages"])
        if not submitted(messages):
            why["did not submit"] += 1
            continue
        # Code runs about 3-4 characters a token: far beyond the limit in
        # characters is beyond it in tokens; only the rest is tokenized.
        if sum(len(m["content"]) + sum(len(c["function"]["arguments"]) for c in m.get("tool_calls") or [])
               for m in messages) > max_tokens * 6:
            why["too long"] += 1
            continue
        tools = [json.loads(t) for t in row["tools"]]
        try:
            length = len(train_lora.tokenize_row(tokenizer, {"messages": messages, "tools": tools,
                                                              "train": "all"})["input_ids"])
        except ValueError:  # the trainer cannot mark the turns: no row
            why["not trainable"] += 1
            continue
        if length > max_tokens:
            why["too long"] += 1
            continue
        lines.append(json.dumps({"messages": messages, "tools": tools, "train": "all",
                                 "source": {"instance": row["instance_id"], "repo": row["repo"],
                                            "teacher": teacher, "tokens": length}}, ensure_ascii=False))
    return lines, dict(why)


_TOKENIZERS: dict = {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--languages", default="python", help="comma list, or 'all'")
    parser.add_argument("--exclude-repos", type=Path, help="JSON list of owner/name repositories never to train on")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    from multiprocessing import Pool

    languages = None if args.languages == "all" else sorted(args.languages.split(","))
    excluded = sorted(r.lower() for r in json.loads(args.exclude_repos.read_text())) if args.exclude_repos else []
    files = sorted(p for folder in args.folders for p in folder.rglob("*.parquet"))
    jobs = [(path, Path(args.tokenizer), str(TRAINER), args.max_tokens, languages, excluded) for path in files]
    why: Counter = Counter()
    seen: set[str] = set()
    kept = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle, Pool(args.workers) as pool:
        for path, (lines, reasons) in zip(files, pool.imap(convert, jobs), strict=True):
            why.update(reasons)
            for line in lines:
                key = hashlib.sha256(json.dumps(json.loads(line)["messages"], sort_keys=True).encode()).hexdigest()
                if key in seen:
                    why["duplicate"] += 1
                    continue
                seen.add(key)
                handle.write(line + "\n")
                kept += 1
            print(f"{path.parent.name}/{path.name}: {kept} kept so far", flush=True)
    stamp = {"producer": "edgeagent-trace-rows", "version": hashlib.sha256(
        (BENCH / "mini_templates.json").read_bytes()).hexdigest()[:16],
        "folders": [str(f) for f in args.folders], "max_tokens": args.max_tokens,
        "excluded_repos": excluded}
    args.out.with_name(args.out.name + ".version").write_text(json.dumps(stamp), encoding="utf-8")
    print(f"{kept} rows -> {args.out}; skipped {dict(why)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
