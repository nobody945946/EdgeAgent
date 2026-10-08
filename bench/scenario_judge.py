"""Judge recorded scenario conversations (bench/scenario_cards.py tasks).

A conversation passes when every programmatic check of its family passes
(bench/scenario_checks.py) and the judge model, a different family from the
teacher (owner, 2026-10-07: Gemma 4 31B), finds that each turn's last answer
answers its own message in full (one call that sees only the messages and
those answers), then every must / must_not item of the card met, twice, with
the items in a different order each time. It sees what the asker saw plus the
tool calls and results, never the thinking.
Writes <run>/verdicts.jsonl; bench/dataset.py turns only passing
conversations into rows.

`validate` first proves the judge worth trusting: for the conversations it
accepts, it must reject corrupted copies (the last answer stopped
mid-sentence, the last answer or every answer swapped for another card's).
A judge that accepts too few originals cannot show that and is not trusted.

    python bench/scenario_judge.py judge .runtime/bench/<run> --task-root <tasks> --config judge-gemma
    python bench/scenario_judge.py validate .runtime/bench/<run> --task-root <tasks> --config judge-gemma
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)
from scenario_checks import failures, family_checks  # noqa: E402

TOOL_RESULT_CHARS = 700
REJECT_NEEDED = 0.9
# Fewer accepted originals than this leave too few corrupted copies to tell
# a judge that tells them apart from one that rejects everything.
MIN_ACCEPTED = 10
CORRUPTIONS = ("cut", "swap", "swap_all")
# Every row is one turn (bench/dataset.py), so every answer must answer its own
# message. A card's items often check only the process: on spec v2's longer
# conversations a last answer swapped for another card's still met them all
# (teacher-cards-1024, 2026-10-08). Asked as one more item among the card's,
# the judge passed 6 of 14 answers cut off mid-sentence (it reads the card's
# items against a long transcript of tool calls); asked alone, per turn, over
# the messages and answers only, it has nothing else to read.
ANSWER_PROMPT = """你是嚴格的評審。下面是一段對話裡使用者說的每一則訊息，以及 AI 助理在那一輪最後給使用者的回答（助理中間查資料、跑工具的過程省略了）。

{turns}

逐輪判斷那一輪的回答：
1. 有沒有回應那則訊息要的事（照前面的對話理解那則訊息；反問使用者來釐清那則訊息也算回應）；
2. 話有沒有講完：句子、清單、表格、程式碼都完整，不是講到一半就斷掉。
兩點都做到才算通過。

只輸出一個 JSON 物件：{{"turns": [{{"turn": 輪次, "pass": true 或 false, "evidence": "沒通過就引用那個回答的最後 20 字，通過寫 無"}}]}}，每一輪都要有。"""

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
    # Without thinking: two passes over hundreds of conversations with thinking
    # would take most of a night; validate() proves the judge still rejects
    # corrupted conversations this way before its verdicts are used.
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "temperature": 0,
                       "max_tokens": 3000, "chat_template_kwargs": {"enable_thinking": False}}).encode()
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
        found = json.loads(match.group(0))["items"] if match else []
        verdicts = {v["id"]: bool(v["pass"]) for v in found}
        # What the judge quoted for each item it failed, to read its reasons.
        failed = {v["id"]: str(v.get("evidence", ""))[:80] for v in found if not v["pass"]}
    except (ValueError, KeyError, TypeError):
        verdicts, failed = {}, {}
    missing = [key for key, _ in items if key not in verdicts]
    return {"items": verdicts, "failed": failed, "missing": missing,
            "passed": not missing and all(verdicts[key] for key, _ in items)}


def turns(trajectory: list[dict]) -> list[tuple[str, str]]:
    """(message, the last answer of its turn) for each user message; the answer
    is empty when the turn has none."""
    found = []
    for message in trajectory:
        if message.get("role") == "user":
            content = message.get("content")
            found.append((content if isinstance(content, str) else "".join(
                i.get("text", "") for i in content or [] if isinstance(i, dict)), ""))
        elif message.get("role") == "assistant" and found and texts(message):
            found[-1] = (found[-1][0], "\n".join(texts(message)))
    return found


def answered(port: int, trajectory: list[dict]) -> dict:
    """The answer check: each turn's last answer answers its message, in full."""
    pairs = turns(trajectory)
    listed = "\n\n".join(f"第 {n} 輪\n使用者：{message}\n助理最後的回答：{answer or '（沒有回答）'}"
                          for n, (message, answer) in enumerate(pairs, 1))
    match = re.search(r"\{.*\}", ask(port, ANSWER_PROMPT.format(turns=listed)), re.DOTALL)
    try:
        found = {int(v["turn"]): v for v in json.loads(match.group(0))["turns"]} if match else {}
        verdicts = {n: bool(found[n]["pass"]) for n in found}
        failed = {f"turn {n}": str(found[n].get("evidence", ""))[:80] for n in found if not verdicts[n]}
    except (ValueError, KeyError, TypeError):
        verdicts, failed = {}, {}
    missing = [f"turn {n}" for n in range(1, len(pairs) + 1) if n not in verdicts]
    return {"turns": verdicts, "failed": failed, "missing": missing,
            "passed": not missing and all(verdicts.values()) and all(answer for _, answer in pairs)}


def verdict(port: int | None, task: dict, record: dict) -> dict:
    """The programmatic checks, then (with a judge server) the answer check
    and the card's items twice; each pass only runs if the one before passed.
    Without a server (--checks-only: the judge failed its own validation) a
    conversation passes on the checks alone."""
    found = failures(record, family_checks(task["family"]))
    if found:
        return {"passed": False, "checks": found}
    if port is None:
        return {"passed": True, "checks": {}, "judge": "checks-only"}
    passes = [answered(port, record["trajectory"])]
    for seed in (1, 2):  # the second pass only confirms a first pass
        if not passes[-1]["passed"]:
            break
        passes.append(rule(port, task, record["trajectory"], seed))
    return {"passed": all(p["passed"] for p in passes), "checks": {}, "judge": passes}


def records(run_dir: Path, task_root: Path) -> list[tuple[str, dict, dict]]:
    """(job, task, record) for every scenario job of the run."""
    found = []
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        task_file = task_root / result["task"] / "task.json"
        # A crash is the sandbox failing to report (run.py reruns it), not a conversation.
        if result.get("kind") != "scenario" or result.get("outcome") == "crash" or not task_file.is_file():
            continue
        job = f"{result['task']}-r{result['rep']}"
        trajectory_file = run_dir / "traj" / f"{job}.json"
        trajectory = json.loads(trajectory_file.read_text(encoding="utf-8")) if trajectory_file.is_file() else []
        record = {"dialogue": result.get("dialogue") or [], "ended": result.get("ended"),
                  "trajectory": trajectory, "outcome": result.get("outcome")}
        found.append((job, json.loads(task_file.read_text(encoding="utf-8")), record))
    return found


def judge(args: argparse.Namespace, server: run.Server | None) -> None:
    out = args.run / "verdicts.jsonl"
    done = {json.loads(line)["job"] for line in out.read_text(encoding="utf-8").splitlines()} if out.is_file() else set()
    lock = threading.Lock()

    def one(item: tuple[str, dict, dict]) -> None:
        job, task, record = item
        found = verdict(server.port if server is not None else None, task, record)
        with lock, out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"job": job, "family": task["family"], **found}, ensure_ascii=False) + "\n")
        print(f"{job}: {'PASS' if found['passed'] else 'fail'} {list(found['checks'])}", flush=True)

    todo = [item for item in records(args.run, args.task_root) if item[0] not in done]
    with ThreadPoolExecutor(server.parallel if server is not None else 1) as pool:
        list(pool.map(one, todo))


def texts(message: dict) -> list[str]:
    """The answer's text parts (what the transcript shows the judge)."""
    return [x["text"] for x in message.get("content") or []
            if isinstance(x, dict) and x.get("type") == "text" and x.get("text", "").strip()]


def answers(trajectory: list[dict]) -> list[int]:
    return [i for i, m in enumerate(trajectory) if m.get("role") == "assistant" and texts(m)]


SENTENCE_END = re.compile(r"[。！？!?\n]")


def cut_short(text: str) -> str:
    """The answer stopped in the middle of a sentence: the first sentence of 12
    characters or more from 40% of the answer on (else its longest), cut in
    half (cut at 40% alone, an answer could stop right where a sentence ends
    and read as complete)."""
    spans, start = [], 0
    for end in [m.start() for m in SENTENCE_END.finditer(text)] + [len(text)]:
        if end > start:
            spans.append((start, end))
        start = end + 1
    at = len(text) * 2 // 5
    start, end = next(((a, b) for a, b in spans if b > at and b - a >= 12), max(spans, key=lambda s: s[1] - s[0]))
    return text[:start + (end - start) // 2].rstrip("，、：；,;: ")


def corrupt(trajectory: list[dict], other: list[dict], how: str) -> list[dict]:
    """A copy whose answers fail the answer check for sure: the last turn's
    answer stops mid-sentence (cut), is another card's last answer (swap), or
    every answer is another card's (swap_all)."""
    def replaced(index: int, text: str) -> dict:
        message = trajectory[index]
        kept = [x for x in message["content"] if not (isinstance(x, dict) and x.get("type") == "text")]
        return {**message, "content": [*kept, {"type": "text", "text": text}]}

    mine, theirs = answers(trajectory), ["\n".join(texts(other[i])) for i in answers(other)]
    out = list(trajectory)
    if how == "cut":
        out[mine[-1]] = replaced(mine[-1], cut_short("\n".join(texts(trajectory[mine[-1]])).strip()))
    elif how == "swap":
        out[mine[-1]] = replaced(mine[-1], theirs[-1])
    else:
        for n, index in enumerate(mine):
            out[index] = replaced(index, theirs[n % len(theirs)])
    return out


def validate(args: argparse.Namespace, server: run.Server) -> None:
    found = [(job, task, record) for job, task, record in records(args.run, args.task_root)
             if answers(record["trajectory"]) and not failures(record, family_checks(task["family"]))][:args.limit]

    def one(index: int) -> dict:
        job, task, record = found[index]
        original = verdict(server.port, task, record)
        # Only an accepted original tells anything: rejecting the copies of a
        # rejected one proves nothing (a judge rejecting all would pass).
        if not original["passed"]:
            return {"job": job, "accepted": False, "failed": original.get("judge", [{}])[-1].get("failed", {})}
        other = found[(index + 1) % len(found)][2]["trajectory"]
        slipped = [how for how in CORRUPTIONS
                   if verdict(server.port, task, {**record, "trajectory": corrupt(record["trajectory"], other, how)})["passed"]]
        return {"job": job, "accepted": True, "slipped": slipped}

    details, accepted, slipped = [], 0, []
    with ThreadPoolExecutor(server.parallel) as pool:
        for detail in pool.map(one, range(len(found))):
            details.append(detail)
            accepted += detail["accepted"]
            slipped += detail.get("slipped", [])
            print(f"{detail['job']}: originals accepted {accepted}/{len(details)}, "
                  f"corrupted rejected {accepted * len(CORRUPTIONS) - len(slipped)}/{accepted * len(CORRUPTIONS)}",
                  flush=True)
    trials = accepted * len(CORRUPTIONS)
    summary = {"conversations": len(found), "originals_accepted": accepted, "corrupted": trials,
               "corrupted_rejected": trials - len(slipped),
               "slipped": {how: slipped.count(how) for how in CORRUPTIONS},
               "trusted": accepted >= MIN_ACCEPTED and trials - len(slipped) >= REJECT_NEEDED * trials}
    (args.run / "judge-validation.json").write_text(json.dumps({**summary, "details": details}, ensure_ascii=False),
                                                    encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    if accepted < MIN_ACCEPTED:
        raise SystemExit(f"the judge accepted only {accepted} originals (needs {MIN_ACCEPTED} to be tested)")
    if not summary["trusted"]:
        raise SystemExit(f"the judge rejected only {summary['corrupted_rejected']}/{trials} corrupted conversations "
                         f"(needs {REJECT_NEEDED:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("judge", "validate"))
    parser.add_argument("run", type=Path)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--config", required=True, help="the judge's bench config (configs.json)")
    parser.add_argument("--port", type=int, default=18489)
    parser.add_argument("--limit", type=int, default=30, help="validate: conversations to use")
    parser.add_argument("--parallel", type=int, default=3,
                        help="conversations judged side by side (fewer if the shared GPU holds fewer)")
    parser.add_argument("--checks-only", action="store_true",
                        help="judge: no judge model (it failed validation); the checks decide")
    args = parser.parse_args()
    if args.checks_only:
        judge(args, None)
        return
    config = json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    (args.run / "judge").mkdir(exist_ok=True)
    server = run.Server(config, args.run / "judge", args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    try:
        {"judge": judge, "validate": validate}[args.command](args, server)
    finally:
        server.close()


if __name__ == "__main__":
    main()
