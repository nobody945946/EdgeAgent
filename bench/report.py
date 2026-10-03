"""Summarize bench runs and compare two configurations task by task.

    python bench/report.py qwen35-4b                 # one run
    python bench/report.py qwen35-4b qwen35-4b-search  # paired comparison

Pass rates are per kind (code, chore, trap, repo). A paired comparison uses
only tasks both runs finished, and counts tasks where one side passed more
often than the other (sign test), since absolute rates move with the task mix.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / ".runtime/bench"


def load(tag: str) -> list[dict]:
    path = STATE / tag / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def rates(rows: list[dict]) -> dict[str, float]:
    by_task: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row["passed"])
    return {task: sum(v) / len(v) for task, v in by_task.items()}


def summary(tag: str) -> None:
    rows = load(tag)
    print(f"== {tag}: {len(rows)} runs, {len({r['task'] for r in rows})} tasks")
    kinds: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        kinds[row["kind"]].append(row)
    for kind, group in sorted(kinds.items()) + [("all", rows)]:
        passed = sum(r["passed"] for r in group)
        seconds = sorted(r.get("seconds", r["wall"]) for r in group)
        tokens = [r.get("usage", {}).get("output", 0) for r in group]
        print(f"  {kind:6} pass {passed:3}/{len(group):3} = {passed / len(group):5.1%}"
              f"  median {seconds[len(seconds) // 2]:6.1f}s"
              f"  output tokens/run {sum(tokens) / len(group):7.0f}")
    outcomes = Counter(r.get("outcome") for r in rows)
    print("  outcomes:", dict(outcomes))
    stops = Counter((r.get("notice") or "")[:24] for r in rows if r.get("outcome") == "error")
    print("  stops:", dict(stops))
    errors: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    for row in rows:
        errors.update(row.get("tool_errors", {}))
        calls.update(row.get("tools", {}))
    print("  tool calls:", dict(calls))
    print("  tool errors:", dict(errors))
    problems = Counter(p.split(":")[0] for r in rows for p in r["grade"].get("problems", []))
    print("  grade problems:", dict(problems))
    safety = [r for r in rows if any(p.startswith(("keep", "collateral", "tampered"))
                                     for p in r["grade"].get("problems", []))]
    print(f"  unsafe runs (changed what it must not): {len(safety)}/{len(rows)}")
    for row in safety:
        print(f"    {row['task']} r{row['rep']}: {row['grade']['problems'][:4]}")


def compare(a: str, b: str) -> None:
    ra, rb = rates(load(a)), rates(load(b))
    common = sorted(set(ra) & set(rb))
    better = [t for t in common if rb[t] > ra[t]]
    worse = [t for t in common if rb[t] < ra[t]]
    n = len(better) + len(worse)
    k = min(len(better), len(worse))
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n) if n else 1.0
    mean_a = sum(ra[t] for t in common) / len(common)
    mean_b = sum(rb[t] for t in common) / len(common)
    print(f"== {a} vs {b}: {len(common)} common tasks")
    print(f"  mean pass {mean_a:.1%} vs {mean_b:.1%}")
    print(f"  {b} better on {len(better)}, worse on {len(worse)} (sign test p={p:.3f})")
    print("  better:", better)
    print("  worse:", worse)


def main() -> None:
    for tag in sys.argv[1:]:
        summary(tag)
    if len(sys.argv) == 3:
        compare(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
