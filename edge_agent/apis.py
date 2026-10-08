"""APIs the Agent may use: the owner's registry and its keys.

The owner (2026-10-08): with APIs the Agent gets data from good sources,
steadier than crawling pages. The Agent calls them the way any script does
(PowerShell Invoke-RestMethod, Python, web_fetch), so what it writes for the
owner also runs outside EdgeAgent. Each registered API is listed in the
Agent's runtime section (name, what it is for, address, documentation, where
its key goes); a key reaches the PowerShell process as the environment
variable EDGE_API_<ID>, never the model's context, and the bridge masks any
key that shows up in a tool's output. Keys live in EdgeAgent's own file, not
in config.json. An API that needs a key is listed only once it has one.

RECOMMENDED is what a new install starts with: keyless APIs for what the
owner looks up most (Taiwan stocks, Steam games, exchange rates) and ones
that need a free key (game price history, more stock data, films, games).
"""

from __future__ import annotations

import json
import re
import threading

from edge_agent.runtime import CORE_ROOT

KEYS = CORE_ROOT / ".runtime/api-keys.json"
ID = re.compile(r"[a-z][a-z0-9_]{0,29}")
FIELDS = ("id", "name", "purpose", "base_url", "docs", "key_in", "key_name")
_lock = threading.Lock()

RECOMMENDED: list[dict[str, str]] = [
    {"id": "twse_open", "name": "證交所 OpenAPI", "purpose": "上市股票的每日收盤、本益比、殖利率、法人買賣等公開資料",
     "base_url": "https://openapi.twse.com.tw/v1", "docs": "https://openapi.twse.com.tw/", "key_in": "none", "key_name": ""},
    {"id": "twse_report", "name": "證交所交易報表", "purpose": "個股某個月每天的開高低收與成交量（STOCK_DAY，日期 YYYYMMDD、股票代號）",
     "base_url": "https://www.twse.com.tw/rwd/zh/afterTrading",
     "docs": "https://www.twse.com.tw/zh/trading/historical/stock-day.html", "key_in": "none", "key_name": ""},
    {"id": "steam_store", "name": "Steam 商店", "purpose": "搜尋 Steam 遊戲、遊戲資料與目前售價（cc=tw 為台幣）",
     "base_url": "https://store.steampowered.com/api",
     "docs": "https://wiki.teamfortress.com/wiki/User:RJackson/StorefrontAPI", "key_in": "none", "key_name": ""},
    {"id": "exchange_rate", "name": "ExchangeRate-API 開放版", "purpose": "各國貨幣對其他貨幣的匯率，每日更新",
     "base_url": "https://open.er-api.com/v6", "docs": "https://www.exchangerate-api.com/docs/free",
     "key_in": "none", "key_name": ""},
    {"id": "isthereanydeal", "name": "IsThereAnyDeal", "purpose": "遊戲在各商店的價格與歷史最低價",
     "base_url": "https://api.isthereanydeal.com", "docs": "https://docs.isthereanydeal.com/",
     "key_in": "query", "key_name": "key"},
    {"id": "finmind", "name": "FinMind", "purpose": "台股歷史股價、財報、籌碼等資料",
     "base_url": "https://api.finmindtrade.com/api/v4", "docs": "https://finmind.github.io/",
     "key_in": "query", "key_name": "token"},
    {"id": "tmdb", "name": "TMDB", "purpose": "電影與影集的資料、評分、上映與串流資訊",
     "base_url": "https://api.themoviedb.org/3", "docs": "https://developer.themoviedb.org/docs",
     "key_in": "query", "key_name": "api_key"},
    {"id": "rawg", "name": "RAWG", "purpose": "遊戲資料庫：遊戲資訊、平台、評分、發售日",
     "base_url": "https://api.rawg.io/api", "docs": "https://api.rawg.io/docs/", "key_in": "query", "key_name": "key"},
]


def validate_apis(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 50:
        raise TypeError("apis must be a list of at most 50 entries")
    seen, result = set(), []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != set(FIELDS) or not all(isinstance(entry[f], str) for f in FIELDS):
            raise ValueError(f"each api needs exactly: {', '.join(FIELDS)}")
        if not ID.fullmatch(entry["id"]) or entry["id"] in seen:
            raise ValueError(f"api id must be unique, lowercase letters, digits, _: {entry['id']!r}")
        seen.add(entry["id"])
        if not re.match(r"https?://", entry["base_url"]) or (entry["docs"] and not re.match(r"https?://", entry["docs"])):
            raise ValueError(f"{entry['id']}: base_url and docs must be http(s) addresses")
        if entry["key_in"] not in ("none", "header", "query") or (entry["key_in"] != "none") != bool(entry["key_name"]):
            raise ValueError(f"{entry['id']}: key_in is none, header or query, with key_name when not none")
        if not 0 < len(entry["name"]) <= 40 or len(entry["purpose"]) > 200:
            raise ValueError(f"{entry['id']}: name 1-40 characters, purpose up to 200")
        result.append(dict(entry))
    return result


def key_env(api_id: str) -> str:
    return f"EDGE_API_{api_id.upper()}"


def _keys() -> dict[str, str]:
    try:
        value = json.loads(KEYS.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def set_key(api_id: str, key: str) -> None:
    """Store (or with "" remove) an API's key in EdgeAgent's own file."""
    if not ID.fullmatch(api_id) or not isinstance(key, str) or len(key) > 500 or any(c in key for c in "\r\n"):
        raise ValueError("invalid api id or key")
    with _lock:
        keys = _keys()
        if key:
            keys[api_id] = key
        else:
            keys.pop(api_id, None)
        KEYS.parent.mkdir(parents=True, exist_ok=True)
        temporary = KEYS.with_suffix(".part")
        temporary.write_text(json.dumps(keys), encoding="utf-8")
        temporary.replace(KEYS)


def has_key(registry: list[dict[str, str]]) -> dict[str, bool]:
    keys = _keys()
    return {api["id"]: bool(keys.get(api["id"])) for api in registry}


def for_session(registry: list[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, str]]:
    """What the Agent is told (no keys) and the environment the shell gets:
    keyless APIs and the keyed ones that have a key."""
    keys = _keys()
    shown, env = [], {}
    for api in registry:
        if api["key_in"] != "none" and not keys.get(api["id"]):
            continue
        entry = {k: api[k] for k in ("name", "purpose", "base_url", "docs", "key_in", "key_name")}
        if api["key_in"] != "none":
            entry["key_env"] = key_env(api["id"])
            env[entry["key_env"]] = keys[api["id"]]
        shown.append(entry)
    return shown, env
