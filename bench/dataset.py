"""Training rows from recorded bench runs (run.py --record), hard-gated.

A job becomes rows only if it passed with no grade problem (hidden tests
pass, nothing it must not touch was changed, no test tampering), ended
normally, and, for a tool task, used the ready-made tool the task is about. Rows are the requests the Agent sent with its tools (the exact
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
skipped.

Eval tasks (bench/tasks, and the eval splits of the scenario cards and the
Python tasks) never become training rows: only runs over training tasks
(.runtime/agentdata/tool-tasks, scenario train cards, Python train tasks) are
accepted.

A scenario conversation (bench/scenario_cards.py) becomes rows only if the
judge passed it (bench/scenario_judge.py, <run>/verdicts.jsonl). Each user
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
from run import BUDGET_CUT  # noqa: E402

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


def user_turns(request: dict) -> int:
    return sum(m.get("role") == "user" for m in request["messages"])


def verdicts(run_dir: Path) -> dict[str, bool]:
    """The judge's verdict per scenario job (empty when the run has none)."""
    path = run_dir / "verdicts.jsonl"
    if not path.is_file():
        return {}
    return {row["job"]: bool(row["passed"]) for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())}


def rows(run_dir: Path) -> tuple[list[dict], dict[str, int]]:
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
        if not result["passed"] or result["grade"].get("problems") or result.get("outcome") != "done":
            skip("not a clean pass")
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
        answer = final_answer(json.loads(trajectory_path.read_text(encoding="utf-8")))
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--thinking", choices=THINKING, default="as-recorded",
                        help="both: every row with reasoning also without it (thinking on and off); "
                             "none: no reasoning in any row")
    args = parser.parse_args()
    total: list[dict] = []
    for run_dir in args.runs:
        kept, why = rows(run_dir)
        print(f"{run_dir.name}: {len(kept)} rows; skipped {why}")
        total += [marked for row in kept for marked in with_thinking_modes(row, args.thinking)]
    stale = [row["source"]["job"] for row in total if scope_of(row) is None]
    if stale:
        print(f"REFUSED: {len(stale)} rows were made under another system prompt: {stale[:5]}")
        return 3
    mixed = mixed_versions(total)
    if mixed:
        print(f"REFUSED: rows from more than one product version would mix: {mixed}")
        return 3
    versions = sorted({(scope_of(row), product_system(row)) for row in total}
                      | {(name, digest) for row in total for name, digest in row["source"]["first_tools"].items()})
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
