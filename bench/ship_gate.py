"""Decide whether a new Agent adapter ships: the owner's rule (2026-10-07),
applied by the dev box alone.

The candidate (new adapter + new prompt) is compared with what the laptop runs
now, on the same tasks:
  1. the product bench, thinking off and high: total pass rate not lower;
  2. every chat probe (the owner's past complaints: a greeting, praise,
     "where do you run"...) passes in every run;
  3. the held-out scenario cards: more cards pass the judge.

    python bench/ship_gate.py --off base-old fw2-eval --high base-old-high fw2-eval-high \
        --cards scen-eval-base scen-eval-fw2

Prints each gate with its numbers and the tasks that got worse, writes
<candidate off run>/ship-gate.json and, for a failed gate, <...>/why.txt: every
task or card that got worse with both sides' answers, tool calls and problems,
the material for the improvement loop (SilentTalk handoff/dev-box.md §3).
Exits 0 only if all pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / ".runtime/bench"


def results(tag: str) -> dict[tuple[str, int], dict]:
    """The latest finished run of each (task, repeat); crashes are reruns, not results."""
    found = {}
    for line in (STATE / tag / "results.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("outcome") != "crash":
            found[(row["task"], row["rep"])] = row
    return found


def bench_gate(base_tag: str, new_tag: str) -> dict:
    base, new = results(base_tag), results(new_tag)
    common = sorted(set(base) & set(new))
    by_task: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for key in common:
        by_task[key[0]][0] += base[key]["passed"]
        by_task[key[0]][1] += new[key]["passed"]
    kinds: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for task, (b, n) in by_task.items():
        kind = task.split("-")[0]
        kinds[kind][0] += b
        kinds[kind][1] += n
        kinds[kind][2] += sum(1 for key in common if key[0] == task)
    base_pass = sum(base[key]["passed"] for key in common)
    new_pass = sum(new[key]["passed"] for key in common)
    chat = [key for key in new if key[0].startswith("chat-")]
    return {
        "runs": len(common), "base": base_pass, "new": new_pass,
        "missing": len(set(base) ^ set(new)),
        "kinds": {kind: {"base": b, "new": n, "runs": r} for kind, (b, n, r) in sorted(kinds.items())},
        "worse": sorted(task for task, (b, n) in by_task.items() if n < b),
        "better": sorted(task for task, (b, n) in by_task.items() if n > b),
        "chat_failed": sorted(f"{task} r{rep}" for task, rep in chat if not new[(task, rep)]["passed"]),
        "chat_runs": len(chat),
        "passed": new_pass >= base_pass and len(common) > 0,
    }


def side(row: dict | None) -> str:
    """One run as read when looking for the cause: outcome, tools, problems, answer."""
    if row is None:
        return "   (no run)"
    tools = ", ".join(f"{name}×{n}" for name, n in (row.get("tools") or {}).items()) or "none"
    problems = row.get("grade", {}).get("problems") or row.get("checks") or []
    answer = (row.get("answer") or "").replace("\n", " ")[:400]
    return (f"   {'PASS' if row.get('passed') else 'fail'} {row.get('outcome')} {row.get('reason') or ''}"
            f" | tools: {tools} | problems: {problems}\n   answer: {answer}")


def why(base_tag: str, new_tag: str, tasks: list[str]) -> list[str]:
    base, new = results(base_tag), results(new_tag)
    lines = []
    for task in tasks:
        for rep in sorted({k[1] for k in base if k[0] == task} | {k[1] for k in new if k[0] == task}):
            lines += [f"== {task} r{rep}", f"  before ({base_tag}):", side(base.get((task, rep))),
                      f"  after ({new_tag}):", side(new.get((task, rep)))]
    return lines


def card_why(base_tag: str, new_tag: str, jobs: list[str]) -> list[str]:
    lines = []
    for job in jobs:
        lines.append(f"== {job}")
        for tag in (base_tag, new_tag):
            found = [json.loads(l) for l in (STATE / tag / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()]
            row = next((v for v in found if v["job"] == job), {})
            failed = [k for p in row.get("judge") or [] for k, ok in p.get("items", {}).items() if not ok]
            lines.append(f"  {tag}: {'PASS' if row.get('passed') else 'fail'} checks {list(row.get('checks') or {})}"
                         f" judge items failed {failed}  (conversation: {STATE / tag / 'traj' / (job + '.json')})")
    return lines


def verdicts(tag: str) -> dict[str, bool]:
    path = STATE / tag / "verdicts.jsonl"
    return {row["job"]: row["passed"] for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def card_gate(base_tag: str, new_tag: str) -> dict:
    base, new = verdicts(base_tag), verdicts(new_tag)
    common = sorted(set(base) & set(new))
    by_family: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for job in common:
        family = job.split("-")[1]
        by_family[family][0] += base[job]
        by_family[family][1] += new[job]
        by_family[family][2] += 1
    base_pass, new_pass = sum(base[j] for j in common), sum(new[j] for j in common)
    return {
        "cards": len(common), "base": base_pass, "new": new_pass,
        "missing": len(set(base) ^ set(new)),
        "families": {f: {"base": b, "new": n, "cards": c} for f, (b, n, c) in sorted(by_family.items())},
        "lost": sorted(j for j in common if base[j] and not new[j]),
        "won": sorted(j for j in common if new[j] and not base[j]),
        "passed": new_pass > base_pass,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--off", nargs=2, metavar=("BASE", "NEW"), required=True)
    parser.add_argument("--high", nargs=2, metavar=("BASE", "NEW"), required=True)
    parser.add_argument("--cards", nargs=2, metavar=("BASE", "NEW"), required=True)
    args = parser.parse_args()
    off, high = bench_gate(*args.off), bench_gate(*args.high)
    cards = card_gate(*args.cards)
    chat_failed = off["chat_failed"] + [f"{c} (high)" for c in high["chat_failed"]]
    gates = {
        "bench_off": off["passed"], "bench_high": high["passed"],
        "chat_probes": not chat_failed and off["chat_runs"] > 0 and high["chat_runs"] > 0,
        "held_out_cards": cards["passed"],
    }
    report = {"gates": gates, "ship": all(gates.values()), "off": off, "high": high, "cards": cards,
              "chat_failed": chat_failed}
    (STATE / args.off[1] / "ship-gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    if not report["ship"]:
        chat = sorted({c.split()[0] for c in chat_failed})
        lines = ["# thinking off: tasks that got worse", *why(*args.off, sorted(set(off["worse"]) | set(chat))),
                 "# thinking high: tasks that got worse", *why(*args.high, sorted(set(high["worse"]) | set(chat))),
                 "# held-out cards lost", *card_why(*args.cards, cards["lost"])]
        (STATE / args.off[1] / "why.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"why: {STATE / args.off[1] / 'why.txt'}")
    for name, gate in (("off", off), ("high", high)):
        print(f"bench {name}: {gate['base']} -> {gate['new']} of {gate['runs']} runs "
              f"({'ok' if gate['passed'] else 'LOWER'}); missing {gate['missing']}")
        for kind, k in gate["kinds"].items():
            print(f"   {kind}: {k['base']} -> {k['new']} of {k['runs']}")
        print(f"   worse: {gate['worse']}\n   better: {gate['better']}")
    print(f"chat probes failed: {chat_failed or 'none'}")
    print(f"held-out cards: {cards['base']} -> {cards['new']} of {cards['cards']} "
          f"({'ok' if cards['passed'] else 'NOT HIGHER'}); missing {cards['missing']}")
    for family, f in cards["families"].items():
        print(f"   {family}: {f['base']} -> {f['new']} of {f['cards']}")
    print(f"ship: {report['ship']} {gates}")
    sys.exit(0 if report["ship"] else 1)


if __name__ == "__main__":
    main()
