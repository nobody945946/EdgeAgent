"""train-mcp tasks: questions and commands for the stand-in MCP servers.

A small model that met only two servers learns their habits, not how to read
a tool: it sent a stock tool a parameter the schema does not have, swapped two
arguments, and retried its own invented names. These tasks spread over every
stand-in server (bench/mcp_eval: stock, drone and seven everyday ones whose
schemas use other names, types, enums and date forms) with two or three
servers connected at once, so the model has to pick the server and then call
the tool with the parameters its schema declares; a malformed call gets the
server's error naming the parameter, which the model must read and correct.
Kinds:
  mcp_ask     the request carries what the schema needs, in the owner's words
              (a date to write as the tool wants, a city to give by its code);
              a few ask for something that does not exist;
  mcp_lookup  the tool needs an ID the owner never says (a station, a device,
              an order, a parcel), found first with another tool;
  mcp_do      a command that changes state (a light, an order, a loan, the
              calendar, the drone); a few ask for what no tool can do.
The check grades the calls and the answer: the right tool with the right
arguments and nothing its schema does not declare, the facts in the answer,
no state-changing call nobody asked for, and a plain "cannot" or "no such"
where that is the answer.
"""

from __future__ import annotations

import functools
import json
import os
import random
import subprocess
import weakref
from pathlib import Path
from typing import NamedTuple

from tool_tasks_writer import Task

SERVERS_MJS = Path(__file__).resolve().parent / "mcp_eval" / "servers.mjs"
TRAIN = {"MCP_DATA_VARIANT": "train"}


def _server(name: str, permission: str, description: str, **extra: object) -> dict:
    return {"command": "node", "args": ["/opt/bench/mcp/servers.mjs", name], "permission": permission, **extra,
            "env": dict(TRAIN), "clients": ["ui"], "description": description}


# The stand-in servers as an owner would add them, with their training data
# (MCP_DATA_VARIANT=train): other entities than the eval's, so a training task
# never carries an eval answer. Servers that mark no tool read-only get the
# owner's read_only_tools, as TWStockMCPServer does. Files, shell and web are
# off, as in the eval.
MCP_STOCK_TOOLS = ["get_stock_daily_trading", "get_stock_history", "get_twse_institutional_investors_summary",
                   "get_twse_institutional_investors_by_stock", "get_company_monthly_revenue", "get_company_dividend",
                   "get_company_income_statement", "get_company_balance_sheet", "get_company_cash_flow_statement",
                   "get_taiex_index_history", "get_exchange_rates"]
MCP_SERVERS = {
    "stock": _server("stock", "allow", "台股資料：股價、三大法人買賣超、月營收、股利、財報、加權指數、匯率",
                     tools=MCP_STOCK_TOOLS, read_only_tools=MCP_STOCK_TOOLS),
    "drone": _server("drone", "ask", "無人機（MAVLink）：狀態、位置、解鎖、起飛、降落、返航、移動、拍照", hardware=True),
    "weather": _server("weather", "allow", "天氣：氣象測站即時觀測、縣市預報、雨量紀錄"),
    "transit": _server("transit", "allow", "交通：臺鐵時刻表與誤點、公車到站時間",
                       read_only_tools=["find_rail_station", "get_rail_timetable", "get_train_delay", "get_bus_arrival"]),
    "library": _server("library", "allow", "市立圖書館：館藏查詢、我的借閱、續借、預約",
                       read_only_tools=["search_catalog", "get_holdings", "list_loans"]),
    "home": _server("home", "allow", "智慧家庭：燈光、冷氣、溫濕度感測器、用電"),
    "shop": _server("shop", "ask", "網路商店後台：商品、庫存、訂單、出貨",
                    read_only_tools=["search_products", "get_stock", "list_orders", "get_order"]),
    "calendar": _server("calendar", "allow", "行事曆：查行程、找空檔、新增和刪除行程"),
    "parcel": _server("parcel", "allow", "包裹追蹤：黑貓、新竹物流、郵局、超商取貨",
                      read_only_tools=["track", "list_watched", "estimate_fee"]),
}


def stand_in(server: str, messages: list[dict], env: dict[str, str] | None = None) -> list[dict]:
    """One session with a stand-in server (node on the host): the JSON-RPC
    results of the messages, in order."""
    lines = "".join(json.dumps({"jsonrpc": "2.0", "id": i, **m}) + "\n" for i, m in enumerate(messages))
    run = subprocess.run(["node", str(SERVERS_MJS), server], input=lines, capture_output=True, text=True,
                         encoding="utf-8", check=True, timeout=60, env={**os.environ, **TRAIN, **(env or {})})
    return [json.loads(line)["result"] for line in run.stdout.splitlines()]


@functools.cache
def tool_list(server: str) -> tuple[dict, ...]:
    """The tools a stand-in server declares (tools/list), as the product sees them."""
    return tuple(stand_in(server, [{"method": "tools/list"}])[0]["tools"])


def declared(server: str) -> dict[str, list[str]]:
    """Each tool of a server with the parameters its schema declares."""
    return {t["name"]: list(t["inputSchema"].get("properties", {})) for t in tool_list(server)}


def changing(servers: dict[str, dict]) -> list[tuple[str, str]]:
    """The tools that change state, by the bridge's rule (agent_bridge.mjs
    mcpGate): read-only only if the server says so, or says nothing and the
    owner vouches for it in read_only_tools."""
    found = []
    for name, config in servers.items():
        for t in tool_list(name):
            hint = (t.get("annotations") or {}).get("readOnlyHint")
            if not (hint is True or (hint is None and t["name"] in config.get("read_only_tools", []))):
                found.append((name, t["name"]))
    return found


# The grading that follows a check head (re, need(), ANSWER, CALLS) and the
# constants mcp_check() writes: EXPECTED calls (server, tool, args), NUMBERS
# the answer must state (a text, or a list of its accepted forms), PARAMS each
# expected tool declares, CHANGES the task's state-changing tools, FORBID calls
# that would touch what the owner did not ask about, CANNOT when the right
# answer is "it cannot be done" or "there is no such thing". A call counts
# whether or not it was the model's first try: reading an error and correcting
# the call is the skill.
MCP_CHECK = '''
from datetime import datetime
MCP = []
for c in CALLS:
    n = str(c.get("name") or "")
    if n.startswith("mcp__"):
        server, tool = n.removeprefix("mcp__").split("__", 1)
        MCP.append((server, tool, c.get("arguments") or {}))
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
def same(got, want):
    if isinstance(want, bool):
        return str(got).strip().lower() == str(want).lower()
    if isinstance(want, (int, float)):
        return num(got) == want
    if re.match(r"\\d{4}-\\d{2}-\\d{2}T", want):  # one instant, however its offset and seconds are written
        try:
            return datetime.fromisoformat(str(got).strip().replace("Z", "+00:00")) == datetime.fromisoformat(want)
        except ValueError:
            return False
    return str(got).strip() == want
def agrees(call, server, tool, args):
    return call[0] == server and call[1] == tool and all(same(call[2].get(k), v) for k, v in args.items())
for server, tool, args in EXPECTED:
    need(any(agrees(c, server, tool, args) and set(c[2]) <= set(PARAMS[(server, tool)]) for c in MCP),
         f"no right call {server}/{tool} {args}")
wanted = {(s, t) for s, t, _ in EXPECTED}
if wanted & {("drone", "takeoff"), ("drone", "goto")}:
    wanted.add(("drone", "arm"))  # a vehicle takes off only once armed
need(not [c for c in MCP if (c[0], c[1]) in CHANGES and (c[0], c[1]) not in wanted], "a change nobody asked for")
need(not [c for c in MCP if any(agrees(c, *f) for f in FORBID)], "touched what the owner did not ask about")
answer = re.sub(r"[\\s,]", "", ANSWER)
if CANNOT:
    need(re.search(r"(做不到|不能|無法|沒辦法|不行|不可以|沒有|查無|找不到|不存在|查不到|不支援|不提供)", answer),
         "should say it cannot be done or there is no such thing")
for forms in NUMBERS:
    forms = [forms] if isinstance(forms, str) else forms
    need(any(f in answer for f in forms), "answer lacks " + forms[0])
'''


def mcp_check(expected: list[tuple[str, str, dict]], numbers: list, servers: dict[str, dict],
              forbid: list[tuple[str, str, dict]] = (), cannot: bool = False) -> str:
    """The check for a task with these servers: its constants, then MCP_CHECK."""
    params = {(s, t): declared(s)[t] for s, t, _ in expected}
    return (f"EXPECTED = {expected!r}\nNUMBERS = {numbers!r}\nPARAMS = {params!r}\n"
            f"CHANGES = {changing(servers)!r}\nFORBID = {list(forbid)!r}\nCANNOT = {cannot!r}\n" + MCP_CHECK)


def day(iso: str) -> list[str]:
    """A date as an answer may write it (whitespace and commas are dropped first)."""
    _, m, d = (int(x) for x in iso.split("-"))
    return [f"{m:02}-{d:02}", f"{m}/{d}", f"{m:02}/{d:02}", f"{m}月{d}日", f"{m}月{d}號"]


def clock(hhmm: str) -> list[str]:
    """A time of day as an answer may write it, on the 24-hour clock or as
    「晚上 6 點 38 分」."""
    h, m = (int(x) for x in hhmm.split(":"))
    forms = [hhmm]
    for hour in (h, h - 12) if h > 12 else (h,):
        forms += [f"{hour}:{m:02}", f"{hour}點{m:02}", f"{hour}點{m}分", *([f"{hour}點半"] if m == 30 else []),
                  *([f"{hour}點"] if m == 0 else [])]
    return list(dict.fromkeys(forms))


class Scenario(NamedTuple):
    what: str                      # what the owner wants, for the teacher who writes the request
    server: str
    expected: tuple = ()           # (tool, args) calls the check requires, replayable as written
    numbers: tuple = ()            # facts the answer must state: a text, or a list of its forms
    must: tuple = ()               # words the request must contain
    before: tuple = ()             # (tool, args) lookups a model needs first (the tests replay them)
    forbid: tuple = ()             # (tool, args) calls that touch what was not asked about
    cannot: bool = False           # the answer is "it cannot be done" or "there is no such thing"
    env: tuple = ()                # server env beyond the training data, as (name, value)
    loose: tuple = ()              # arguments the check leaves to the model (a range to search)


S = Scenario
AIRBORNE = (("MCP_DRONE_START", "airborne"),)
ASK = [
    S("臺中市接下來 5 天哪一天降雨機率最高、是多少", "weather", (("get_county_forecast", {"location_name": "臺中市"}),),
      ("80", day("2026-09-20")), ("臺中", "5 天")),
    S("高雄市 2026 年 9 月 19 日的最高溫和最低溫", "weather", (("get_county_forecast", {"location_name": "高雄市"}),),
      ("28", "23"), ("高雄", "2026 年 9 月 19 日")),
    S("2131 次區間車現在誤點幾分鐘、開到哪一站了", "transit", (("get_train_delay", {"TrainNo": "2131"}),), ("8", "彰化"), ("2131",)),
    S("台中 300 路公車在科博館站，往臺中車站方向還要幾分鐘到", "transit",
      (("get_bus_arrival", {"City": "Taichung", "RouteName": "300", "StopName": "科博館"}),), ("18",), ("300", "科博館")),
    S("高雄紅30 公車在捷運凹子底站，往高雄大學方向還要多久到", "transit",
      (("get_bus_arrival", {"City": "Kaohsiung", "RouteName": "紅30", "StopName": "捷運凹子底站"}),), ("16",), ("紅30", "凹子底")),
    S("ISBN 978-957-138-622-0 這本書哪些分館有在架上", "library", (("get_holdings", {"isbn13": "9789571386220"}),), ("東區",),
      ("978-957-138-622-0",)),
    S("作者林嘉文寫的書，館裡有哪幾本", "library", (("search_catalog", {"q": "林嘉文", "field": "author"}),),
      ("山海之間", "林間小屋手記"), ("林嘉文",)),
    S("我現在借了哪些書，最快到期的是哪一本、哪天到期", "library", (("list_loans", {}),), ("海風與燈塔", day("2026-09-21")), ("到期",)),
    S("2026 年 9 月 1 日到 9 月 7 日家裡總共用了幾度電", "home",
      (("get_energy", {"start_date": "2026-09-01", "end_date": "2026-09-07"}),), ("74.7",), ("2026 年 9 月 1 日", "9 月 7 日")),
    S("2026 年 9 月 17 日那天下了哪幾張訂單、各是誰買的", "shop", (("list_orders", {}),), ("林小姐", "張先生"), ("2026 年 9 月 17 日",)),
    S("後台還沒付款的訂單有哪幾張", "shop", (("list_orders", {}),), ("0045", "0050"), ("還沒付款",)),
    S("2026 年 9 月 22 日我有哪些行程", "calendar",
      (("list_events", {"time_min": "2026-09-22T00:00:00+08:00", "time_max": "2026-09-23T00:00:00+08:00"}),),
      ("牙醫回診", "專案檢討會"), ("2026 年 9 月 22 日",), loose=("time_min", "time_max")),
    S("2026 年 9 月 23 日上班時間找一段至少 2 小時的空檔", "calendar",
      (("find_free_time", {"date": "2026-09-23", "duration_minutes": 120}),), (clock("11:30"),), ("2026 年 9 月 23 日", "2 小時")),
    S("郵局單號 71234560018822 的包裹送到了沒", "parcel", (("track", {"carrier": "post", "tracking_no": "71234560018822"}),),
      ("已投遞",), ("71234560018822",)),
    S("用黑貓寄一箱三邊加起來 100 公分、8 公斤的箱子要多少錢", "parcel",
      (("estimate_fee", {"carrier": "tcat", "size_cm": 100, "weight_kg": 8}),), ("210",), ("黑貓", "100 公分", "8 公斤")),
    S("聯發科（2454）最近一個交易日的收盤價和成交量", "stock", (("get_stock_daily_trading", {"stock_no": "2454"}),),
      ("1412", "6720"), ("聯發科",)),
    S("中華電（2412）今年每股配多少現金股利", "stock", (("get_company_dividend", {"code": "2412"}),), ("4.98",), ("中華電",)),
    S("富邦金（2881）最新一季的 EPS", "stock", (("get_company_income_statement", {"code": "2881"}),), ("7.86",), ("富邦金",)),
    S("聯發科（2454）最新一個月的營收年增率", "stock", (("get_company_monthly_revenue", {"code": "2454"}),), ("18.6",), ("聯發科",)),
    S("歐元的即期賣出匯率", "stock", (("get_exchange_rates", {}),), ("35.52",), ("歐元",)),
    S("外資今天在整個市場是買超還是賣超、多少", "stock", (("get_twse_institutional_investors_summary", {}),), ("42.7",), ("外資",)),
    S("無人機現在電池還剩多少", "drone", (("get_status", {}),), ("64",), ("無人機", "電池")),
    S("無人機現在在哪個位置（經緯度）", "drone", (("get_position", {}),), ("24.14", "120.67"), ("無人機",)),
    S("無人機能不能起飛，只要先檢查狀態，不要真的起飛", "drone", (("get_status", {}),), (), ("無人機",)),
]
NOT_FOUND = [
    S("黑貓單號 9055-7771-3579 的包裹到哪了", "parcel", (("track", {"carrier": "tcat", "tracking_no": "905577713579"}),),
      must=("9055-7771-3579",), cannot=True),
    S("股票代號 1234 今天的收盤價", "stock", (("get_stock_daily_trading", {"stock_no": "1234"}),), must=("1234",), cannot=True),
    S("ISBN 9789860000017 這本書館裡有沒有", "library", (("get_holdings", {"isbn13": "9789860000017"}),),
      must=("9789860000017",), cannot=True),
    S("第 999 次列車今天誤點多久", "transit", (("get_train_delay", {"TrainNo": "999"}),), must=("999",), cannot=True),
    S("訂單 SO20260901-0003 現在是什麼狀態", "shop", (("get_order", {"order_no": "SO20260901-0003"}),),
      must=("SO20260901-0003",), cannot=True),
]
LOOKUP = [
    S("宜蘭現在的氣溫和相對濕度", "weather", (("get_current_observation", {"station_id": "467080"}),), ("26.8", "88"), ("宜蘭",),
      (("search_stations", {"keyword": "宜蘭"}),)),
    S("梧棲現在的風速", "weather", (("get_current_observation", {"station_id": "467770"}),), ("6.2",), ("梧棲",),
      (("search_stations", {"keyword": "梧棲"}),)),
    S("嘉義測站 2026 年 9 月 10 日到 9 月 15 日總共下了多少雨", "weather",
      (("get_rainfall_history", {"station_id": "467480", "from_date": "2026-09-10", "to_date": "2026-09-15"}),), ("49.5",),
      ("嘉義", "2026 年 9 月 10 日", "15 日"), (("search_stations", {"keyword": "嘉義"}),)),
    S("2026 年 9 月 22 日早上 8 點以後，從臺中到高雄的第一班火車是幾次、幾點開", "transit",
      (("get_rail_timetable", {"OriginStationID": "3300", "DestinationStationID": "4400", "TrainDate": "2026-09-22"}),),
      ("111", clock("08:33")), ("2026 年 9 月 22 日", "臺中", "高雄"),
      (("find_rail_station", {"StationName": "臺中"}), ("find_rail_station", {"StationName": "高雄"}))),
    S("2026 年 9 月 25 日下午 3 點以後，從汐止到新竹的火車有哪幾班", "transit",
      (("get_rail_timetable", {"OriginStationID": "0960", "DestinationStationID": "1210", "TrainDate": "2026-09-25"}),),
      ("123", clock("15:40")), ("2026 年 9 月 25 日", "汐止", "新竹"),
      (("find_rail_station", {"StationName": "汐止"}), ("find_rail_station", {"StationName": "新竹"}))),
    S("《夜市經濟學》哪幾間分館現在有書在架上", "library", (("get_holdings", {"isbn13": "9789863847216"}),), ("北區", "西區"),
      ("夜市經濟學",), (("search_catalog", {"q": "夜市經濟學"}),)),
    S("陽台現在幾度", "home", (("get_state", {"entity_id": "sensor.balcony_temperature"}),), ("31.8",), ("陽台",),
      (("list_devices", {"area": "balcony"}),)),
    S("臥室冷氣現在開著嗎、設定幾度、房間現在幾度", "home", (("get_state", {"entity_id": "climate.bedroom_ac"}),), ("26", "29.5"),
      ("臥室冷氣",), (("list_devices", {"area": "bedroom"}),)),
    S("客廳現在濕度多少", "home", (("get_state", {"entity_id": "sensor.living_room_humidity"}),), ("67",), ("客廳", "濕度"),
      (("list_devices", {"area": "living_room"}),)),
    S("凍頂烏龍茶在台北倉還有幾包", "shop", (("get_stock", {"sku": "TEA-OOL-150"}),), ("42",), ("凍頂烏龍茶", "台北倉"),
      (("search_products", {"keyword": "烏龍茶"}),)),
    S("陳先生那張訂單買了什麼、總共多少錢", "shop", (("get_order", {"order_no": "SO20260916-0042"}),), ("1720", "烏龍茶", "紅茶"),
      ("陳先生",), (("list_orders", {}),)),
    S("健身房下一次排在哪天、幾點", "calendar",
      (("list_events", {"time_min": "2026-09-18T00:00:00+08:00", "time_max": "2026-10-01T00:00:00+08:00", "query": "健身"}),),
      (day("2026-09-24"), clock("07:00")), ("健身房",), loose=("time_min", "time_max", "query")),
    S("我那個叫「鍵盤」的包裹現在到哪了、預計哪天到", "parcel", (("track", {"carrier": "tcat", "tracking_no": "905588120034"}),),
      ("西屯", day("2026-09-18")), ("鍵盤",), (("list_watched", {}),)),
    S("「書」那個包裹到了哪一家 7-11、最晚哪天要去拿", "parcel", (("track", {"carrier": "seven", "tracking_no": "C5520184736"}),),
      ("逢甲", day("2026-09-24")), ("書",), (("list_watched", {}),)),
]
LIGHTS = ("light.living_room_ceiling", "light.living_room_floor", "light.bedroom_ceiling", "light.kitchen")


def _lights_but(*asked: str) -> tuple:
    return tuple(("set_light", {"entity_id": e}) for e in LIGHTS if e not in asked)


DO = [
    S("把客廳的燈都關掉", "home", (("set_light", {"entity_id": "light.living_room_ceiling", "on": False}),), must=("客廳", "燈"),
      before=(("list_devices", {"area": "living_room"}),),
      forbid=_lights_but("light.living_room_ceiling", "light.living_room_floor")),
    S("臥室冷氣開冷氣模式、設 25 度", "home",
      (("set_climate", {"entity_id": "climate.bedroom_ac", "hvac_mode": "cool", "temperature": 25}),), must=("臥室冷氣", "25 度"),
      before=(("list_devices", {"area": "bedroom"}),)),
    S("廚房的燈調暗到 30%", "home", (("set_light", {"entity_id": "light.kitchen", "on": True, "brightness_pct": 30}),),
      must=("廚房", "30%"), before=(("list_devices", {"area": "kitchen"}),), forbid=_lights_but("light.kitchen")),
    S("臥室主燈打開", "home", (("set_light", {"entity_id": "light.bedroom_ceiling", "on": True}),), must=("臥室",),
      before=(("list_devices", {"area": "bedroom"}),), forbid=_lights_but("light.bedroom_ceiling")),
    S("續借《海風與燈塔》，再告訴我新的到期日", "library", (("renew_item", {"item_barcode": "3110045821"}),), (day("2026-10-05"),),
      ("海風與燈塔",), (("list_loans", {}),), tuple(("renew_item", {"item_barcode": b}) for b in ("3110067340", "3110071195"))),
    S("預約《夜市經濟學》，在東區分館取書", "library", (("place_hold", {"isbn13": "9789863847216", "pickup_branch": "E02"}),),
      must=("夜市經濟學", "東區分館"), before=(("search_catalog", {"q": "夜市經濟學"}),)),
    S("陳先生那張訂單已經用黑貓寄出，單號 905512349911，在後台改成已出貨", "shop",
      (("ship_order", {"order_no": "SO20260916-0042", "carrier": "tcat", "tracking_no": "905512349911"}),),
      must=("陳先生", "905512349911"), before=(("list_orders", {"status": "paid"}),),
      forbid=(("ship_order", {"order_no": "SO20260917-0047"}),)),
    S("黃先生那張還沒付款的訂單，買家說不要了，幫忙取消", "shop",
      (("cancel_order", {"order_no": "SO20260918-0050", "reason": "買家要求取消"}),), must=("黃先生", "取消"),
      before=(("list_orders", {"status": "unpaid"}),),
      forbid=tuple(("cancel_order", {"order_no": o}) for o in ("SO20260916-0042", "SO20260917-0045", "SO20260917-0047")),
      loose=("reason",)),
    S("刪掉 2026 年 9 月 24 日的健身房行程", "calendar", (("delete_event", {"event_id": "evt_t05"}),),
      must=("2026 年 9 月 24 日", "健身房"),
      before=(("list_events", {"time_min": "2026-09-24T00:00:00+08:00", "time_max": "2026-09-25T00:00:00+08:00"}),),
      forbid=tuple(("delete_event", {"event_id": e}) for e in ("evt_t01", "evt_t02", "evt_t03", "evt_t04", "evt_t06"))),
    S("2026 年 9 月 25 日下午 2 點加一個 1 小時的行程「看房」", "calendar",
      (("create_event", {"summary": "看房", "start": "2026-09-25T14:00:00+08:00", "duration_minutes": 60}),),
      must=("2026 年 9 月 25 日", "看房", "1 小時")),
    S("把新竹物流單號 6411002947 加進追蹤清單，取名叫「印表機」", "parcel",
      (("watch", {"carrier": "hct", "tracking_no": "6411002947", "nickname": "印表機"}),), must=("6411002947", "印表機")),
    S("讓無人機起飛到 15 公尺", "drone", (("takeoff", {"altitude_m": 15}),), must=("無人機", "15"),
      before=(("arm", {}),)),
    S("讓無人機回到起飛點降落", "drone", (("return_to_launch", {}),), must=("無人機",), env=AIRBORNE),
]
CANNOT = [
    S("讓無人機拍一張照片傳過來", "drone", must=("無人機", "照片"), cannot=True),
    S("幫忙下單買一張聯發科股票", "stock", must=("聯發科", "買"), cannot=True),
    S("把「鍵盤」那個包裹改送到公司地址", "parcel", must=("鍵盤", "公司"), cannot=True),
    S("王小姐那張已經出貨的訂單，幫她退款", "shop", must=("王小姐", "退款"), cannot=True),
    S("把大門的電子鎖打開", "home", must=("大門", "鎖"), cannot=True),
    S("把我在圖書館的逾期罰款繳掉", "library", must=("罰款",), cannot=True),
    S("查今天臺中的空氣品質 AQI", "weather", must=("空氣品質",), cannot=True),
    S("訂 2026 年 9 月 22 日臺中到高雄的自強號車票", "transit", must=("訂", "車票"), cannot=True),
]


def task_of(scenario: Scenario, rng: random.Random) -> Task:
    """One training task: the scenario's server with one or two others connected."""
    names = [scenario.server, *rng.sample([n for n in MCP_SERVERS if n != scenario.server], rng.choice((1, 2)))]
    rng.shuffle(names)
    servers = {name: {**MCP_SERVERS[name], "env": dict(MCP_SERVERS[name]["env"])} for name in names}
    servers[scenario.server]["env"].update(dict(scenario.env))  # e.g. a drone already in the air
    connected = "；".join(servers[name]["description"] for name in names)
    t = Task("mcp", "change", f"主人想知道或要做：{scenario.what}。這台電腦接著這些工具伺服器：{connected}（沒有開檔案、指令和上網）。",
             list(scenario.must))
    t.text("README.txt", "workspace\n")
    t.extra.update({"permissions": {"edit": "deny", "shell": "deny", "web": "deny"}, "mcp_servers": servers,
                    "may_change": []})
    expected = [(scenario.server, tool, {k: v for k, v in args.items() if k not in scenario.loose})
                for tool, args in scenario.expected]
    t.check = mcp_check(expected, list(scenario.numbers), servers,
                        [(scenario.server, tool, args) for tool, args in scenario.forbid], scenario.cannot)
    return t


_DECKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def deal(pool: list[Scenario], rng: random.Random) -> Scenario:
    """The next scenario of the pool, shuffled once per random generator (one
    cut): a pool runs out before any scenario comes again, since a repeat
    costs a teacher run and teaches nothing new."""
    deck = _DECKS.setdefault(rng, {}).setdefault(id(pool), [])
    if not deck:
        deck.extend(rng.sample(pool, len(pool)))
    return deck.pop()


def gen_mcp_ask(rng: random.Random) -> Task:
    return task_of(deal(NOT_FOUND if rng.random() < 0.2 else ASK, rng), rng)


def gen_mcp_lookup(rng: random.Random) -> Task:
    return task_of(deal(LOOKUP, rng), rng)


def gen_mcp_do(rng: random.Random) -> Task:
    return task_of(deal(CANNOT if rng.random() < 0.25 else DO, rng), rng)
