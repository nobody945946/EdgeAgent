"""Automatic checks on 求知儀式 rounds (bench/grill_eval.py output), per config.

What needs no judge: did the round produce questions at all (or was it
stopped, e.g. by the repeat guard), did it look at the workspace when there
was one, are the recommendations real (not "please describe..."), and is it
in Traditional Chinese. Runs with SilentTalk's venv for OpenCC.

    /home/clawd/SilentTalk/.venv/bin/python bench/grill_auto.py RUN [--round 1]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import opencc

ROOT = Path(__file__).resolve().parents[1]
S2T = opencc.OpenCC("s2t")
# A "recommendation" that hands the question back to the user.
PLACEHOLDER = re.compile(
    r"Your recommended|[Pp]lease|[Dd]escribe|[Pp]rovide|請(提供|說明|描述|告訴|回答)|你的建議答案|看你"
)


def scenario_files() -> dict[str, bool]:
    rows = [json.loads(line) for line in (ROOT / "bench/grill/scenarios.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["id"]: bool(row.get("files")) for row in rows if "id" in row}


def check(text: str) -> dict:
    questions = len(re.findall(r"❓", text))
    recs = re.findall(r"➡️([^\n]*)", text)
    cjk = [c for c in text if "一" <= c <= "鿿"]
    latin = re.findall(r"[A-Za-z]", re.sub(r"`[^`]*`", "", text))
    simplified = sum(a != b for a, b in zip("".join(cjk), S2T.convert("".join(cjk))))
    return {
        "produced": questions > 0,
        "questions": questions,
        "real_recs": sum(1 for r in recs if r.strip() and not PLACEHOLDER.search(r)),
        "chinese": len(cjk) / max(1, len(cjk) + len(latin)) > 0.5,
        "simplified_chars": simplified,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--round", type=int, default=1)
    args = parser.parse_args()
    has_files = scenario_files()
    configs: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(args.run.glob("*/*.json")):
        state = json.loads(path.read_text(encoding="utf-8"))
        if len(state["rounds"]) < args.round:
            continue
        rnd = state["rounds"][args.round - 1]
        config = re.sub(r"-s\d+$", "", path.parent.name)
        result = check(rnd["text"]) | {"scenario": state["scenario"], "seconds": rnd["seconds"],
                                        "tool_calls": len(rnd["tool_calls"])}
        result["looked"] = result["tool_calls"] > 0 if has_files[state["scenario"]] else None
        configs[config].append(result)
    print(f"round {args.round}: config | runs | produced | 中文 | 簡體字/輪 | 題數 | 真建議/題 | 有檔案時有查 | 秒")
    for config, rows in sorted(configs.items()):
        produced = [r for r in rows if r["produced"]]
        looked = [r["looked"] for r in rows if r["looked"] is not None]
        questions = sum(r["questions"] for r in produced)
        print(f"{config:10} | {len(rows):3} | {len(produced) / len(rows):4.0%} | "
              f"{sum(r['chinese'] for r in produced) / max(1, len(produced)):4.0%} | "
              f"{sum(r['simplified_chars'] for r in produced) / max(1, len(produced)):5.1f} | "
              f"{questions / max(1, len(produced)):4.1f} | "
              f"{sum(r['real_recs'] for r in produced) / max(1, questions):4.0%} | "
              f"{sum(looked) / max(1, len(looked)):4.0%} | "
              f"{sum(r['seconds'] for r in rows) / len(rows):4.1f}")


if __name__ == "__main__":
    main()
