"""How long the teacher thought in a recording, and how often its thinking
budget cut a thought short (run.BUDGET_CUT): the budget must cut almost
nothing, or the students learn only the short thoughts (owner, 2026-10-10:
the teacher thinks at least as long as the base E4B).

    python bench/thought_stats.py .runtime/bench/v3-think-pilot --max-cut 0.02

Per kind of job (scenario, train-tool, code...): the steps, the share whose
thought was cut, the median and 90th percentile thought in characters, and
the median minutes a job took. Exits 1 when any kind's share cut is over
--max-cut. With --base (an exam run of the base E4B with thinking on) each
kind's median is also set against the base's median step: the teacher at
xhigh thought 288 characters a tool step and 245 a Python step where the
base thinks 650 (2026-10-10), so a short teacher shows at every check.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import final_answer  # noqa: E402
from run import BUDGET_CUT  # noqa: E402


def thoughts(run_dir: Path, job: str) -> list[str]:
    """Each Agent step's thought once: what the next request carries after
    this one's messages, and the final answer's from the trajectory."""
    requests = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((run_dir / "records" / job).glob("*.json"))]
    requests = [r for r in requests if r.get("tools")]
    found = []
    for request, after in zip(requests, requests[1:], strict=False):
        seen = request["messages"]
        if after["messages"][:len(seen)] == seen and len(after["messages"]) > len(seen):
            reply = after["messages"][len(seen)]
            if reply.get("role") == "assistant":
                found.append(reply.get("reasoning_content") or "")
    trajectory = run_dir / "traj" / f"{job}.json"
    answer = final_answer(json.loads(trajectory.read_text(encoding="utf-8"))) if trajectory.is_file() else None
    if answer is not None:
        found.append(answer.get("reasoning_content") or "")
    return found


def base_median(run_dir: Path) -> int:
    """The base's median thought per step over an exam run's trajectories."""
    lengths = []
    for path in (run_dir / "traj").glob("*.json"):
        for message in json.loads(path.read_text(encoding="utf-8")):
            items = message.get("content") if message.get("role") == "assistant" else None
            thought = "\n".join(i.get("thinking", "") for i in items or []
                                if isinstance(i, dict) and i.get("type") == "thinking")
            if thought.strip():
                lengths.append(len(thought))
    return int(statistics.median(lengths)) if lengths else 0


def stats(run_dir: Path) -> dict[str, dict]:
    kinds: dict[str, dict] = {}
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        kind = kinds.setdefault(result.get("kind") or "task", {"jobs": 0, "minutes": [], "thoughts": []})
        kind["jobs"] += 1
        kind["minutes"].append(result.get("wall", 0) / 60)
        kind["thoughts"] += [t for t in thoughts(run_dir, f"{result['task']}-r{result['rep']}") if t.strip()]
    out = {}
    for name, kind in sorted(kinds.items()):
        lengths = sorted(len(t) for t in kind["thoughts"])
        cut = sum(BUDGET_CUT in t for t in kind["thoughts"])
        out[name] = {"jobs": kind["jobs"], "steps": len(lengths), "cut": cut,
                     "cut_share": round(cut / len(lengths), 4) if lengths else 0.0,
                     "median_chars": int(statistics.median(lengths)) if lengths else 0,
                     "p90_chars": lengths[int(0.9 * (len(lengths) - 1))] if lengths else 0,
                     "median_minutes": round(statistics.median(kind["minutes"]), 1)}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--max-cut", type=float, default=0.02)
    parser.add_argument("--base", type=Path, help="an exam run of the base E4B with thinking on")
    args = parser.parse_args()
    over = []
    base = base_median(args.base) if args.base else 0
    if base:
        print(json.dumps({"run": args.base.name, "base_median_chars": base}), flush=True)
    for run_dir in args.runs:
        for name, row in stats(run_dir).items():
            if base:
                row["of_base"] = round(row["median_chars"] / base, 2)
            print(json.dumps({"run": run_dir.name, "kind": name, **row}), flush=True)
            if row["cut_share"] > args.max_cut:
                over.append(f"{run_dir.name} {name} {row['cut_share']:.1%}")
    if over:
        raise SystemExit(f"the thinking budget cut more than {args.max_cut:.0%} of the thoughts: {', '.join(over)}")


if __name__ == "__main__":
    main()
