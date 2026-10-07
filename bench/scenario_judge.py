"""Judge recorded scenario conversations (bench/scenario_cards.py tasks).

A conversation passes when every programmatic check of its family passes
(bench/scenario_checks.py) and the judge model, a different family from the
teacher (owner, 2026-10-07: Gemma 4 31B), finds every must / must_not item of
the card met, twice, with the items in a different order each time. It sees
what the asker saw plus the tool calls and results, never the thinking.
Writes <run>/verdicts.jsonl; bench/dataset.py turns only passing
conversations into rows.

`validate` first proves the judge worth trusting: it must reject corrupted
copies of conversations (the last answer cut short, or swapped for another
card's), and it reports how often it accepts the originals.

    python bench/scenario_judge.py judge .runtime/bench/<run> --task-root <tasks> --config judge-gemma
    python bench/scenario_judge.py validate .runtime/bench/<run> --task-root <tasks> --config judge-gemma
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)
from scenario_checks import failures  # noqa: E402

TOOL_RESULT_CHARS = 700
REJECT_NEEDED = 0.9

JUDGE_PROMPT = """你是嚴格的評審，要判斷一個 AI 助理在一段對話裡有沒有做到規定。只根據下面的對話紀錄判斷，不要猜測紀錄以外的事。

使用者的目標（使用者沒有原句說出來）：{goal}

對話紀錄（「工具」是助理呼叫的工具和結果，使用者看不到工具結果，只看得到助理的回答）：
{transcript}

逐條判斷下列規定。「必須」類：紀錄裡明確做到才算通過；「不可」類：紀錄裡完全沒有發生才算通過。
{items}

只輸出一個 JSON 物件：{{"items": [{{"id": "規定編號", "pass": true 或 false, "evidence": "引用紀錄中的原文（20 字內），沒有就寫 無"}}]}}，每條規定都要有。"""


def transcript(trajectory: list[dict]) -> str:
    lines = []
    for message in trajectory:
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            text = content if isinstance(content, str) else "".join(
                i.get("text", "") for i in content or [] if isinstance(i, dict))
            lines.append(f"使用者：{text}")
        elif role == "assistant":
            for item in content or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and item.get("text", "").strip():
                    lines.append(f"助理：{item['text']}")
                elif item.get("type") == "toolCall":
                    arguments = json.dumps(item.get("arguments"), ensure_ascii=False)[:300]
                    lines.append(f"工具（呼叫 {item.get('name')}）：{arguments}")
        elif role == "toolResult":
            text = "".join(i.get("text", "") for i in content or [] if isinstance(i, dict))
            lines.append(f"工具（結果{'，失敗' if message.get('isError') else ''}）：{text[:TOOL_RESULT_CHARS]}")
    return "\n".join(lines)


def items_for(task: dict, rng: random.Random) -> list[tuple[str, str]]:
    found = [(f"M{i}", f"必須：{text}") for i, text in enumerate(task["must"], 1)]
    found += [(f"N{i}", f"不可：{text}") for i, text in enumerate(task["must_not"], 1)]
    rng.shuffle(found)
    return found


def ask(port: int, prompt: str) -> str:
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "temperature": 0,
                       "max_tokens": 8000, "chat_template_kwargs": {"enable_thinking": True}}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read())["choices"][0]["message"].get("content") or ""


def rule(port: int, task: dict, trajectory: list[dict], seed: int) -> dict:
    """One judging pass: every item's verdict, or why it could not be read."""
    items = items_for(task, random.Random(seed))
    prompt = JUDGE_PROMPT.format(goal=task["scenario"]["goal"], transcript=transcript(trajectory),
                                 items="\n".join(f"{key}. {text}" for key, text in items))
    match = re.search(r"\{.*\}", ask(port, prompt), re.DOTALL)
    try:
        verdicts = {v["id"]: bool(v["pass"]) for v in json.loads(match.group(0))["items"]} if match else {}
    except (ValueError, KeyError, TypeError):
        verdicts = {}
    missing = [key for key, _ in items if key not in verdicts]
    return {"items": verdicts, "missing": missing,
            "passed": not missing and all(verdicts[key] for key, _ in items)}


def verdict(port: int, task: dict, record: dict) -> dict:
    found = failures(record, task.get("checks", []))
    if found:
        return {"passed": False, "checks": found}
    passes = [rule(port, task, record["trajectory"], seed) for seed in (1, 2)]
    return {"passed": all(p["passed"] for p in passes), "checks": {}, "judge": passes}


def records(run_dir: Path, task_root: Path) -> list[tuple[str, dict, dict]]:
    """(job, task, record) for every scenario job of the run."""
    found = []
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        task_file = task_root / result["task"] / "task.json"
        if result.get("kind") != "scenario" or not task_file.is_file():
            continue
        job = f"{result['task']}-r{result['rep']}"
        trajectory_file = run_dir / "traj" / f"{job}.json"
        trajectory = json.loads(trajectory_file.read_text(encoding="utf-8")) if trajectory_file.is_file() else []
        record = {"dialogue": result.get("dialogue") or [], "ended": result.get("ended"),
                  "trajectory": trajectory, "outcome": result.get("outcome")}
        found.append((job, json.loads(task_file.read_text(encoding="utf-8")), record))
    return found


def judge(args: argparse.Namespace, server: run.Server) -> None:
    out = args.run / "verdicts.jsonl"
    done = {json.loads(line)["job"] for line in out.read_text(encoding="utf-8").splitlines()} if out.is_file() else set()
    for job, task, record in records(args.run, args.task_root):
        if job in done:
            continue
        found = verdict(server.port, task, record)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"job": job, "family": task["family"], **found}, ensure_ascii=False) + "\n")
        print(f"{job}: {'PASS' if found['passed'] else 'fail'} {list(found['checks'])}", flush=True)


def corrupt(trajectory: list[dict], other: list[dict], how: str) -> list[dict]:
    """The conversation with its last answer cut to its first sentence, or
    replaced by the last answer of another card's conversation."""
    last = max(i for i, m in enumerate(trajectory) if m.get("role") == "assistant"
               and any(isinstance(x, dict) and x.get("type") == "text" for x in m.get("content") or []))
    message = dict(trajectory[last])
    texts = [x for x in message["content"] if isinstance(x, dict) and x.get("type") == "text"]
    if how == "cut":
        first = re.split(r"(?<=[。！？\n])", texts[-1]["text"].strip())[0]
        replacement = first[:60]
    else:
        replacement = next(x.get("text", "") for m in reversed(other) if m.get("role") == "assistant"
                           for x in m.get("content") or [] if isinstance(x, dict) and x.get("type") == "text")
    message["content"] = [x for x in message["content"] if x not in texts] + [{"type": "text", "text": replacement}]
    return trajectory[:last] + [message] + trajectory[last + 1:]


def validate(args: argparse.Namespace, server: run.Server) -> None:
    found = [(job, task, record) for job, task, record in records(args.run, args.task_root)
             if not failures(record, task.get("checks", []))][:args.limit]
    accepted = rejected = trials = 0
    for index, (job, task, record) in enumerate(found):
        accepted += verdict(server.port, task, record)["passed"]
        other = found[(index + 1) % len(found)][2]["trajectory"]
        for how in ("cut", "swap"):
            trials += 1
            bad = {**record, "trajectory": corrupt(record["trajectory"], other, how)}
            rejected += not verdict(server.port, task, bad)["passed"]
        print(f"{job}: originals accepted {accepted}/{index + 1}, corrupted rejected {rejected}/{trials}", flush=True)
    rate = rejected / trials if trials else 0.0
    summary = {"conversations": len(found), "originals_accepted": accepted,
               "corrupted_rejected": rejected, "corrupted": trials, "trusted": rate >= REJECT_NEEDED}
    (args.run / "judge-validation.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    if rate < REJECT_NEEDED:
        raise SystemExit(f"the judge rejected only {rate:.0%} of corrupted conversations (needs {REJECT_NEEDED:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("judge", "validate"))
    parser.add_argument("run", type=Path)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--config", required=True, help="the judge's bench config (configs.json)")
    parser.add_argument("--port", type=int, default=18489)
    parser.add_argument("--limit", type=int, default=30, help="validate: conversations to use")
    args = parser.parse_args()
    config = json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    (args.run / "judge").mkdir(exist_ok=True)
    server = run.Server(config, args.run / "judge", 1, args.port)
    try:
        {"judge": judge, "validate": validate}[args.command](args, server)
    finally:
        server.close()


if __name__ == "__main__":
    main()
