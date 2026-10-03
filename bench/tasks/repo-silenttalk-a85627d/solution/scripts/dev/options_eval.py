"""Options mode (選項) A/B: which set of four choices sounds like the character itself?

Usage (from the repo root, llama-server on PATH):
    python scripts/dev/options_eval.py generate --rev 75ea075 --tag thought
    python scripts/dev/options_eval.py judge --tag thought
    python scripts/dev/options_eval.py generate --cpu ...   # while the GPU is busy

At every other line someone else says in the M1 test set (turn_eval_data.jsonl,
held out from training), the options code at `--rev` and the working tree
both write their four choices for the same chat, on one llama-server. The
judge (`claude -p`, dev only: the test set is synthetic, never real
conversations) sees the character sheet, the last lines and the two sets as
A/B, twice with the order swapped, without lane names or the thought, and
scores each set: its own point of view, answers the right line, sounds like
the character, natural speech, how many of the four are worth picking.
Results land in .runtime/dev/options_eval/<tag>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import turn_eval as te

OUT_DIR = te.PROJECT_DIR / ".runtime" / "dev" / "options_eval"
PORT = 18432
SCORES = ("own", "grounded", "voice", "natural", "usable")

JUDGE_PROMPT = """你是對話品質評審。下面是一個 Discord 語音聊天角色的人設，以及對話裡的幾個時間點。每個時間點有 A、B 兩組選項，每組最多四句，是替這個角色想的、可以直接說出口的話，玩家會挑一句讓角色說。

【角色人設】
{sheet}

每組都評：
- own 1-5：有沒有這個角色自己的想法、立場或想說的事，而不是只附和、或機械地對最後一句做反應
- grounded 1-5：接得上對話：回對人、回對事，不答非所問，不重複別人剛說的話
- voice 1-5：像這個角色本人：口吻、跟在場的人的關係、個性前後一致；口頭禪硬塞、每句都在講同一個特質也扣分
- natural 1-5：像台灣人在語音裡會直接說出口的話
- usable 0-4：四句裡有幾句你會想挑來說（空的不算）
最後 better：整體哪一組比較好，"A"、"B" 或 "tie"

{items}

只輸出 JSON 陣列，不要其他文字，長度 {k}，依序對應：[{{"id": 1, "A": {{"own": 3, "grounded": 4, "voice": 3, "natural": 4, "usable": 2}}, "B": {{"own": 4, "grounded": 4, "voice": 4, "natural": 4, "usable": 3}}, "better": "B"}}, ...]"""


def lanes_of(suggestions: Any) -> list[str]:
    return [text for _, text in suggestions.items()]


async def generate(args: argparse.Namespace) -> None:
    from silenttalk.copilot import llm as llm_module
    from silenttalk.copilot import persona as persona_module
    from silenttalk.copilot.types import ConversationTurn

    test_set = te.load_test_set()
    old = te.load_old_modules(args.rev)
    te.ensure_llama_server_on_path()
    config = te.copilot_config()
    if args.cpu:
        config["llm_gpu_layers"] = 0
    model_path = te.PROJECT_DIR / ".runtime" / "models" / "llm" / (args.model or config["llm_model"])
    te.EVAL_PORT = PORT
    new_model = te.make_model(llm_module, model_path, config)
    # Same server for both: the old client only builds different prompts.
    old_model = te.make_model(old.llm, model_path, config)
    # The app's 15 s limit is for a live chat; a shared or CPU-only eval box
    # can be slower, and one timeout should not cost the whole run.
    new_model._request_timeout = old_model._request_timeout = 180.0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.tag}.json"
    records: list[dict[str, Any]] = []
    if out.is_file():  # resume: conversations already in the file are done
        records = json.loads(out.read_text(encoding="utf-8"))["records"]
    done = {r["conv"] for r in records}

    def save() -> None:
        out.write_text(json.dumps({
            "rev": args.rev, "model": model_path.name, "cpu": args.cpu, "records": records,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    await new_model.start()
    only = set(args.only.split(",")) if args.only else None
    try:
        for conv in test_set.conversations:
            if (only and conv.id not in only) or conv.id in done:
                continue
            new_persona = te.resolve_persona(persona_module, test_set, conv.persona_id)
            old_persona = te.resolve_persona(old.persona, test_set, conv.persona_id)
            new_model.set_persona(new_persona)
            old_model.set_persona(old_persona)
            # Each conversation starts fresh, even when the persona repeats.
            for model in (new_model, old_model):
                if hasattr(model, "_last_thought"):
                    model._last_thought = ""
            ids = te.speaker_ids(conv)
            new_turns: list[Any] = []
            old_store = old.context.ConversationStore()
            others = 0
            for index, line in enumerate(conv.lines):
                if line.get("self"):
                    new_turns.append(ConversationTurn(0, new_persona.name, line["text"], is_self=True))
                    old_store.append(old.types.ConversationTurn(
                        0, old_persona.name, line["text"], is_self=True
                    ))
                    continue
                new_turns.append(ConversationTurn(ids[line["speaker"]], line["speaker"], line["text"]))
                old_store.append(old.types.ConversationTurn(
                    ids[line["speaker"]], line["speaker"], line["text"]
                ))
                others += 1
                if others % args.every:
                    continue
                started = time.perf_counter()
                new = await new_model.suggest(
                    turns=list(new_turns), trigger_speaker=line["speaker"], trigger_text=line["text"]
                )
                new_ms = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                before = await old_model.suggest(
                    context=old_store.render_for_prompt(),
                    trigger_speaker=line["speaker"],
                    trigger_text=line["text"],
                )
                old_ms = (time.perf_counter() - started) * 1000
                records.append({
                    "conv": conv.id, "index": index, "persona": conv.persona_id,
                    "old": lanes_of(before), "new": lanes_of(new), "thought": new.thought,
                    "old_ms": round(old_ms), "new_ms": round(new_ms),
                })
            save()
            print(f"  {conv.id}: {sum(r['conv'] == conv.id for r in records)} points", flush=True)
    finally:
        await new_model.stop()
    print(f"saved {out} ({len(records)} points)")


def judge(args: argparse.Namespace) -> None:
    from silenttalk.copilot import persona as persona_module

    path = OUT_DIR / f"{args.tag}.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    records = result["records"]
    test_set = te.load_test_set()
    convs = {c.id: c for c in test_set.conversations}
    rng = random.Random(args.seed)
    by_persona: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        record["verdicts"] = []
        by_persona[record["persona"]].append(record)
    for persona_id, items in sorted(by_persona.items()):
        persona = te.resolve_persona(persona_module, test_set, persona_id)
        for chunk_start in range(0, len(items), args.batch):
            chunk = items[chunk_start:chunk_start + args.batch]
            first = [rng.random() < 0.5 for _ in chunk]  # True: new is A
            for new_is_a in (first, [not f for f in first]):
                rendered = []
                for n, (record, a_new) in enumerate(zip(chunk, new_is_a, strict=True), 1):
                    conv = convs[record["conv"]]
                    window = conv.lines[max(0, record["index"] - 7):record["index"] + 1]
                    chat = "\n".join(
                        f"{persona.name}（角色）：{line['text']}" if line.get("self")
                        else f"{line['speaker']}：{line['text']}"
                        for line in window
                    )
                    sets = {"A": record["new" if a_new else "old"], "B": record["old" if a_new else "new"]}
                    shown = "\n".join(
                        f"{name} 組：" + "／".join(text for text in lanes if text) if any(lanes)
                        else f"{name} 組：（沒有選項）"
                        for name, lanes in sets.items()
                    )
                    rendered.append(f"=== 第 {n} 個時間點 ===\n{chat}\n{shown}")
                prompt = JUDGE_PROMPT.format(
                    sheet=te.persona_sheet(persona), items="\n\n".join(rendered), k=len(chunk)
                )
                verdicts = te.run_judge(prompt, args.judge_model)
                for n, (record, a_new) in enumerate(zip(chunk, new_is_a, strict=True), 1):
                    verdict = next((v for v in verdicts if v.get("id") == n), None)
                    if not verdict:
                        continue
                    better = verdict.get("better")
                    record["verdicts"].append({
                        "new": verdict.get("A" if a_new else "B", {}),
                        "old": verdict.get("B" if a_new else "A", {}),
                        "better": "tie" if better not in ("A", "B")
                        else ("new" if (better == "A") == a_new else "old"),
                    })
            print(f"  judged {persona_id} {chunk_start + len(chunk)}/{len(items)}", flush=True)
    result["judge_model"] = args.judge_model
    result["summary"] = summarize(records)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_summary(result["summary"])


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = [v for r in records for v in r.get("verdicts", [])]
    out: dict[str, Any] = {"points": len(records), "verdicts": len(verdicts)}
    for side in ("old", "new"):
        for key in SCORES:
            values = [float(v[side][key]) for v in verdicts if key in v.get(side, {})]
            out[f"{side}_{key}"] = round(statistics.fmean(values), 2) if values else None
    wins = [v["better"] for v in verdicts]
    out["new_wins"] = round(wins.count("new") / len(wins), 3) if wins else None
    out["old_wins"] = round(wins.count("old") / len(wins), 3) if wins else None
    both = [r["verdicts"] for r in records if len(r.get("verdicts", [])) == 2]
    out["order_consistent"] = (
        round(sum(a["better"] == b["better"] for a, b in both) / len(both), 3) if both else None
    )
    for side in ("old", "new"):
        out[f"{side}_empty_lanes"] = round(statistics.fmean(
            sum(not text for text in r[side]) for r in records
        ), 2) if records else None
        out[f"{side}_ms_median"] = statistics.median(r[f"{side}_ms"] for r in records) if records else None
    return out


def print_summary(summary: dict[str, Any]) -> None:
    print(f"{summary['points']} points, {summary['verdicts']} verdicts "
          f"(order-consistent {summary['order_consistent']})")
    print(f"better: new {summary['new_wins']}  old {summary['old_wins']}")
    print("| | " + " | ".join(SCORES) + " | empty lanes | median ms |")
    print("|---|" + "---|" * (len(SCORES) + 2))
    for side in ("old", "new"):
        print(f"| {side} | " + " | ".join(str(summary[f"{side}_{k}"]) for k in SCORES)
              + f" | {summary[f'{side}_empty_lanes']} | {summary[f'{side}_ms_median']} |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--rev", required=True, help="git revision with the old options code")
    gen.add_argument("--tag", required=True)
    gen.add_argument("--model", help="gguf in .runtime/models/llm/ (default: config.toml)")
    gen.add_argument("--every", type=int, default=2, help="options at every Nth line of others")
    gen.add_argument("--only", help="comma-separated conversation ids")
    gen.add_argument("--cpu", action="store_true", help="no GPU layers (GPU busy)")
    jud = sub.add_parser("judge")
    jud.add_argument("--tag", required=True)
    jud.add_argument("--judge-model", default="sonnet")
    jud.add_argument("--batch", type=int, default=8)
    jud.add_argument("--seed", type=int, default=7)
    rep = sub.add_parser("report")
    rep.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.command == "generate":
        asyncio.run(generate(args))
    elif args.command == "judge":
        judge(args)
    else:
        result = json.loads((OUT_DIR / f"{args.tag}.json").read_text(encoding="utf-8"))
        print_summary(result["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
