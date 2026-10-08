"""Blind pairwise judge for the machine spirit's lines (SilentTalk's
scripts/dev/line_ab.py pairs): which of two lines is the better next line.

The judge is Gemma 4 31B (owner, 2026-10-07: not the teacher's family, and
never Claude). Each pair is judged twice with A and B swapped; only a
verdict that holds both ways counts, anything else is a tie. Before its
verdicts are used it must prove itself: on pairs where one side is broken
(a line from another chat, or a line cut to its first few characters) it
must pick the intact line at least 90% of the time.

    python bench/pair_judge.py <out>.pairs.json --config judge-gemma
writes <out>.verdicts.json ({"n": "A" | "B" | "tie"}) and
<out>.judge-validation.json.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)

REJECT_NEEDED = 0.9
PROMPT = """你是嚴格的評審。下面是一段 Discord 聊天（多半是語音轉成的文字），以及「機魂」接下來可能說的兩句話 A 和 B。機魂是陪主人和朋友聊天的 AI 角色，設定來自戰鎚 40K 的機魂。判斷哪一句是更好的下一句。

好的下一句：
1. 接得上：回應最後說話的人，內容合理，不答非所問。
2. 像這個角色：角色感來自它在意的事、想要的東西和想事情的方式，不是口頭禪或硬塞的專有名詞。
3. 適合說出口：口語、自然、簡短，台灣繁體中文用語。
4. 不捏造事實、不說錯話、不重複別人剛說的。

聊天紀錄：
{chat}

A：{a}
B：{b}

只輸出一個 JSON 物件：{{"better": "A" 或 "B" 或 "tie", "why": "20 字內"}}"""


def ask(port: int, chat: str, a: str, b: str) -> str:
    body = json.dumps({"messages": [{"role": "user", "content": PROMPT.format(chat=chat, a=a, b=b)}],
                       "temperature": 0, "max_tokens": 200,
                       "chat_template_kwargs": {"enable_thinking": False}}, ensure_ascii=False).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        text = json.loads(response.read())["choices"][0]["message"].get("content") or ""
    match = re.search(r'"better"\s*:\s*"(A|B|tie)"', text)
    return match.group(1) if match else "tie"


def verdict(port: int, chat: str, a: str, b: str) -> str:
    """A or B only when the judge says so both ways round."""
    first, second = ask(port, chat, a, b), ask(port, chat, b, a)
    swapped = {"A": "B", "B": "A"}.get(second, "tie")
    return first if first == swapped and first != "tie" else "tie"


def broken(pairs: list[dict], rng: random.Random) -> list[tuple[str, str, str, str]]:
    """(chat, intact, broken, how) for validation: the intact line is the
    pair's A; the broken one is another chat's line, or A cut short."""
    found = []
    for pair in pairs:
        other = rng.choice([p for p in pairs if p["chat"] != pair["chat"]] or [pair])
        found.append((pair["chat"], pair["A"], other["B"], "another chat's line"))
        if len(pair["A"]) > 8:
            found.append((pair["chat"], pair["A"], pair["A"][:4], "cut short"))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pairs", type=Path)
    parser.add_argument("--config", required=True, help="the judge's bench config (configs.json)")
    parser.add_argument("--port", type=int, default=18491)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--limit", type=int, default=20, help="pairs used to validate the judge")
    args = parser.parse_args()
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
    out = args.pairs.with_name(args.pairs.name.replace(".pairs.json", ""))
    config = json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    folder = out.with_suffix(".judge")
    folder.mkdir(parents=True, exist_ok=True)
    server = run.Server(config, folder, args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    try:
        rng = random.Random(11)
        trials = broken(rng.sample(pairs, min(args.limit, len(pairs))), rng)
        with ThreadPoolExecutor(server.parallel) as pool:
            picked = list(pool.map(lambda t: verdict(server.port, t[0], t[1], t[2]), trials))
        right = sum(p == "A" for p in picked)
        summary = {"trials": len(trials), "intact_picked": right, "trusted": right >= REJECT_NEEDED * len(trials)}
        out.with_suffix(".judge-validation.json").write_text(json.dumps(summary), encoding="utf-8")
        print(json.dumps(summary), flush=True)
        if not summary["trusted"]:
            raise SystemExit(f"the judge picked the intact line in only {right}/{len(trials)} broken pairs")
        with ThreadPoolExecutor(server.parallel) as pool:
            verdicts = dict(zip((str(p["n"]) for p in pairs),
                                pool.map(lambda p: verdict(server.port, p["chat"], p["A"], p["B"]), pairs)))
        out.with_suffix(".verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")
        print(f"{len(verdicts)} verdicts -> {out.with_suffix('.verdicts.json')}", flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
