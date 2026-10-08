"""The owner's own conversations as scenario cards.

The owner chats with the Agent on the laptop and hands the exports over
(2026-10-08); they are the goals, never the text. The teacher reads one
exported conversation and writes a card for the same need and the mistakes the
Agent made in it, then variant cards of the same ability on other topics. A
quarter of the conversations (by a hash of the file) are held out: their card
goes to the eval cards only, without variants, so the eval measures the
owner's real needs on cards no training card came from.

    python bench/scenario_exports.py --config teacher-38-iq3 ../SilentTalk/archive/EdgeAgent-*.txt
    (then scenario_cards.py files and tasks, as for every card)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)
import scenario_cards as cards  # noqa: E402

HELD_OUT_EVERY = 4
HEADER = re.compile(r"^【(使用者|Agent|工具|狀態|批准)】\s*$", re.MULTILINE)

EXPORT_PROMPT = """下面是使用者和 AI 助理「機魂」的真實對話紀錄。助理是使用者筆電上的個人代理，就是要被改進的對象；它在這段對話裡可能犯了錯。

{transcript}

請根據這段對話寫一張「情境卡」：之後會由另一個模型照卡扮演這位使用者，和改進後的助理重新對話一次。

能力分類（選一個最接近的）：
{families}

只輸出一個 JSON 物件，欄位如下：
- "family"：上面的分類代號（一個英文字母）。
- "persona"：這位使用者是什麼樣的人、講話習慣（從對話推斷，一句話）。
- "opening"：開場怎麼起頭（一句話）。
- "goal"：使用者真正想得到的東西（模擬使用者知道，但不會原句說出來）。
- "plan"：2 到 5 個步驟，使用者怎麼一步步問、助理答偏時怎麼反應；參考這位使用者在紀錄裡真的怎麼問、怎麼糾正、怎麼催。
- "must"：1 到 5 條，好的助理在這段對話裡一定要做到、看對話紀錄就能判斷的事，特別是紀錄裡的助理沒做到的。
- "must_not"：1 到 4 條，紀錄裡的助理犯過、看對話紀錄就能判斷的錯。
- "max_turns"：使用者最多說幾則（2 到 8 的整數）。
{web_rule}
要求：
- 全部用台灣繁體中文；不要照抄使用者的原句，用自己的話寫。
- 人名、公司、帳號、路徑、金額這類私人資訊一律換成虛構的或省略。
- 使用者可以要助理處理工作資料夾裡已有的檔案（檔案內容之後另外寫，這張卡不用給）；助理看不到工作資料夾以外的地方。"""

VARIANT_PROMPT = """下面是一張情境卡（另一個模型會照卡扮演使用者，和 AI 助理「機魂」多輪對話）：

{card}

請寫一張新卡：測同一個能力、同樣的陷阱、同樣的使用者講話習慣，但主題換成「{topic}」，內容和步驟都要重新想，不要只換名詞。
欄位和上面那張完全一樣（不含 family），只輸出一個 JSON 物件。
{web_rule}
全部用台灣繁體中文；虛構就整組虛構，不要把真實的代號或名稱配上錯的資料。"""


def transcript(text: str) -> str:
    """An export as the teacher reads it: who said what, tools in one line each."""
    parts = HEADER.split(text)
    lines = []
    for kind, body in zip(parts[1::2], parts[2::2], strict=True):
        body = body.strip()
        if kind == "使用者":
            lines.append(f"使用者：{body}")
        elif kind == "Agent":
            lines.append(f"助理：{body}")
        elif kind == "工具":
            first, _, rest = body.partition("\n")
            lines.append(f"（工具 {first.strip()}：{' '.join(rest.split())[:200]}）")
        elif kind == "批准":
            lines.append(f"（{body}）")
    return "\n".join(lines)


def held_out(data: bytes) -> bool:
    return int(hashlib.sha256(data).hexdigest(), 16) % HELD_OUT_EVERY == 0


def family_list(spec: dict) -> str:
    return "\n".join(f"{key}．{f['name']}：{f['ability']}" for key, f in sorted(spec["families"].items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exports", nargs="+", type=Path)
    parser.add_argument("--config", required=True, help="the teacher's bench config (configs.json)")
    parser.add_argument("--variants", type=int, default=3, help="variant cards per training conversation")
    parser.add_argument("--port", type=int, default=18389)
    parser.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()
    spec = cards.families()
    config = json.loads((cards.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    paths = {split: cards.OUT / f"{split}-cards.jsonl" for split in cards.SPLITS}
    kept = {split: [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.is_file() else [] for split, path in paths.items()}
    for split, found in kept.items():
        if found and found[0]["source"]["families_version"] != spec["version"]:
            raise SystemExit(f"{paths[split]} was made from another families version; start a new file")
    done = {c["source"].get("export") for found in kept.values() for c in found}
    todo = [(p, p.read_bytes()) for p in args.exports]
    todo = [(p, data) for p, data in todo if hashlib.sha256(data).hexdigest()[:12] not in done]
    print(f"{len(todo)} of {len(args.exports)} conversations to turn into cards", flush=True)
    if not todo:
        return
    run_dir = cards.OUT / "server-exports"
    run_dir.mkdir(parents=True, exist_ok=True)
    server = run.Server(config, run_dir, args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    lock = threading.Lock()
    today = datetime.date.today().isoformat()
    topics = cards.topics_for("train", spec)

    def ask_card(prompt: str, family_id: str | None) -> dict | None:
        for _ in range(3):
            card = cards.parse_card(cards.ask(server.port, prompt))
            card = cards.traditional(card) if card is not None else None
            family = family_id or (card or {}).get("family")
            if card is not None and family in spec["families"] \
                    and not cards.problems(card, family, spec["families"][family]):
                card["family"] = family
                return card
        return None

    def keep(card: dict, split: str, export: str, variant_of: str | None) -> str:
        with lock:
            n = sum(c["source"].get("from") == "owner" for c in kept[split]) + 1
            card_id = f"{split}-{card['family']}-o{n:03d}"
            card.update({"id": card_id, "topic": card.get("topic") or "使用者的對話",
                         "source": {"teacher": config["model"], "families_version": spec["version"],
                                    "prompt": hashlib.sha256((EXPORT_PROMPT + VARIANT_PROMPT).encode()).hexdigest()[:12],
                                    "today": today, "from": "owner", "export": export, "variant_of": variant_of}})
            kept[split].append(card)
            with paths[split].open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(card, ensure_ascii=False) + "\n")
            return card_id

    def one(item: tuple[Path, bytes]) -> None:
        path, data = item
        export = hashlib.sha256(data).hexdigest()[:12]
        split = "eval" if held_out(data) else "train"
        prompt = EXPORT_PROMPT.format(transcript=transcript(data.decode("utf-8-sig", errors="replace")),
                                      families=family_list(spec), web_rule=cards.WEB_RULES["optional"]
                                      + "（選 J 的話，pages 改照下面的格式，並加上 apis：）\n" + cards.WEB_RULES["api"])
        card = ask_card(prompt, None)
        if card is None:
            print(f"{path.name}: no usable card", flush=True)
            return
        card_id = keep(card, split, export, None)
        print(f"{path.name} -> {card_id} ({split})", flush=True)
        if split == "eval":
            return
        base = {k: v for k, v in card.items() if k not in ("id", "source", "family", "topic")}
        rng = random.Random(export)
        for topic in rng.sample(topics, min(args.variants, len(topics))):
            rule = cards.WEB_RULES[spec["families"][card["family"]]["web"]] or cards.WEB_RULES["optional"]
            variant = ask_card(VARIANT_PROMPT.format(card=json.dumps(base, ensure_ascii=False, indent=1),
                                                     topic=topic, web_rule=rule),
                               card["family"])
            if variant is not None:
                variant["topic"] = topic
                print(f"   variant {keep(variant, 'train', export, card_id)} {topic}", flush=True)

    try:
        with ThreadPoolExecutor(server.parallel) as pool:
            list(pool.map(one, todo))
    finally:
        server.close()


if __name__ == "__main__":
    main()
