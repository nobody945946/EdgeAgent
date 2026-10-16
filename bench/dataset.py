"""Training rows from recorded bench runs (run.py --record), hard-gated.

A job becomes rows only if it passed with no grade problem (hidden tests
pass, nothing it must not touch was changed, no test tampering), ended
normally, read no tool result saying a name did not resolve (the sandbox has
no network, the laptop has: scenario_checks.unresolved), and, for a tool
task, used the ready-made tool the task is about. Rows are the requests the Agent sent with its tools (the exact
system prompt, tool definitions and history the model saw; Pi's own summary
requests carry no tools and are skipped) and the replies to them, in
train_lora.py's format {"messages", "tools", "train": "all", "train_from"}.
tool_search loads tools mid-task, so the tool list grows: each stretch of
requests with the same tools is one row (its last request plus the reply),
trained only on the replies written in that stretch, under the tools the
model saw when it wrote them.

A takeover job (run.py --takeover: a student's failed conversation replayed
word for word up to a point, then the teacher's) trains only the teacher's
replies; the student's are the situation. One whose replayed prefix got other
tool results than the student did is not the student's situation and is
skipped. Where the takeover is at the student's mistake (run.mistake_points:
a runaway reply, a repeated call, a wrong last answer), the student's reply
and the teacher's to the same request are a wrong/right pair (--pairs, in
train_lora.py --pairs' format {"messages", "tools", "chosen", "rejected"}),
from a job that passed like any other.

Eval tasks (bench/tasks, and the eval splits of the scenario cards and the
Python tasks) never become training rows: only runs over training tasks
(.runtime/agentdata/tool-tasks, scenario train cards, Python train tasks) are
accepted.

A recorded conversation becomes rows only if the judge passed it
(bench/scenario_judge.py, <run>/verdicts.jsonl): a scenario card
(bench/scenario_cards.py) on its items, any other task, after its own
check, on what its answers state; a run never judged gives none. Each user
turn ends a stretch: Gemma's template shows the model's reasoning only after
the last user message, so a row never spans two turns. The teacher's
reasoning comes with its replies (reasoning_content, as Pi sends it back);
with --thinking both every row with reasoning also comes without it, so one
adapter learns to answer with thinking on and off (the row's "thinking" tells
train_lora.py which way to render it). A row with a thought the reasoning
budget cut short (run.BUDGET_CUT) comes only without reasoning.

One output holds one product version: per task scope (who asked), every
row's system prompt must be the same and the current product's
(edge_agent/prompt.py), and each tool has one definition across all rows;
runs recorded under other product code would teach two. Which tools a task
starts with and which MCP servers it has follow the owner's settings for
that task (web denied, servers connected), like its folder: per task, not a
version. Otherwise nothing is written (exit 3). The output gets a
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
sys.path.insert(0, str(ROOT))
from edge_agent import prompt as agent_prompt  # noqa: E402
from edge_agent.turn import taiwan_traditional  # noqa: E402

sys.path.insert(0, str(ROOT / "bench"))
from run import BUDGET_CUT, clean  # noqa: E402
from scenario_checks import unresolved  # noqa: E402

EVAL_TASKS = {p.name for p in (ROOT / "bench/tasks").iterdir() if p.is_dir()}


def is_eval(task: str) -> bool:
    """bench/tasks, scenario cards of the eval split (eval-*) and Python eval tasks (py-eval-*)."""
    return task in EVAL_TASKS or task.startswith(("eval-", "py-eval-"))


def final_answer(trajectory: list[dict]) -> dict | None:
    """The Agent's last assistant message in wire format (its text and the
    reasoning before it: a finished job ends with its report, not a tool call)."""
    for message in reversed(trajectory):
        if message.get("role") != "assistant":
            continue
        items = message.get("content") or []
        if any(isinstance(i, dict) and i.get("type") == "toolCall" for i in items):
            return None
        text = "".join(i.get("text", "") for i in items if isinstance(i, dict) and i.get("type") == "text")
        reasoning = "\n".join(i.get("thinking", "") for i in items if isinstance(i, dict)
                              and i.get("type") == "thinking" and i.get("thinking", "").strip())
        if not text.strip():
            return None
        return {"role": "assistant", "content": text, **({"reasoning_content": reasoning} if reasoning else {})}
    return None


# Searching a whole drive or the whole user folder (Get-ChildItem C:\ -Recurse)
# takes minutes on the owner's laptop and is never how to find a file the user
# put in the work folder; the teacher did it when a file was not where it
# first looked (v3 recording, 2026-10-14: 8 of about 1,200 jobs, some passed).
# The student must not learn it, so such a job gives no rows.
_SEGMENT = re.compile(r"[;|\n]")
_LISTING = re.compile(r"(?i)(?:^|[\s=(&])(?:Get-ChildItem|gci|ls|dir)(?=\s)")
_RECURSE = re.compile(r"(?i)\s-r(?:e(?:c(?:u(?:r(?:se?)?)?)?)?)?\b")
_SHALLOW = re.compile(r"(?i)\s-d(?:e(?:p(?:th?)?)?)?\s+[0-2]\b")
_WHOLE = re.compile(r"(?i)(?:^|[\s'\"(,=])(?:[A-Za-z]:[\\/]?(?:Users(?:[\\/][^\\/'\"\s,)]+)?[\\/]?)?"
                    r"|\$env:(?:USERPROFILE|HOMEDRIVE|SystemDrive)\\?|\$HOME|~)(?=$|[\s'\"),])")


def whole_drive_scan(trajectory: list[dict]) -> bool:
    """Whether a PowerShell call listed a whole drive or user folder recursively."""
    for message in trajectory:
        for item in message.get("content") or [] if message.get("role") == "assistant" else []:
            if not (isinstance(item, dict) and item.get("type") == "toolCall" and item.get("name") == "powershell"):
                continue
            command = str((item.get("arguments") or {}).get("command") or "")
            for segment in _SEGMENT.split(command):
                shallow = _SHALLOW.search(segment)  # -Depth 2 or less looks around, it does not sweep
                if _LISTING.search(segment) and _RECURSE.search(segment) and _WHOLE.search(segment) and not shallow:
                    return True
    return False


def user_turns(request: dict) -> int:
    return sum(m.get("role") == "user" for m in request["messages"])


def verdicts(run_dir: Path) -> dict[str, bool]:
    """The judge's verdict per job (empty when the run has none)."""
    path = run_dir / "verdicts.jsonl"
    if not path.is_file():
        return {}
    return {row["job"]: bool(row["passed"]) for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def rows(run_dir: Path, pairs: list[dict] | None = None) -> tuple[list[dict], dict[str, int]]:
    """A run's rows and why the rest were skipped; its wrong/right pairs go
    into `pairs` when one is given."""
    kept, why = [], {}
    judged = verdicts(run_dir)

    def skip(reason: str) -> None:
        why[reason] = why.get(reason, 0) + 1

    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        job = f"{result['task']}-r{result['rep']}"
        if is_eval(result["task"]):
            skip("eval task")
            continue
        if result.get("kind") == "scenario" and not judged.get(job):
            skip("scenario not passed by the judge")
            continue
        if not clean(result):
            skip("not a clean pass")
            continue
        if not judged.get(job):
            skip("answers not passed by the judge")
            continue
        takeover = result.get("takeover")
        if takeover and takeover.get("replay_differs"):
            # The replayed prefix got other tool results than the student did:
            # the teacher did not take over the student's situation.
            skip("takeover replay drifted")
            continue
        records = sorted((run_dir / "records" / job).glob("*.json"))
        requests = [json.loads(p.read_text(encoding="utf-8")) for p in records]
        requests = [r for r in requests if r.get("tools")]
        trajectory_path = run_dir / "traj" / f"{job}.json"
        if not requests or not trajectory_path.is_file():
            skip("no recording")
            continue
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
        if whole_drive_scan(trajectory):
            skip("searched a whole drive")
            continue
        if unresolved(trajectory):
            skip("met the sandbox's missing network")
            continue
        answer = final_answer(trajectory)
        if answer is None:
            skip("no final answer")
            continue
        replies = []
        for index, request in enumerate(requests):
            if index + 1 == len(requests):
                replies.append(answer)
                continue
            seen, after = request["messages"], requests[index + 1]["messages"]
            follows = after[:len(seen)] == seen and len(after) > len(seen)
            replies.append(after[len(seen)] if follows and after[len(seen)].get("role") == "assistant" else None)
        if takeover:
            # Learning from its own mistakes (run.py --takeover): the student's
            # answers before the takeover are the situation, never a target.
            replies[:takeover["at"]] = [None] * min(takeover["at"], len(replies))
            at = takeover["at"]
            if pairs is not None and takeover.get("rejected") and at < len(replies) and replies[at] is not None:
                pair = {"messages": [dict(m) for m in requests[at]["messages"]], "tools": requests[at]["tools"],
                        "chosen": dict(replies[at]), "rejected": dict(takeover["rejected"])}
                for message in [*pair["messages"], pair["chosen"], pair["rejected"]]:
                    if message.get("role") in ("user", "assistant"):
                        for key in ("content", "reasoning_content"):
                            if isinstance(message.get(key), str):
                                message[key] = taiwan_traditional(message[key])
                pairs.append({**pair, "source": {"run": run_dir.name, "job": job, "kind": result.get("kind", ""),
                                                 "takeover": {k: takeover[k] for k in ("from", "at", "of")},
                                                 "first_tools": {t["function"]["name"]: _digest(t["function"])
                                                                 for t in requests[0]["tools"]}}})
        start = 0
        for index, request in enumerate(requests):
            reply = replies[index]
            if reply is None:
                start = index + 1
                continue
            last_of_stretch = (index + 1 == len(requests) or replies[index + 1] is None
                               or requests[index + 1]["tools"] != request["tools"]
                               or user_turns(requests[index + 1]) > user_turns(request))
            if not last_of_stretch:
                continue
            messages = [dict(m) for m in request["messages"]] + [reply]
            # The teacher (Qwen) slips Simplified characters into what it and
            # the simulated user write; the student learns Taiwan's forms (the
            # product's own converter, which leaves Traditional text alone).
            for message in messages:
                if message.get("role") in ("user", "assistant"):
                    for key in ("content", "reasoning_content"):
                        if isinstance(message.get(key), str):
                            message[key] = taiwan_traditional(message[key])
            kept.append({"messages": messages, "tools": request["tools"], "train": "all",
                         "train_from": len(requests[start]["messages"]),
                         "source": {"run": run_dir.name, "job": job, "kind": result.get("kind", ""),
                                    **({"takeover": {k: takeover[k] for k in ("from", "at", "of")}} if takeover else {}),
                                    "first_tools": {t["function"]["name"]: _digest(t["function"])
                                                    for t in requests[0]["tools"]}}})
            start = index + 1
    return kept, why


# Pi adds the workspace's own AGENTS.md to the system prompt (the product does
# the same for the user's folders), the folder, the date, the model and its
# window (the bridge's runtime section), and the MCP servers the owner
# connected: per task, not a product version.
PER_TASK = re.compile(r"<(project_context|cwd|today|runtime|addendum|mcp_servers)>.*?</\1>\n*", re.DOTALL)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def scope_of(row: dict) -> str | None:
    """Which of the product's prompts (edge_agent/prompt.py) the row was
    made under; None when it matches none of them (other product code)."""
    system = "".join(str(m["content"]) for m in row["messages"] if m.get("role") == "system")
    for scope in agent_prompt.WHO:
        if system.startswith(agent_prompt.system_prompt(scope)):
            return scope
    return None


def product_system(row: dict) -> str:
    """The system prompt as the product writes it, without per-task sections."""
    return _digest([PER_TASK.sub("", str(m["content"])) for m in row["messages"] if m.get("role") == "system"])


def mixed_versions(total: list[dict]) -> dict[str, dict[str, int]]:
    """Each scope's system prompt and each tool's definition that comes in
    more than one version, with its rows per version and run."""
    seen: dict[str, Counter] = {}
    for row in total:
        run = row["source"]["run"]
        seen.setdefault(f"system {scope_of(row)}", Counter())[f"{run} {product_system(row)}"] += 1
        for name, digest in row["source"]["first_tools"].items():
            seen.setdefault(f"tool {name}", Counter())[f"{run} {digest}"] += 1
    return {key: dict(runs) for key, runs in seen.items()
            if len({version.split()[1] for version in runs}) > 1}


THINKING = ("as-recorded", "both", "none")
CHAT = "train-chat"  # make_tool_tasks' plain conversation


def with_thinking_modes(row: dict, mode: str) -> list[dict]:
    """The row marked with how train_lora.py renders it: as recorded; `both`,
    a row that has reasoning also once without any; `none`, never with
    reasoning (the student keeps its own thinking: 2026-10-10, adapters trained
    on the teacher's thoughts, cut to a 1024-token budget, thought half as long
    as the base and lost with thinking on). A thought cut short by the budget is
    never a target: that row comes only without reasoning.

    Plain conversation is answered at once in either mode, as DeepSeek's R1
    data answered simple queries without a chain of thought (owner, 2026-10-10:
    long thinking is the Agent's work, chatting is another matter): a chat
    task's row comes without reasoning, once with the think switch on (Gemma's
    template then has the reply follow the turn directly) and once off."""
    thinking = any(m.get("reasoning_content") for m in row["messages"])
    plain = {**row, "messages": [{k: v for k, v in m.items() if k != "reasoning_content"} for m in row["messages"]],
             "thinking": False}
    if row.get("source", {}).get("kind") == CHAT:
        return [{**plain, "thinking": True}, plain]
    if mode == "none" or any(BUDGET_CUT in (m.get("reasoning_content") or "") for m in row["messages"]):
        return [plain]
    marked = {**row, "thinking": thinking}
    if not (thinking and mode == "both"):
        return [marked]
    return [marked, plain]


def pair_modes(pair: dict, mode: str) -> list[dict]:
    """A pair marked for train_lora.py as with_thinking_modes marks a row:
    `both`, as recorded and once without reasoning; `none`, never with it.
    Without reasoning a pair whose side said nothing else (a runaway thought)
    or whose sides become the same is no pair; a chosen thought the budget cut
    short comes only without."""
    def plain(message: dict) -> dict:
        return {k: v for k, v in message.items() if k != "reasoning_content"}

    def said(message: dict) -> bool:
        return bool((message.get("content") or "").strip() or message.get("tool_calls"))

    stripped = {**pair, "messages": [plain(m) for m in pair["messages"]], "chosen": plain(pair["chosen"]),
                "rejected": plain(pair["rejected"]), "thinking": False}
    stripped = [stripped] if said(stripped["chosen"]) and said(stripped["rejected"]) \
        and stripped["chosen"] != stripped["rejected"] else []
    thinking = any(m.get("reasoning_content") for m in [*pair["messages"], pair["chosen"], pair["rejected"]])
    if mode == "none" or BUDGET_CUT in (pair["chosen"].get("reasoning_content") or ""):
        return stripped
    marked = [{**pair, "thinking": thinking}]
    return marked + stripped if thinking and mode == "both" else marked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pairs", type=Path,
                        help="also write the takeovers' wrong/right pairs here (train_lora.py --pairs), "
                             "stamped like --out")
    parser.add_argument("--thinking", choices=THINKING, default="as-recorded",
                        help="both: every row with reasoning also without it (thinking on and off); "
                             "none: no reasoning in any row")
    args = parser.parse_args()
    total: list[dict] = []
    paired: list[dict] = []
    for run_dir in args.runs:
        found: list[dict] = []
        kept, why = rows(run_dir, found)
        print(f"{run_dir.name}: {len(kept)} rows, {len(found)} pairs; skipped {why}")
        total += [marked for row in kept for marked in with_thinking_modes(row, args.thinking)]
        paired += [marked for pair in found for marked in pair_modes(pair, args.thinking)]
    stale = [row["source"]["job"] for row in total + paired if scope_of(row) is None]
    if stale:
        print(f"REFUSED: {len(stale)} rows were made under another system prompt: {stale[:5]}")
        return 3
    mixed = mixed_versions(total + paired)
    if mixed:
        print(f"REFUSED: rows from more than one product version would mix: {mixed}")
        return 3
    versions = sorted({(scope_of(row), product_system(row)) for row in total + paired}
                      | {(name, digest) for row in total + paired for name, digest in row["source"]["first_tools"].items()})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in total:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with_project = sum("<project_context>" in str(row["messages"][0]["content"]) for row in total)
    stamp = {"producer": "edgeagent-dataset",
             "version": _digest(versions),
             "runs": [run.name for run in args.runs],
             "thinking": args.thinking,
             "rows_with_project_agents_md": with_project}
    args.out.with_name(args.out.name + ".version").write_text(json.dumps(stamp), encoding="utf-8")
    print(f"{len(total)} rows -> {args.out} (product version {stamp['version']})")
    if args.pairs:
        args.pairs.parent.mkdir(parents=True, exist_ok=True)
        with args.pairs.open("w", encoding="utf-8") as handle:
            for pair in paired:
                handle.write(json.dumps(pair, ensure_ascii=False) + "\n")
        args.pairs.with_name(args.pairs.name + ".version").write_text(
            json.dumps({**stamp, "kind": "pairs"}), encoding="utf-8")
        print(f"{len(paired)} pairs -> {args.pairs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
