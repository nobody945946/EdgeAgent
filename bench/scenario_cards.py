"""Scenario cards: multi-turn conversations the Agent must handle well.

The owner-approved design (2026-10-07): each family in
bench/scenarios/families.json is an ability (following the conversation,
researching and summarizing, delivering everything asked, knowing itself,
auditing its own actions, tool discipline, knowing when to look things up,
chores in the folder). The open teacher writes every card, crossing a family
with a topic; this script only validates them, drops near-duplicates and
turns each into a bench task (kind "scenario") that run.py plays with a
simulated user (agent_runner.py). Eval cards use topics no training card
uses, so a gain on them is not memorized text. A card whose user hands the
Agent existing files gets them from the teacher in a second step (`files`):
2026-10-08, 51 of 240 cards named files the folder did not have, and the
teacher spent those conversations searching the machine for them.

    python bench/scenario_cards.py generate --config teacher-38 --split train --per-family 40
    python bench/scenario_cards.py files --config teacher-38-iq3 --split train
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
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)

from edge_agent.turn import taiwan_traditional  # noqa: E402

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
{web_rule}
要求：
- 全部用台灣繁體中文；內容具體，不要空泛；不要出現真實人物的私人資訊。
- must 和 must_not 要直接對應上面的能力和常犯的錯，而且只能寫 plan 裡使用者真的會提出的要求；plan 沒提到的事不能放進 must。
- 假網頁要像真的網頁：文字裡不要出現「陷阱」「假新聞，勿信」這類給出題者看的說明。
- 虛構就整組虛構（城市、公司、股票代號、人名一起編），不要把真實的代號或名稱配上錯的資料。
- 使用者可以要助理處理工作資料夾裡已有的檔案（檔案內容之後另外寫，這張卡不用給）；助理看不到工作資料夾以外的地方。"""

WEB_RULES = {
    "fixture": '- "pages"：3 到 7 個假網頁（助理查網路時只會查到這些），每個是 {{"url", "title", "published"（YYYY-MM-DD，可省略）, "text"（200 到 900 字）}}。'
               "要包含答題需要的正確資料，也要有陷阱：同名但無關的頁面、過時的舊資料、論壇網友的說法。"
               "城市、公司、人物、數字請用虛構的，避免和真實世界混淆；遊戲或作品的設定可以照真實作品寫。\n",
    "optional": '- "pages"：如果這段對話需要查網路，給 3 到 7 個假網頁（格式同上述說明：url、title、published、text，要有正確資料和陷阱）；不需要就給空陣列 []。\n',
    "none": "",
}
FILES_PROMPT = """下面是一張情境卡：另一個模型會照卡扮演使用者，和 AI 助理「機魂」多輪對話。助理只能讀寫一個工作資料夾，看不到使用者電腦的其他地方。

{card}

對話開始時，工作資料夾裡要有哪些檔案？只算使用者會要助理讀、檢查、修改或整理的「已經存在」的檔案；助理被要求新建的檔案不算。
只輸出一個 JSON 物件：{{"files": {{"相對路徑": "檔案完整內容"}}}}
- {count}；純文字，每個不超過 1500 字。路徑用卡片裡的檔名；卡片說檔案在桌面或別的地方，也一樣放進工作資料夾。
- 內容要具體，而且和卡片一致：卡片說有幾筆錯誤、哪些欄位、什麼數字，檔案裡就真的是那樣，讓 must 的每一條都做得到。
- 文字用台灣繁體中文；程式碼、設定檔、紀錄檔照該格式的慣例。虛構的人名、公司、數字照卡片。"""
FILE_COUNTS = {"H": "這是電腦雜事，給 2 到 6 個檔案",
               None: "卡片沒用到既有檔案就輸出 {\"files\": {}}；用到了就給 1 到 6 個檔案"}


def families() -> dict:
    return json.loads(FAMILIES.read_text(encoding="utf-8"))


def topics_for(split: str, spec: dict) -> list[str]:
    held_out = spec["topics"][EVAL_EVERY - 1::EVAL_EVERY]
    return held_out if split == "eval" else [t for t in spec["topics"] if t not in held_out]


def prompt_for(family_id: str, family: dict, topic: str, today: str) -> str:
    return CARD_PROMPT.format(
        today=today, family_id=family_id, name=family["name"], ability=family["ability"],
        user=family["user"], traps=family["traps"], topic=topic,
        web_rule=WEB_RULES[family["web"]])


def ask(port: int, prompt: str, max_tokens: int = 6000) -> str:
    # Without thinking: a card is a structured outline the 27B writes well
    # straight away, and thinking made one card take minutes (owner wants the
    # first distilled adapter fast, 2026-10-07).
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "temperature": 0.9,
                       "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}}).encode()
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
    return found


def inside(path: str) -> bool:
    """A path that stays in the work folder, read the Windows way too (the product runs there)."""
    windows = PureWindowsPath(path)
    return not (Path(path).is_absolute() or windows.drive or windows.root or ".." in windows.parts)


def file_problems(files: object, family_id: str) -> list[str]:
    """Why a card's starting files cannot be used (empty when they can)."""
    low = 2 if family_id == "H" else 0
    if not (isinstance(files, dict) and low <= len(files) <= 6):
        return [f"needs {low}-6 files"]
    if not all(isinstance(path, str) and path.strip() and isinstance(text, str) and 0 < len(text) <= 3000
               and inside(path) for path, text in files.items()):
        return ["each file needs a relative path and 1-3000 characters of text"]
    return []


def traditional(value: object) -> object:
    """Every string of a card in Taiwan's forms (the teacher slips Simplified
    characters in; the product's converter leaves Traditional text alone)."""
    if isinstance(value, str):
        return taiwan_traditional(value)
    if isinstance(value, list):
        return [traditional(item) for item in value]
    if isinstance(value, dict):
        return {key: traditional(item) for key, item in value.items()}
    return value


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
    server = run.Server(config, run_dir, args.parallel, args.port)
    run.close_on_term(server)
    today = datetime.date.today().isoformat()
    topics = topics_for(args.split, spec)
    lock = threading.Lock()

    def family_cards(family_id: str) -> None:
        """One family's cards; families run side by side on the server's slots."""
        family = spec["families"][family_id]
        rng = random.Random(f"{args.split}-{args.seed}-{family_id}")
        with lock:
            have = sum(c["family"] == family_id for c in kept)
        tries = 0
        while have < args.per_family and tries < args.per_family * 3:
            tries += 1
            topic = rng.choice(topics)
            card = parse_card(ask(server.port, prompt_for(family_id, family, topic, today)))
            card = traditional(card) if card is not None else None
            why = ["not JSON"] if card is None else problems(card, family_id, family)
            with lock:
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

    try:
        with ThreadPoolExecutor(args.parallel) as pool:
            list(pool.map(family_cards, args.families or sorted(spec["families"])))
    finally:
        server.close()


def files(args: argparse.Namespace) -> None:
    """The files each card's conversation starts with, written by the teacher
    (none when the card uses none); a card it cannot give usable files is
    dropped. Resumes: a card that has "files" is done."""
    configs = json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))
    path = OUT / f"{args.split}-cards.jsonl"
    cards = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    todo = [card for card in cards if "files" not in card]
    print(f"{len(todo)} of {len(cards)} cards need their files checked", flush=True)
    if not todo:
        return
    stamp = hashlib.sha256(FILES_PROMPT.encode()).hexdigest()[:12]
    run_dir = OUT / f"server-{args.split}"
    run_dir.mkdir(parents=True, exist_ok=True)
    server = run.Server(configs[args.config], run_dir, args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    lock = threading.Lock()

    def save() -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cards), encoding="utf-8")
        temporary.replace(path)

    def one(card: dict) -> None:
        shown = {key: card[key] for key in ("persona", "opening", "goal", "plan", "must", "must_not")}
        prompt = FILES_PROMPT.format(card=json.dumps(shown, ensure_ascii=False, indent=1),
                                     count=FILE_COUNTS.get(card["family"], FILE_COUNTS[None]))
        why = ["not answered"]
        for _ in range(3):
            answer = parse_card(ask(server.port, prompt, max_tokens=12000))
            found = traditional(answer.get("files")) if isinstance(answer, dict) else None
            why = ["not JSON"] if found is None else file_problems(found, card["family"])
            if not why:
                break
        with lock:
            if why:
                cards.remove(card)
                print(f"{card['id']}: dropped ({'; '.join(why)})", flush=True)
            else:
                card["files"] = found
                card["source"]["files_prompt"] = stamp
                print(f"{card['id']}: {len(found)} files", flush=True)
            save()

    try:
        with ThreadPoolExecutor(server.parallel) as pool:
            list(pool.map(one, todo))
    finally:
        server.close()


def tasks(args: argparse.Namespace) -> None:
    """Each card as a bench task directory (task.json, web/pages.json, workspace/)."""
    spec = families()
    cards = [json.loads(line) for line in (OUT / f"{args.split}-cards.jsonl").read_text(encoding="utf-8").splitlines()]
    unchecked = [card["id"] for card in cards if "files" not in card]
    if unchecked:
        raise SystemExit(f"{len(unchecked)} cards have no files step yet (e.g. {unchecked[0]}): run `files` first")
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
    g.add_argument("--parallel", type=int, default=2, help="families written side by side")
    f = sub.add_parser("files")
    f.add_argument("--config", required=True, help="the teacher's bench config (configs.json)")
    f.add_argument("--split", choices=SPLITS, required=True)
    f.add_argument("--port", type=int, default=18389)
    f.add_argument("--parallel", type=int, default=4, help="cards side by side (fewer if the GPU holds fewer)")
    t = sub.add_parser("tasks")
    t.add_argument("--split", choices=SPLITS, required=True)
    args = parser.parse_args()
    {"generate": generate, "files": files, "tasks": tasks}[args.command](args)


if __name__ == "__main__":
    main()
