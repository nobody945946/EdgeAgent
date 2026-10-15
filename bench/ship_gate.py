"""Decide whether a new Agent adapter ships: the owner's rule (2026-10-07),
applied by the dev box alone.

The candidate (new adapter + new prompt) is compared with each opponent it has
to beat (owner, 2026-10-10: the original E4B and what the laptop runs now, each
at its own better sampling), on the same tasks:
  1. the product bench, thinking off and high: total pass rate not lower;
  2. every chat probe (the owner's past complaints: a greeting, praise,
     "where do you run"...) passes in every run of the candidate;
  3. the held-out scenario cards: more cards pass the judge;
  4. the tool and MCP tasks, thinking off and high: not lower on their own,
     whatever the total does (owner, 2026-10-10: the plugins added later
     work through them);
  5. the held-out Python tasks: not lower (owner, 2026-10-10: Python counts too).

    python bench/ship_gate.py --new msv3-off msv3-high msv3-cards msv3-py \
        --against base v3-base v3-base-high v3-scen-eval-base v3-py-eval-base \
        --against ms3n ms3n-v3-off ms3n-v3-high ms3n-v3-cards ms3n-v3-py

Prints each gate with its numbers and the tasks that got worse, writes
<candidate off run>/ship-gate.json and, for a failed gate, <...>/why.txt: every
task or card that got worse with both sides' answers, tool calls and problems,
the material for the improvement loop (SilentTalk handoff/dev-box.md §3).
Exits 0 only if every gate passes against every opponent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / ".runtime/bench"
KEPT_KINDS = ("tool", "mcp")  # rule 4
RUNS = ("off", "high", "cards", "python")


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
    return {
        "runs": len(common), "base": base_pass, "new": new_pass,
        "missing": len(set(base) ^ set(new)),
        "kinds": {kind: {"base": b, "new": n, "runs": r} for kind, (b, n, r) in sorted(kinds.items())},
        "worse": sorted(task for task, (b, n) in by_task.items() if n < b),
        "better": sorted(task for task, (b, n) in by_task.items() if n > b),
        "lowered": [kind for kind in KEPT_KINDS if kind in kinds and kinds[kind][1] < kinds[kind][0]],
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
            failed = [k for p in row.get("judge") or [] for k in [*(p.get("failed") or {}), *p.get("missing", [])]]
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


def chat_probes(off_tag: str, high_tag: str) -> dict:
    """Rule 2, on the candidate alone: both modes ran the probes and every run passed."""
    failed, ran = [], True
    for tag, mode in ((off_tag, ""), (high_tag, " (high)")):
        chat = {key: row for key, row in results(tag).items() if key[0].startswith("chat-")}
        ran = ran and bool(chat)
        failed += sorted(f"{task} r{rep}{mode}" for (task, rep), row in chat.items() if not row["passed"])
    return {"failed": failed, "passed": ran and not failed}


def against(new: dict[str, str], old: dict[str, str]) -> dict:
    """Rules 1, 3, 4 and 5 against one opponent."""
    off, high = bench_gate(old["off"], new["off"]), bench_gate(old["high"], new["high"])
    cards, python = card_gate(old["cards"], new["cards"]), bench_gate(old["python"], new["python"])
    gates = {"bench_off": off["passed"], "bench_high": high["passed"], "held_out_cards": cards["passed"],
             "tools_and_mcp": not off["lowered"] and not high["lowered"], "python": python["passed"]}
    return {"gates": gates, "passed": all(gates.values()), "off": off, "high": high, "cards": cards,
            "python": python}


def why_lines(name: str, new: dict[str, str], old: dict[str, str], gate: dict, chat: list[str]) -> list[str]:
    return [f"# against {name}, thinking off: tasks that got worse",
            *why(old["off"], new["off"], sorted(set(gate["off"]["worse"]) | set(chat))),
            f"# against {name}, thinking high: tasks that got worse",
            *why(old["high"], new["high"], sorted(set(gate["high"]["worse"]) | set(chat))),
            f"# against {name}, held-out cards lost", *card_why(old["cards"], new["cards"], gate["cards"]["lost"]),
            f"# against {name}, Python tasks that got worse",
            *why(old["python"], new["python"], gate["python"]["worse"])]


def show(name: str, gate: dict) -> None:
    print(f"== against {name}: {'pass' if gate['passed'] else 'FAIL'} {gate['gates']}")
    for mode in ("off", "high"):
        g = gate[mode]
        print(f"bench {mode}: {g['base']} -> {g['new']} of {g['runs']} runs "
              f"({'ok' if g['passed'] else 'LOWER'}); missing {g['missing']}")
        for kind, k in g["kinds"].items():
            print(f"   {kind}: {k['base']} -> {k['new']} of {k['runs']}")
        print(f"   worse: {g['worse']}\n   better: {g['better']}")
        print(f"   tool/mcp lower: {g['lowered'] or 'none'}")
    cards, python = gate["cards"], gate["python"]
    print(f"held-out cards: {cards['base']} -> {cards['new']} of {cards['cards']} "
          f"({'ok' if cards['passed'] else 'NOT HIGHER'}); missing {cards['missing']}")
    for family, f in cards["families"].items():
        print(f"   {family}: {f['base']} -> {f['new']} of {f['cards']}")
    print(f"python: {python['base']} -> {python['new']} of {python['runs']} "
          f"({'ok' if python['passed'] else 'LOWER'}); missing {python['missing']}; worse: {python['worse']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--new", nargs=4, metavar=tuple(r.upper() for r in RUNS), required=True,
                        help="the candidate's runs: bench off, bench high, held-out cards, Python tasks")
    parser.add_argument("--against", nargs=5, metavar=("NAME", *(r.upper() for r in RUNS)), action="append",
                        required=True, help="an opponent it has to beat and its runs, in --new's order")
    args = parser.parse_args()
    new = dict(zip(RUNS, args.new))
    opponents = {name: dict(zip(RUNS, tags)) for name, *tags in args.against}
    gates = {name: against(new, old) for name, old in opponents.items()}
    chat = chat_probes(new["off"], new["high"])
    report = {"ship": chat["passed"] and all(g["passed"] for g in gates.values()), "chat_probes": chat,
              "against": gates}
    (STATE / new["off"] / "ship-gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")
    if not report["ship"]:
        probes = sorted({c.split()[0] for c in chat["failed"]})
        lines = [line for name, gate in gates.items()
                 for line in why_lines(name, new, opponents[name], gate, probes)]
        (STATE / new["off"] / "why.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"why: {STATE / new['off'] / 'why.txt'}")
    for name, gate in gates.items():
        show(name, gate)
    print(f"chat probes failed: {chat['failed'] or 'none'}{'' if chat['passed'] or chat['failed'] else ' (a mode ran none)'}")
    print(f"ship: {report['ship']}")
    sys.exit(0 if report["ship"] else 1)


if __name__ == "__main__":
    main()
