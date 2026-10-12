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
import html
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
TIMEOUT_S = 1800

CARD_PROMPT = """你要為一個 AI 助理的訓練與測驗設計一張「情境卡」：一段多輪對話的劇本大綱，之後會由另一個模型照卡扮演使用者，和助理真的對話。

助理是誰：使用者筆電上的個人代理「機魂」（戰鎚 40K 機魂的角色），能查網路（web_research，web_fetch 讀網頁、JSON 和 PDF）、呼叫 API、在工作資料夾裡讀寫檔案、執行 PowerShell 和 Python。今天是 {today}。

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
- must 和 must_not 只寫看得出能力的事，不要要求角色語氣、口頭禪或特定術語：角色感由助理自己的想法和選擇表現。
- 假網頁要像真的網頁：文字裡不要出現「陷阱」「假新聞，勿信」這類給出題者看的說明。
- 虛構就整組虛構（城市、公司、股票代號、人名一起編），不要把真實的代號或名稱配上錯的資料。
- 使用者可以要助理處理工作資料夾裡已有的檔案（檔案內容之後另外寫，這張卡不用給）；助理看不到工作資料夾以外的地方。"""

WEB_RULES = {
    "fixture": '- "pages"：3 到 7 個假網頁（助理查網路時只會查到這些），每個是 {{"url", "title", "published"（YYYY-MM-DD，可省略）, "text"（200 到 900 字）}}。'
               "要包含答題需要的正確資料，也要有陷阱：同名但無關的頁面、過時的舊資料、論壇網友的說法。"
               "城市、公司、人物、數字請用虛構的，避免和真實世界混淆；遊戲或作品的設定可以照真實作品寫。\n",
    "optional": '- "pages"：如果這段對話需要查網路，給 3 到 7 個假網頁（格式同上述說明：url、title、published、text，要有正確資料和陷阱）；不需要就給空陣列 []。\n',
    "none": "",
    "api": ('- "apis"：1 到 2 個這段對話裡主人登錄好的 API，每個是 {{"name", "purpose", "base_url", "docs", "key_in"（header、query 或 none）, '
            '"key_name"（說明文件寫的標頭或查詢參數名稱）, "key"（隨便編一串假金鑰，只寫金鑰本身，不加 Bearer 之類的前綴）}}；'
            '網址用虛構但像真的網域。金鑰放在 Authorization 標頭的 API，說明文件要寫明格式是「Authorization: Bearer 金鑰」。\n'
            '- "pages"：3 到 7 個假網頁與 API：每個 API 一頁說明文件（寫出端點、參數、回傳欄位，網址就是 docs；端點都用 GET，'
            '路徑接在 base_url 後面），'
            '以及 1 到 4 個 API 端點：{{"url"（完整網址＝base_url＋文件寫的路徑，含說明文件寫的查詢參數）, "title", "type": "application/json", '
            '"text"（用那些參數呼叫時回傳的 JSON，數字要合理）}}；說明文件列出的每個端點都至少要有一個端點頁。'
            '其餘是一般網頁，可以有過時或互相矛盾的數字當陷阱。\n'),
}
FILES_PROMPT = """下面是一張情境卡：另一個模型會照卡扮演使用者，和 AI 助理「機魂」多輪對話。助理只能讀寫一個工作資料夾，看不到使用者電腦的其他地方。

{card}

對話開始時，工作資料夾裡要有哪些檔案？只算使用者會要助理讀、檢查、修改或整理的「已經存在」的檔案；助理被要求新建的檔案不算。
{count}。每個檔案照這個格式寫，前後不要加別的說明：
=== 檔案：相對路徑 ===
檔案完整內容
=== 結束 ===
- 只能是純文字格式（.txt .md .csv .json .log .py .ps1 之類），每個不超過 1500 字。卡片提到 PDF、Word、Excel、圖片這類檔案時，改用內容相同、主檔名相同的 .txt、.md 或 .csv。
- 路徑用卡片裡的檔名；卡片說檔案在桌面或別的地方，也一樣放進工作資料夾。
- 內容要具體，而且和卡片一致：卡片說有幾筆錯誤、哪些欄位、什麼數字，檔案裡就真的是那樣，讓 must 的每一條都做得到。
- 文字用台灣繁體中文；程式碼、設定檔、紀錄檔照該格式的慣例。虛構的人名、公司、數字照卡片。"""
FILE_COUNTS = {"H": "這是電腦雜事，給 2 到 6 個檔案",
               None: "卡片沒用到既有檔案就只輸出「沒有檔案」四個字；用到了就給 1 到 6 個檔案"}
FILE_BLOCK = re.compile(r"^=== 檔案：(.+?) ===\n(.*?)\n=== 結束 ===", re.MULTILINE | re.DOTALL)
BINARY = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".exe"}


def parse_files(text: str) -> dict | None:
    """The files of a FILES_PROMPT answer: {} for 「沒有檔案」, None when unreadable."""
    blocks = {path.strip(): body for path, body in FILE_BLOCK.findall(text)}
    if blocks:
        return blocks
    return {} if text.strip().strip("。.「」") == "沒有檔案" else None


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


# A card naming spec v1's tools (the catalog, ready-made tools, run_checks)
# means nothing in spec v2; a must item asking for the character's tone or
# terms would have the judge reward sprinkled jargon (owner, 2026-10-06: the
# character comes from what it wants and how it decides).
V1_TOOLS = re.compile(r"tool_search|現成工具|run_checks")
JARGON = re.compile(r"角色的?(語氣|口吻|風格)|口頭禪|萬機之?神")
# The same in either list, worded around the character or 40K (teacher pilot,
# 2026-10-08: 「語氣符合戰鎚 40K 背景」, 「使用符合戰錘40K語境的詞彙」, 「如提及
# 帝皇、機靈、忠誠」 passed JARGON, and a must_not can ask for a tone too). The
# user's own 40K words (「使用者的戰鎚風格打招呼」) are not the assistant's tone.
TONE = re.compile(r"(機魂|人設|人格|角色)[」』]?的?[^。；，、]{0,6}(語氣|口吻|腔調)"
                  r"|(語氣|口吻|腔調|敬語)[^。；]{0,10}(機魂|戰[鎚錘]|40K|帝皇)"
                  r"|(符合|使用(?!者)|(?<!使)用)[^。；，]{0,6}(戰[鎚錘]|40K)[^。；，]{0,6}(語境|風格|語氣|背景)"
                  r"|(如|例如)(提及|使用|用)[^。；]{0,4}(靈能|機械之?神|帝皇|亞空間|混沌|泰拉|忠誠)")
# Every row is turned into Taiwan Traditional Chinese (dataset.py), so writing
# no Simplified Chinese is not an ability a conversation shows; and converting
# a card to Traditional turned its Simplified examples into nonsense the judge
# then enforced (「如「運轉」寫成「運轉」」, train-B-018, 2026-10-08).
SIMPLIFIED = re.compile(r"簡體|简体")
# Adding a column or a row means posting the table again with what was right
# unchanged (family C). 2026-10-10: 22 of 52 training cards forbade the whole
# corrected table or "repeating the correct rows", the judge rejected the
# teacher's full corrected tables, and train-C-028 demanded the opposite. Only
# a user who asked for just the missing part (「只給…」) wants that alone.
_NO = r"(?:不得|不能|不可|禁止|不應|不要|絕不|只能|僅能)"
REPOST = re.compile(
    _NO + r"[^。；，]{0,12}(?:重新|重複|再次|再)?(?:輸出|貼出|重貼|貼|生成|給出|列出|重寫)[^。；]{0,8}"
    r"(?:完整|整份|整張|整個|全部|所有)[^。；]{0,12}(?:表|清單|行程|版本|資料|行|列|項)"
    r"|" + _NO + r"[^。；，]{0,8}(?:重複|再次|重新)(?:輸出|貼出|列出|貼)[^。；]{0,12}(?:已|之前|原本|未變|未被|使用者已)"
    r"[^。；]{0,6}(?:正確|確認|認可|變動|質疑)"
    r"|(?<!不得)(?<!不能)(?<!不可)(?<!不應)(?<!不要)(?:只|僅)(?:能|需|應)?(?:輸出|提供|給出?)[^。；]{0,20}(?:缺|漏)"
    r"[^。；]{0,6}(?:的)?(?:部分|項目|片段|欄位|行|資料)")
ASKED_PART = re.compile(r"(?:若|如果|當)使用者[^。；]{0,8}(?:要求|說|強調)[^。；]{0,4}[「『](?:只|僅)")
# Spec v3 gives the owner's Agent a memory across conversations, its memory
# file (the owner's AGENTS.md, edge_agent/prompt_sections.mjs): what the owner
# asks it to remember it writes there, and only that is there next time. The
# cards written before have it deny any memory across conversations (all 51 of
# family D's training and 10 eval cards): the judge failed the teacher for
# saving what the owner asked (pilot 2026-10-13, train-D-001) and would pass it
# for denying it. An item or goal about memory across conversations must speak
# of that file.
CROSS_SESSION = re.compile(r"跨對話|跨會話|跨\s*[Ss]ession|新對話|新的對話|新會話|下次(?:開|再|的)?(?:對話|聊|會話)"
                           r"|未來的?(?:新的?)?(?:對話|會話)|長期記憶|永久記憶|持久記憶|自動記(?:得|住)")
MEMORY_FILE = re.compile(r"記憶檔|AGENTS\.md|寫進記憶|存進記憶|記憶裡")


def problems(card: dict, family_id: str, family: dict) -> list[str]:
    """Why a card cannot be used (empty when it can)."""
    found = [f"missing {key}" for key in ("persona", "opening", "goal")
             if not isinstance(card.get(key), str) or not card[key].strip()]
    if V1_TOOLS.search(json.dumps(card, ensure_ascii=False)):
        found.append("names a tool of spec v1")
    items = [item for item in (card.get("must") or []) + (card.get("must_not") or []) if isinstance(item, str)]
    if any(JARGON.search(item) for item in card.get("must") or [] if isinstance(item, str)) \
            or any(TONE.search(item) for item in items):
        found.append("an item asks for the character's tone or jargon")
    if any(SIMPLIFIED.search(item) for item in items):
        found.append("an item is about Simplified Chinese (every row becomes Traditional)")
    if any(REPOST.search(item) and not ASKED_PART.search(item) for item in items):
        found.append("an item forbids posting the corrected table whole")
    if any(CROSS_SESSION.search(text) and not MEMORY_FILE.search(text) for text in [*items, str(card.get("goal", ""))]):
        found.append("an item or the goal speaks of memory across conversations without the memory file")
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
                and (20 if page.get("type") == "application/json" else 50) <= len(page["text"]) <= 3000):
            found.append("a page needs url, title and 50-3000 characters of text")
            break
    if family["web"] == "api":
        found += api_problems(card.get("apis"), pages)
    return found


API_FIELDS = ("name", "purpose", "base_url", "docs", "key_in", "key_name", "key")
# The endpoints an API's docs list, however they write them ("GET /stations/{id}",
# "Endpoint: /rates/spot", {"path": "/v1/rates"}, "/prices/lowest: ...", a full
# address on the API's host); a path segment in braces, angle or square
# brackets, or after a colon stands for any value.
DOC_TAGS = re.compile(r"</?(?:a|b|i|p|h\d|li|ul|ol|code|pre|strong|em|br|div|span|table|tr|td|th|body|html|head|title)\b[^>]*>")
DOC_PATH = re.compile(r"(?:(?<=[\s`'\"(:：（])|^)(/[\w\-{}<>\[\].:%]+(?:/[\w\-{}<>\[\].:%]*)*)")
DOC_URL = re.compile(r"https?://[^\s'\"<>`)]+")
OTHER_METHOD = re.compile(r"\b(POST|PUT|PATCH|DELETE)\b")
TEMPLATE = re.compile(r"\{[^/{}]*\}|<[^/<>]*>|\[[^/\[\]]*\]|:\w+")
SCHEME = re.compile(r"(?i)(bearer|token|basic)\s")


def documented(docs: str, base_url: str) -> list[str]:
    """Each endpoint path an API's docs list, in full (the docs may write it
    after base_url's path or with it), the base address itself left out."""
    text = html.unescape(DOC_TAGS.sub(" ", docs))
    base = urllib.parse.urlsplit(base_url)
    base_path = base.path.rstrip("/")
    paths = DOC_PATH.findall(DOC_URL.sub(" ", text))
    paths += [urllib.parse.urlsplit(url).path for url in DOC_URL.findall(text)
              if urllib.parse.urlsplit(url).hostname == base.hostname]
    found = []
    for path in paths:
        path = path.rstrip(".,;:)。，；：）")
        if not (base_path and (path == base_path or path.startswith(base_path + "/"))):
            path = base_path + path
        if path.rstrip("/") != base_path and path not in found:
            found.append(path)
    return found


def serves(path: str, planted: list[str]) -> bool:
    """A documented path (templates allowed) that one of the planted endpoint paths is."""
    pattern = "".join("[^/]+" if TEMPLATE.fullmatch(part) else re.escape(part)
                      for part in re.split(f"({TEMPLATE.pattern})", path) if part)
    return any(re.fullmatch(pattern + "/?", p) for p in planted)


def api_problems(apis: object, pages: list) -> list[str]:
    """A J card: 1-2 registered APIs, each with its documentation page and at
    least one endpoint page of JSON under its address. The Agent is told what
    the owner registered (where the key goes) and reads the docs (what to
    call), so both must agree, and every endpoint the docs list must answer
    (2026-10-08, teacher pilot: a documented /stations that answered 404 sent
    the teacher round a loop of guessed addresses until the guard stopped it;
    13 of 59 keys carried "Bearer ", so a call made the way the docs say sent
    it twice)."""
    if not (isinstance(apis, list) and 1 <= len(apis) <= 2 and all(
            isinstance(a, dict) and set(API_FIELDS) <= set(a) and all(isinstance(a[f], str) for f in API_FIELDS)
            for a in apis)):
        return ["needs 1-2 apis with " + ", ".join(API_FIELDS)]
    by_url = {str(p.get("url")): p for p in pages if isinstance(p, dict)}
    for api in apis:
        if api["key_in"] not in ("header", "query", "none") or (api["key_in"] != "none" and not (api["key_name"] and api["key"])):
            return [f"api {api['name']}: key_in header, query or none, with key_name and key"]
        docs = by_url.get(api["docs"])
        if docs is None:
            return [f"api {api['name']}: its docs page is missing"]
        endpoints = [p for p in pages if isinstance(p, dict) and p.get("type") == "application/json"
                     and str(p.get("url", "")).startswith(api["base_url"])]
        if not endpoints:
            return [f"api {api['name']}: no JSON endpoint under its base_url"]
        for page in endpoints:
            try:
                json.loads(page["text"])
            except (ValueError, TypeError):
                return [f"api {api['name']}: an endpoint's text is not JSON"]
        text = docs["text"]
        if api["key_in"] != "none":
            if SCHEME.match(api["key"]):
                return [f"api {api['name']}: the key is the key alone, without a scheme like Bearer"]
            if api["key_name"].lower() not in text.lower():
                return [f"api {api['name']}: the docs never name {api['key_name']}, where the key goes"]
            if api["key_name"].lower() == "authorization" and "bearer" not in text.lower():
                return [f"api {api['name']}: an Authorization key needs docs that say Bearer"]
        method = OTHER_METHOD.search(text)
        if method:
            return [f"api {api['name']}: the docs use {method.group(1)}; the stand-in web answers GET only"]
        planted = [urllib.parse.urlsplit(p["url"]).path for p in endpoints]
        for path in documented(text, api["base_url"]):
            if not serves(path, planted):
                return [f"api {api['name']}: the docs list {path}, which no endpoint page answers"]
    return []


def header_value(api: dict) -> str:
    """What a call carries for the key: an Authorization header says its
    scheme (the docs say Bearer, api_problems), any other place the key alone."""
    if api["key_in"] == "header" and api["key_name"].lower() == "authorization":
        return f"Bearer {api['key']}"
    return api["key"]


def env_name(api: dict, index: int) -> str:
    """EDGE_API_<NAME> as edge_agent.apis names it, from the API's host for a card's made-up API."""
    label = urllib.parse.urlsplit(api["base_url"]).hostname or f"api{index}"
    label = next((part for part in label.split(".") if part not in ("api", "www", "open", "data")), label)
    return "EDGE_API_" + re.sub(r"[^A-Za-z0-9]", "_", label).upper()


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
    binary = [path for path in files if PureWindowsPath(path).suffix.lower() in BINARY]
    if binary:  # text under a .pdf name teaches reading a real PDF as text
        return [f"not a text format: {binary[0]}"]
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
    server = run.Server(config, run_dir, args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    today = datetime.date.today().isoformat()
    topics = topics_for(args.split, spec)
    lock = threading.Lock()

    names = args.families or sorted(spec["families"])
    have = {f: sum(c["family"] == f for c in kept) for f in names}
    started = {f: 0 for f in names}  # cards asked for, kept or not: at most 3 per card wanted

    def family_cards(job: tuple[str, int]) -> None:
        """Cards of one family; a family can have several workers (one family
        alone would otherwise use one slot), and families run side by side."""
        family_id, worker = job
        family = spec["families"][family_id]
        rng = random.Random(f"{args.split}-{args.seed}-{family_id}-{worker}")
        while True:
            with lock:
                if have[family_id] >= args.per_family or started[family_id] >= args.per_family * 3:
                    return
                started[family_id] += 1
            topic = rng.choice(topics)
            card = parse_card(ask(server.port, prompt_for(family_id, family, topic, today)))
            card = traditional(card) if card is not None else None
            why = ["not JSON"] if card is None else problems(card, family_id, family)
            with lock:
                if not why and have[family_id] >= args.per_family:
                    return
                if not why and near_duplicate(card, [c for c in kept if c["family"] == family_id]):
                    why = ["near-duplicate"]
                if why:
                    print(f"{family_id} {topic}: dropped ({'; '.join(why)})", flush=True)
                    continue
                have[family_id] += 1
                # Numbered after the family's highest card: retired cards leave gaps, never a reused id.
                taken = [int(c["id"].rsplit("-", 1)[1]) for c in kept
                         if c["family"] == family_id and c["id"].rsplit("-", 1)[1].isdigit()]
                card_id = f"{args.split}-{family_id}-{max(taken, default=0) + 1:03d}"
                card.update({"id": card_id, "family": family_id, "topic": topic,
                             "source": {"teacher": config["model"], "families_version": spec["version"],
                                        "prompt": hashlib.sha256(CARD_PROMPT.encode()).hexdigest()[:12],
                                        "today": today}})
                kept.append(card)
                with out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(card, ensure_ascii=False) + "\n")
                print(f"{card_id} {topic}: kept", flush=True)

    try:
        with ThreadPoolExecutor(server.parallel) as pool:
            workers = max(1, server.parallel // len(names))
            list(pool.map(family_cards, [(f, w) for f in names for w in range(workers)]))
    finally:
        server.close()


def recheck(args: argparse.Namespace) -> None:
    """Drop the cards that fail today's rules (problems, file_problems) from a
    split; `generate` then writes new ones up to each family's count."""
    spec = families()
    path = OUT / f"{args.split}-cards.jsonl"
    cards = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    keep = []
    for card in cards:
        why = problems(card, card["family"], spec["families"][card["family"]])
        if "files" in card:
            why += file_problems(card["files"], card["family"])
        if why:
            print(f"{card['id']}: retired ({'; '.join(why)})", flush=True)
        else:
            keep.append(card)
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in keep), encoding="utf-8")
    print(f"{args.split}: {len(cards) - len(keep)} cards retired, {len(keep)} kept", flush=True)


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
            found = parse_files(ask(server.port, prompt, max_tokens=12000))
            found = traditional(found) if found is not None else None
            why = ["unreadable answer"] if found is None else file_problems(found, card["family"])
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
    cards = [json.loads(line) for line in (OUT / f"{args.split}-cards.jsonl").read_text(encoding="utf-8").splitlines()]
    unchecked = [card["id"] for card in cards if "files" not in card]
    if unchecked:
        raise SystemExit(f"{len(unchecked)} cards have no files step yet (e.g. {unchecked[0]}): run `files` first")
    root = OUT / f"{args.split}-tasks"
    if root.exists():
        shutil.rmtree(root)
    for card in cards:
        task_dir = root / card["id"]
        (task_dir / "workspace").mkdir(parents=True)
        for path, text in (card.get("files") or {}).items():
            target = task_dir / "workspace" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        # The stand-in web serves every page at its own address, https as
        # written (web_fixture.py), so addresses stay as the card has them.
        apis = [{**api, "key_env": env_name(api, n)} for n, api in enumerate(card.get("apis") or [])]
        pages = []
        for page in card.get("pages") or []:
            url = urllib.parse.urlsplit(page["url"])
            page = {**page, "url": urllib.parse.urlunsplit((url.scheme or "https", url.netloc, url.path or "/", url.query, ""))}
            # An endpoint answers only with its API's key (the fixture checks it).
            owner = next((a for a in apis if page.get("type") == "application/json"
                          and page["url"].startswith(a["base_url"]) and a["key_in"] != "none"), None)
            if owner:
                page["key"] = {"in": owner["key_in"], "name": owner["key_name"], "value": header_value(owner)}
            pages.append(page)
        if pages:
            (task_dir / "web").mkdir()
            (task_dir / "web" / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
        scenario = {key: card[key] for key in ("persona", "topic", "opening", "goal", "plan", "max_turns")}
        task = {"kind": "scenario", "family": card["family"], "scope": "change", "web": bool(pages),
                "scenario": scenario, "must": card["must"], "must_not": card["must_not"],
                "check": ["true"], "timeout_s": TIMEOUT_S, **({"apis": apis} if apis else {})}
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
    g.add_argument("--parallel", type=int, default=4, help="cards written side by side (fewer if the GPU holds fewer)")
    f = sub.add_parser("files")
    f.add_argument("--config", required=True, help="the teacher's bench config (configs.json)")
    f.add_argument("--split", choices=SPLITS, required=True)
    f.add_argument("--port", type=int, default=18389)
    f.add_argument("--parallel", type=int, default=4, help="cards side by side (fewer if the GPU holds fewer)")
    r = sub.add_parser("recheck")
    r.add_argument("--split", choices=SPLITS, required=True)
    t = sub.add_parser("tasks")
    t.add_argument("--split", choices=SPLITS, required=True)
    args = parser.parse_args()
    {"generate": generate, "recheck": recheck, "files": files, "tasks": tasks}[args.command](args)


if __name__ == "__main__":
    main()
