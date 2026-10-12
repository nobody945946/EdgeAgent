"""Blind pairwise judge for the machine spirit's lines (SilentTalk's
scripts/dev/line_ab.py pairs): which of two lines is the better next line.

The judge is Gemma 4 31B (owner, 2026-10-07: not the teacher's family, and
never Claude). It thinks before it answers: asked for a letter at once, it
gave whichever line stood first with near certainty (2026-10-12: a line cut
to four characters won whenever it was A, and another chat's line beat the
data's own both ways round). Each pair is judged twice with A and B
swapped; a line wins only when it wins both ways round, anything else is a
tie. Before its verdicts are used it must prove
itself: against the data's own line for the chat (the pair's "ref", the
teacher's line through the character gate; a model's line can be poor
itself, and a fragment of a line that only repeats the last speaker is no
worse) it must reject that same kind of line from another chat, and the line
cut to its first few characters, at least 90% of the time.

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
MARGIN = 0.1  # past even, of a line's score averaged over both orders
PROMPT = """你是嚴格的評審。下面是一段 Discord 聊天（多半是語音轉成的文字），以及「機魂」接下來可能說的兩句話 A 和 B。機魂是陪主人和朋友聊天的 AI 角色，設定來自戰鎚 40K 的機魂。判斷哪一句是更好的下一句。

好的下一句：
1. 接得上：回應最後說話的人，內容合理，不答非所問。
2. 像這個角色：角色感來自它在意的事、想要的東西和想事情的方式，不是口頭禪或硬塞的專有名詞。
3. 適合說出口：口語、自然、簡短，台灣繁體中文用語；是說完的一句話，不是說到一半。
4. 不捏造事實、不說錯話、不重複別人剛說的。

聊天紀錄：
{chat}

A：{a}
B：{b}

想清楚以後，最後只回答一個字母：A 或 B。"""


def ask(port: int, chat: str, a: str, b: str) -> float:
    """1.0 when the judge, after thinking it over, answers A; 0.0 for B;
    0.5 when it gives neither."""
    body = json.dumps({"messages": [{"role": "user", "content": PROMPT.format(chat=chat, a=a, b=b)}],
                       "temperature": 0, "max_tokens": 8192,
                       "chat_template_kwargs": {"enable_thinking": True}}, ensure_ascii=False).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=1800) as response:
        text = json.loads(response.read())["choices"][0]["message"].get("content") or ""
    letters = re.findall(r"[AB]", text)
    return {"A": 1.0, "B": 0.0}.get(letters[-1], 0.5) if letters else 0.5


def both_ways(port: int, chat: str, a: str, b: str) -> tuple[float, float]:
    """The judge's score for a, shown as A and shown as B."""
    return ask(port, chat, a, b), 1 - ask(port, chat, b, a)


def agreed(answers: tuple[float, float]) -> str:
    """A (the first line) or B by its score averaged over both orders: with
    letters, only a line that wins both ways round."""
    mean = sum(answers) / 2
    return "A" if mean > 0.5 + MARGIN else "B" if mean < 0.5 - MARGIN else "tie"


def verdict(port: int, chat: str, a: str, b: str) -> str:
    return agreed(both_ways(port, chat, a, b))


def broken(pairs: list[dict], rng: random.Random) -> list[tuple[str, str, str, str]]:
    """(chat, intact, broken, how) for validation: the intact line is the
    pair's reference; the broken one is another chat's reference, or this
    one cut short."""
    found = []
    for pair in pairs:
        other = rng.choice([p for p in pairs if p["chat"] != pair["chat"]] or [pair])
        found.append((pair["chat"], pair["ref"], other["ref"], "another chat's line"))
        if len(pair["ref"]) > 8:
            found.append((pair["chat"], pair["ref"], pair["ref"][:4], "cut short"))
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
            answers = list(pool.map(lambda t: both_ways(server.port, t[0], t[1], t[2]), trials))
        picked = [agreed(a) for a in answers]
        right = sum(p == "A" for p in picked)
        # Per kind of break, every trial it missed with the intact line's
        # probability in each order (one over and one under even is the slot
        # deciding, not the line), so a failed check says whether the judge
        # or the break is at fault.
        kinds = {how: [sum(1 for t, p in zip(trials, picked, strict=True) if t[3] == how and p == "A"),
                       sum(1 for t in trials if t[3] == how)] for how in sorted({t[3] for t in trials})}
        missed = [{"how": t[3], "chat": t[0][-300:], "intact": t[1], "broken": t[2], "answers": a}
                  for t, a, p in zip(trials, answers, picked, strict=True) if p != "A"]
        same_slot = sum((a[0] - 0.5) * (a[1] - 0.5) < 0 for a in answers)
        summary = {"trials": len(trials), "intact_picked": right, "trusted": right >= REJECT_NEEDED * len(trials),
                   "by_kind": kinds, "same_slot": same_slot, "missed": missed}
        out.with_suffix(".judge-validation.json").write_text(json.dumps(summary), encoding="utf-8")
        print(json.dumps({k: summary[k] for k in ("trials", "intact_picked", "trusted", "by_kind", "same_slot")}),
              flush=True)
        if not summary["trusted"]:
            raise SystemExit(f"the judge picked the intact line in only {right}/{len(trials)} broken pairs")
        with ThreadPoolExecutor(server.parallel) as pool:
            verdicts = dict(zip((str(p["n"]) for p in pairs),
                                pool.map(lambda p: verdict(server.port, p["chat"], p["A"], p["B"]), pairs),
                                strict=True))
        out.with_suffix(".verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")
        print(f"{len(verdicts)} verdicts -> {out.with_suffix('.verdicts.json')}", flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
