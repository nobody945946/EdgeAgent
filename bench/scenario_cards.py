"""Scenario cards: multi-turn conversations the Agent must handle well.

The owner-approved design (2026-10-07): each family in
bench/scenarios/families.json is an ability (following the conversation,
researching and summarizing, delivering everything asked, knowing itself,
auditing its own actions, tool discipline, knowing when to look things up,
chores in the folder). The open teacher writes every card, crossing a family
with a topic; this script only validates them, drops near-duplicates and
turns each into a bench task (kind "scenario") that run.py plays with a
simulated user (agent_runner.py). Eval cards use topics no training card
uses, so a gain on them is not memorized text.

    python bench/scenario_cards.py generate --config teacher-38 --split train --per-family 40
    python bench/scenario_cards.py tasks --split train
    (then: python bench/run.py --config teacher-38 --task-root .runtime/agentdata/scenarios/train-tasks
           --tasks '*' --repeats 1 --record --tag scen-train-...; judge with scenario_judge.py)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import random
import re
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)
from scenario_checks import check  # noqa: E402

BENCH = Path(__file__).resolve().parent
FAMILIES = BENCH / "scenarios" / "families.json"
OUT = run.ROOT / ".runtime/agentdata/scenarios"
SPLITS = ("train", "eval")
EVAL_EVERY = 4  # every 4th topic is held out for eval cards only
WEB_PORT = 8081  # web_fixture.py serves the pages under their own host names on this port
TIMEOUT_S = 1800

CARD_PROMPT = """你要為一個 AI 助理的訓練與測驗設計一張「情境卡」：一段多輪對話的劇本大綱，之後會由另一個模型照卡扮演使用者，和助理真的對話。

助理是誰：使用者筆電上的個人代理「機魂」（戰鎚 40K 機魂的角色），能用工具查網路（web_research）、在工作資料夾裡讀寫檔案、執行 PowerShell、找現成工具（tool_search）。今天是 {today}。

這張卡要測的能力（{family_id}．{name}）：{ability}
使用者通常怎麼做：{user}
助理常犯的錯：{traps}
主題：{topic}

只輸出一個 JSON 物件，欄位如下：
- "persona"：使用者是什麼樣的人、講話習慣（一句話）。
- "opening"：開場怎麼起頭（一句話，例如先閒聊什麼）。
- "goal"：使用者真正想得到的東西（模擬使用者知道，但不會原句說出來）。
- "plan"：2 到 5 個步驟，使用者怎麼一步步問、助理答偏時怎麼反應。
- "must"：1 到 5 條，助理在這段對話裡一定要做到、而且看對話紀錄就能判斷的事。
- "must_not"：1 到 4 條，助理絕對不能做、看對話紀錄就能判斷的事。
- "max_turns"：使用者最多說幾則（2 到 8 的整數）。
{web_rule}{files_rule}
要求：全部用台灣繁體中文；內容具體，不要空泛；must 和 must_not 要直接對應上面的能力和常犯的錯；不要出現真實人物的私人資訊。"""

WEB_RULES = {
    "fixture": '- "pages"：3 到 7 個假網頁（助理查網路時只會查到這些），每個是 {{"url", "title", "published"（YYYY-MM-DD，可省略）, "text"（200 到 900 字）}}。'
               "要包含答題需要的正確資料，也要有陷阱：同名但無關的頁面、過時的舊資料、論壇網友的說法。"
               "城市、公司、人物、數字請用虛構的，避免和真實世界混淆；遊戲或作品的設定可以照真實作品寫。\n",
    "optional": '- "pages"：如果這段對話需要查網路，給 3 到 7 個假網頁（格式同上述說明：url、title、published、text，要有正確資料和陷阱）；不需要就給空陣列 []。\n',
    "none": "",
}
FILES_RULE = ('- "files"：工作資料夾裡的檔案，物件形式 {"相對路徑": "檔案內容"}，2 到 6 個純文字檔，每個不超過 1500 字，'
              "內容要讓這件雜事有具體的東西可做。\n")


def families() -> dict:
    return json.loads(FAMILIES.read_text(encoding="utf-8"))


def topics_for(split: str, spec: dict) -> list[str]:
    held_out = spec["topics"][EVAL_EVERY - 1::EVAL_EVERY]
    return held_out if split == "eval" else [t for t in spec["topics"] if t not in held_out]


def prompt_for(family_id: str, family: dict, topic: str, today: str) -> str:
    return CARD_PROMPT.format(
        today=today, family_id=family_id, name=family["name"], ability=family["ability"],
        user=family["user"], traps=family["traps"], topic=topic,
        web_rule=WEB_RULES[family["web"]], files_rule=FILES_RULE if family_id == "H" else "")


def ask(port: int, prompt: str) -> str:
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "temperature": 0.9,
                       "max_tokens": 12000, "chat_template_kwargs": {"enable_thinking": True}}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read())["choices"][0]["message"].get("content") or ""


def parse_card(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else None
    except ValueError:
        return None


def _strings(value: object, low: int, high: int) -> bool:
    return isinstance(value, list) and low <= len(value) <= high and all(
        isinstance(v, str) and v.strip() for v in value)


def problems(card: dict, family_id: str, family: dict) -> list[str]:
    """Why a card cannot be used (empty when it can)."""
    found = [f"missing {key}" for key in ("persona", "opening", "goal")
             if not isinstance(card.get(key), str) or not card[key].strip()]
    if not _strings(card.get("plan"), 2, 5):
        found.append("plan must be 2-5 steps")
    if not _strings(card.get("must"), 1, 5):
        found.append("must must be 1-5 items")
    if not _strings(card.get("must_not"), 1, 4):
        found.append("must_not must be 1-4 items")
    if not isinstance(card.get("max_turns"), int) or not 2 <= card["max_turns"] <= 8:
        found.append("max_turns must be 2-8")
    pages = card.get("pages") or []
    if family["web"] == "fixture" and not 3 <= len(pages) <= 7:
        found.append("needs 3-7 pages")
    if family["web"] == "optional" and pages and not 3 <= len(pages) <= 7:
        found.append("pages must be none or 3-7")
    for page in pages:
        url = urllib.parse.urlsplit(str(page.get("url", "")) if isinstance(page, dict) else "")
        if not (isinstance(page, dict) and url.scheme in ("http", "https") and url.hostname
                and isinstance(page.get("title"), str) and isinstance(page.get("text"), str)
                and 50 <= len(page["text"]) <= 3000):
            found.append("a page needs url, title and 50-3000 characters of text")
            break
    files = card.get("files") or {}
    if family_id == "H" and not (isinstance(files, dict) and 2 <= len(files) <= 6 and all(
            isinstance(path, str) and isinstance(text, str) and len(text) <= 3000
            and not Path(path).is_absolute() and ".." not in Path(path).parts
            for path, text in files.items())):
        found.append("H needs 2-6 text files with relative paths")
    text = json.dumps(card, ensure_ascii=False)
    try:
        if check("zh_tw", {"trajectory": [{"role": "assistant", "content": [{"type": "text", "text": text}]}]}):
            found.append("Simplified characters")
    except ImportError:
        pass
    return found


def bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def near_duplicate(card: dict, kept: list[dict]) -> bool:
    mine = bigrams(card["goal"] + "".join(card["plan"]))
    for other in kept:
        theirs = bigrams(other["goal"] + "".join(other["plan"]))
        if mine and theirs and len(mine & theirs) / len(mine | theirs) > 0.5:
            return True
    return False


def generate(args: argparse.Namespace) -> None:
    spec = families()
    configs = json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))
    config = configs[args.config]
    out = OUT / f"{args.split}-cards.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    kept = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] if out.is_file() else []
    if kept and kept[0]["source"]["families_version"] != spec["version"]:
        raise SystemExit(f"{out} was made from families version {kept[0]['source']['families_version']}; "
                         "start a new file instead of mixing versions")
    run_dir = OUT / f"server-{args.split}"
    run_dir.mkdir(parents=True, exist_ok=True)
    server = run.Server(config, run_dir, 1, args.port)
    today = datetime.date.today().isoformat()
    rng = random.Random(f"{args.split}-{args.seed}")
    topics = topics_for(args.split, spec)
    try:
        for family_id in args.families or sorted(spec["families"]):
            family = spec["families"][family_id]
            have = sum(c["family"] == family_id for c in kept)
            tries = 0
            while have < args.per_family and tries < args.per_family * 3:
                tries += 1
                topic = rng.choice(topics)
                card = parse_card(ask(server.port, prompt_for(family_id, family, topic, today)))
                why = ["not JSON"] if card is None else problems(card, family_id, family)
                if not why and near_duplicate(card, [c for c in kept if c["family"] == family_id]):
                    why = ["near-duplicate"]
                if why:
                    print(f"{family_id} {topic}: dropped ({'; '.join(why)})", flush=True)
                    continue
                have += 1
                card_id = f"{args.split}-{family_id}-{have:03d}"
                card.update({"id": card_id, "family": family_id, "topic": topic,
                             "source": {"teacher": config["model"], "families_version": spec["version"],
                                        "prompt": hashlib.sha256(CARD_PROMPT.encode()).hexdigest()[:12],
                                        "today": today}})
                kept.append(card)
                with out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(card, ensure_ascii=False) + "\n")
                print(f"{card_id} {topic}: kept", flush=True)
    finally:
        server.close()


def tasks(args: argparse.Namespace) -> None:
    """Each card as a bench task directory (task.json, web/pages.json, workspace/)."""
    spec = families()
    cards = [json.loads(line) for line in (OUT / f"{args.split}-cards.jsonl").read_text(encoding="utf-8").splitlines()]
    root = OUT / f"{args.split}-tasks"
    if root.exists():
        shutil.rmtree(root)
    for card in cards:
        family = spec["families"][card["family"]]
        task_dir = root / card["id"]
        (task_dir / "workspace").mkdir(parents=True)
        for path, text in (card.get("files") or {}).items():
            target = task_dir / "workspace" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        pages = []
        for page in card.get("pages") or []:
            url = urllib.parse.urlsplit(page["url"])
            # The stand-in web serves every page on its own host name at WEB_PORT, over http.
            pages.append({**page, "url": urllib.parse.urlunsplit(
                ("http", f"{url.hostname}:{WEB_PORT}", url.path or "/", url.query, ""))})
        if pages:
            (task_dir / "web").mkdir()
            (task_dir / "web" / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
        scenario = {key: card[key] for key in ("persona", "topic", "opening", "goal", "plan", "max_turns")}
        task = {"kind": "scenario", "family": card["family"], "scope": "change", "web": bool(pages),
                "scenario": scenario, "must": card["must"], "must_not": card["must_not"],
                "checks": family["checks"], "check": ["true"], "timeout_s": TIMEOUT_S}
        (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(cards)} tasks -> {root}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--config", required=True, help="the teacher's bench config (configs.json)")
    g.add_argument("--split", choices=SPLITS, required=True)
    g.add_argument("--per-family", type=int, default=40)
    g.add_argument("--families", nargs="*", help="family ids (default all)")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--port", type=int, default=18389)
    t = sub.add_parser("tasks")
    t.add_argument("--split", choices=SPLITS, required=True)
    args = parser.parse_args()
    {"generate": generate, "tasks": tasks}[args.command](args)


if __name__ == "__main__":
    main()
