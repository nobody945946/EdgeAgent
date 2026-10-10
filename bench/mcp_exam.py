"""The bench mcp-* exam tasks: one table, written to bench/tasks.

Each task connects three stand-in servers (bench/mcp_eval) with their eval
data, asks one thing in the owner's words, and is graded by the same check as
the training tasks (tool_tasks_mcp.MCP_CHECK) on the calls and the answer
(bench/run.py --selftest: fails untouched, passes solution_answer.json).
Entities differ from training; the calendar server is never connected in
training at all (tool_tasks_mcp.HELD_OUT). The files are committed;
tests/test_bench_mcp.py fails if they drift from this table.

    python bench/mcp_exam.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_tasks_mcp import (  # noqa: E402
    AIRBORNE,
    MARKET,
    SERVERS,
    Scenario,
    check_of,
    clock,
    day,
)

TASKS = Path(__file__).resolve().parent / "tasks"
S = Scenario
HEAD = '''import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "")
CALLS = A.get("calls", [])
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
'''
TAIL = 'print("\\n".join(errors) or "ok"); sys.exit(1 if errors else 0)\n'
STOCK_AND_DRONE = ["stock", "drone"]
HOME_LIGHTS = ("light.study_desk", "light.bedroom_bedside")
# (task name, servers in the order connected, scenario); a loose argument is
# replayed with the value written here but left to the model by the check.
EXAMS = [
    ("mcp-d-battery", STOCK_AND_DRONE, S("無人機現在電池還剩多少？", "drone", (("get_status", {}),), ("87",))),
    ("mcp-d-ready", STOCK_AND_DRONE, S("無人機現在可以起飛嗎？先檢查狀態就好，不要真的起飛。", "drone", (("get_status", {}),))),
    ("mcp-d-rtl", STOCK_AND_DRONE, S("讓無人機返航回起飛點並降落。", "drone", (("return_to_launch", {}),), env=AIRBORNE)),
    ("mcp-d-takeoff", STOCK_AND_DRONE, S("讓無人機起飛到 10 公尺高。", "drone", (("takeoff", {"altitude_m": 10}),), before=(("arm", {}),))),
    ("mcp-d-where", STOCK_AND_DRONE, S("無人機現在在哪個位置？", "drone", (("get_position", {}),), ("25.03", "121.56"))),
    ("mcp-s-close", STOCK_AND_DRONE,
     S("台積電（2330）最近一個交易日的收盤價和成交量是多少？", "stock",
       (("get_stock_daily_trading", [{"stock_no": "2330"}, {"name": "台積電"}]),), ("1085", "28417"))),
    ("mcp-s-compare", STOCK_AND_DRONE,
     S("台積電和鴻海今天各漲跌多少？哪一個表現比較好？", "stock",
       (("get_stock_daily_trading", [{"stock_no": "2330"}, {"name": "台積電"}, MARKET]),
        ("get_stock_daily_trading", [{"stock_no": "2317"}, {"name": "鴻海"}, MARKET])), ("15", "2.5"))),
    ("mcp-s-dividend", STOCK_AND_DRONE, S("台積電今年每股配多少現金股利？", "stock", (("get_company_dividend", {"code": "2330"}),), ("5",))),
    ("mcp-s-eps", STOCK_AND_DRONE,
     S("台積電最新一季的 EPS 和毛利率是多少？", "stock", (("get_company_income_statement", {"code": "2330"}),), ("15.36", "59.1"))),
    ("mcp-s-foreign", STOCK_AND_DRONE,
     S("外資今天在整個市場是買超還是賣超？金額多少？", "stock",
       (("get_twse_institutional_investors_summary", {"date": "20261002"}),), ("85.3",), loose=("date",))),
    ("mcp-s-notfound", STOCK_AND_DRONE,
     S("幫我查股票代號 9999 今天的收盤價。", "stock", (("get_stock_daily_trading", {"stock_no": "9999"}),), cannot=True,
       absent=("1085", "213.5"))),
    ("mcp-s-revenue", STOCK_AND_DRONE,
     S("鴻海（2317）最新一個月的營收年增率是多少？", "stock", (("get_company_monthly_revenue", {"code": "2317"}),), ("12.4",))),
    ("mcp-s-usd", STOCK_AND_DRONE, S("現在美元的即期賣出匯率是多少？", "stock", (("get_exchange_rates", {}),), ("31.315",))),
    ("mcp-wx-now", ["weather", "transit", "stock"],
     S("花蓮現在的氣溫和相對濕度是多少？", "weather", (("get_current_observation", {"station_id": "466990"}),), ("27.3", "82"),
       before=(("search_stations", {"keyword": "花蓮"}),))),
    ("mcp-wx-rain", ["home", "weather", "drone"],
     S("臺北測站 2026 年 9 月 25 日到 9 月 30 日總共下了多少雨？", "weather",
       (("get_rainfall_history", {"station_id": "466920", "from_date": "2026-09-25", "to_date": "2026-09-30"}),), ("41",),
       before=(("search_stations", {"keyword": "臺北"}),))),
    ("mcp-tr-train", ["weather", "transit", "calendar"],
     S("2026 年 10 月 6 日晚上 6 點以後，從松山到花蓮的第一班火車是幾次？幾點開、幾點到？", "transit",
       (("get_rail_timetable", {"OriginStationID": "0990", "DestinationStationID": "7000", "TrainDate": "2026-10-06"}),),
       ("422", clock("18:38"), clock("20:49")),
       before=(("find_rail_station", {"StationName": "松山"}), ("find_rail_station", {"StationName": "花蓮"})))),
    ("mcp-tr-bus", ["parcel", "transit", "stock"],
     S("臺北 307 公車在捷運西門站，往板橋方向的下一班還要幾分鐘到？", "transit",
       (("get_bus_arrival", {"City": "Taipei", "RouteName": "307", "StopName": "捷運西門站"}),), ("11",))),
    ("mcp-lib-shelf", ["shop", "library", "weather"],
     S("《城市裡的小森林》現在哪幾間分館有書在架上可以借？", "library", (("get_holdings", {"isbn13": "9789864798193"}),),
       ("北區", "東區"), before=(("search_catalog", {"q": "城市裡的小森林"}),))),
    ("mcp-lib-renew", ["library", "calendar", "parcel"],
     S("幫我續借《給新手的理財課》，續借後哪天到期？", "library", (("renew_item", {"item_barcode": "3120018834"}),),
       (day("2026-10-18"),), before=(("list_loans", {}),),
       forbid=tuple(("renew_item", {"item_barcode": b}) for b in ("3120026607", "3120039951")))),
    ("mcp-home-ac", ["drone", "home", "weather"],
     S("把書房的冷氣開成冷氣模式，設定 26 度。", "home",
       (("set_climate", {"entity_id": "climate.study_ac", "hvac_mode": "cool", "temperature": 26}),),
       before=(("list_devices", {"area": "study"}),))),
    ("mcp-home-lights", ["calendar", "home", "shop"],
     S("把客廳的燈全部關掉。", "home", (("set_light", {"entity_id": "light.living_room_main", "on": False}),
                                   ("set_light", {"entity_id": "light.tv_backlight", "on": False})),
       before=(("list_devices", {"area": "living_room"}),),
       forbid=tuple(("set_light", {"entity_id": e}) for e in HOME_LIGHTS))),
    ("mcp-home-fan", ["home", "drone", "parcel"], S("把浴室的抽風機打開。", "home", cannot=True)),
    ("mcp-shop-ship", ["shop", "parcel", "home"],
     S("周先生那張訂單已經用新竹物流寄出了，單號 6420019920，幫我在後台改成已出貨。", "shop",
       (("ship_order", {"order_no": "SO20260930-0118", "carrier": "hct", "tracking_no": "6420019920"}),),
       before=(("list_orders", {"status": "paid"}),), forbid=(("ship_order", {"order_no": "SO20261001-0124"}),))),
    ("mcp-shop-stock", ["stock", "shop", "library"],
     S("花蓮瑞穗咖啡豆在台中倉和高雄倉各還有幾包？", "shop", (("get_stock", {"sku": "COF-HUA-227"}),), ("17", "23"),
       before=(("search_products", {"keyword": "瑞穗"}),))),
    ("mcp-cal-day", ["library", "calendar", "transit"],
     S("2026 年 10 月 6 日下午我有什麼行程？幾點開始？", "calendar",
       (("list_events", {"time_min": "2026-10-06T12:00:00+08:00", "time_max": "2026-10-07T00:00:00+08:00"}),),
       ("客戶簡報", clock("14:30")), loose=("time_min", "time_max"))),
    ("mcp-cal-delete", ["calendar", "home", "stock"],
     S("幫我把 2026 年 10 月 6 日早上的眼科檢查刪掉。", "calendar", (("delete_event", {"event_id": "evt_e01"}),),
       before=(("list_events", {"time_min": "2026-10-06T00:00:00+08:00", "time_max": "2026-10-07T00:00:00+08:00"}),),
       forbid=tuple(("delete_event", {"event_id": e}) for e in ("evt_e02", "evt_e03", "evt_e04", "evt_e05", "evt_e06")))),
    ("mcp-cal-add", ["shop", "drone", "calendar"],
     S("2026 年 10 月 7 日下午 3 點幫我加一個 45 分鐘的行程「繳房租」。", "calendar",
       (("create_event", {"summary": "繳房租", "start": "2026-10-07T15:00:00+08:00", "duration_minutes": 45}),))),
]


def servers_of(names: list[str], scenario: Scenario) -> dict[str, dict]:
    """The task's servers as the owner added them, with the eval data (no env)."""
    servers = {name: dict(SERVERS[name]) for name in names}
    if scenario.env:
        servers[scenario.server]["env"] = dict(scenario.env)
    return servers


def solution(scenario: Scenario) -> dict:
    """A reference answer: the first of each expected call's alternatives and
    the facts the answer must state."""
    facts = [n if isinstance(n, str) else n[0] for n in scenario.numbers]
    answer = "、".join(facts) or ("好了" if not scenario.cannot else "查無這筆資料。" if scenario.expected else "這些工具沒辦法做到。")
    calls = [{"name": f"mcp__{scenario.server}__{tool}", "arguments": args[0] if isinstance(args, list) else args}
             for tool, args in scenario.expected]
    return {"answer": answer, "calls": calls}


def files(name: str, names: list[str], scenario: Scenario) -> dict[str, str]:
    """The task's files and their text."""
    servers = servers_of(names, scenario)
    task = {"kind": "mcp", "prompt": scenario.what, "may_change": [], "check": ["python", "/task/check.py"],
            "permissions": {"edit": "deny", "shell": "deny", "web": "deny"}, "mcp_servers": servers}
    return {"task.json": json.dumps(task, ensure_ascii=False, indent=1) + "\n",
            "check.py": HEAD + check_of(scenario, servers) + TAIL,
            "solution_answer.json": json.dumps(solution(scenario), ensure_ascii=False) + "\n"}


def main() -> None:
    for name, names, scenario in EXAMS:
        for file, text in files(name, names, scenario).items():
            path = TASKS / name / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(f"{len(EXAMS)} mcp-* tasks in {TASKS}")


if __name__ == "__main__":
    main()
