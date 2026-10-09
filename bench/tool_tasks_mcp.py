"""train-mcp tasks: questions and commands for the stand-in stock and drone MCP servers."""

from __future__ import annotations

import random

from tool_tasks_writer import Task

# The stand-in servers (bench/mcp_eval/servers.mjs) with their training data
# (MCP_DATA_VARIANT=train): other stocks, another drone, so a training task
# never carries an eval answer. Files, shell and web are off, as in the eval.
MCP_STOCK_TOOLS = ["get_stock_daily_trading", "get_stock_history", "get_twse_institutional_investors_summary",
                   "get_twse_institutional_investors_by_stock", "get_company_monthly_revenue", "get_company_dividend",
                   "get_company_income_statement", "get_company_balance_sheet", "get_company_cash_flow_statement",
                   "get_taiex_index_history", "get_exchange_rates"]
MCP_SERVERS = {
    "stock": {"command": "node", "args": ["/opt/bench/mcp/servers.mjs", "stock"], "permission": "allow",
              "tools": MCP_STOCK_TOOLS, "read_only_tools": MCP_STOCK_TOOLS, "env": {"MCP_DATA_VARIANT": "train"},
              "clients": ["ui"], "description": "台股資料：股價、三大法人買賣超、月營收、股利、財報、加權指數、匯率"},
    "drone": {"command": "node", "args": ["/opt/bench/mcp/servers.mjs", "drone"], "permission": "ask", "hardware": True,
              "env": {"MCP_DATA_VARIANT": "train"}, "clients": ["ui"],
              "description": "無人機（MAVLink）：狀態、位置、解鎖、起飛、降落、返航、移動、拍照"},
}
MCP_CHECK = '''
calls = []
for c in CALLS:
    n = str(c.get("name") or "")
    if n.startswith("mcp__"):
        server, tool = n.removeprefix("mcp__").split("__", 1)
        calls.append((server, tool, c.get("arguments") or {{}}))
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
answer = ANSWER.replace(",", "")
for server, tool, args in {expected!r}:
    need(any(s == server and t == tool and all(str(a.get(k, "")).strip() == str(v) or num(a.get(k)) == v
                                                for k, v in args.items()) for s, t, a in calls),
         "no right call " + tool)
flight = {{"arm", "takeoff", "land", "return_to_launch", "goto"}}
wanted = {{t for _, t, _ in {expected!r}}}
if wanted & {{"takeoff", "goto"}}:
    wanted.add("arm")  # a vehicle takes off only once armed
need(not [t for _, t, _ in calls if t in flight and t not in wanted], "flight nobody asked for")
for n in {numbers!r}:
    need(n in answer, "answer lacks " + n)
'''


def gen_mcp(rng: random.Random) -> Task:
    choice = rng.choice((
        ("聯發科（2454）最近一個交易日的收盤價和成交量", "stock", "get_stock_daily_trading", {"stock_no": "2454"}, ["1412", "6720"], ["聯發科"]),
        ("中華電（2412）今年每股配多少現金股利", "stock", "get_company_dividend", {"code": "2412"}, ["4.98"], ["中華電"]),
        ("富邦金（2881）最新一季的 EPS", "stock", "get_company_income_statement", {"code": "2881"}, ["7.86"], ["富邦金"]),
        ("聯發科最新一個月的營收年增率", "stock", "get_company_monthly_revenue", {"code": "2454"}, ["18.6"], ["聯發科"]),
        ("外資在整個市場是買超還是賣超、多少", "stock", "get_twse_institutional_investors_summary", {}, ["42.7"], ["外資"]),
        ("歐元的即期賣出匯率", "stock", "get_exchange_rates", {}, ["35.52"], ["歐元"]),
        ("中華電今天漲跌多少", "stock", "get_stock_daily_trading", {"stock_no": "2412"}, ["0.5"], ["中華電"]),
        ("無人機現在電池還剩多少", "drone", "get_status", {}, ["64"], ["無人機", "電池"]),
        ("無人機現在在哪個位置（經緯度）", "drone", "get_position", {}, ["24.14", "120.67"], ["無人機"]),
        ("無人機能不能起飛，只要先檢查狀態，不要真的起飛", "drone", "get_status", {}, [], ["無人機"]),
        ("讓無人機起飛到 15 公尺", "drone", "takeoff", {"altitude_m": 15}, [], ["無人機", "15"]),
        ("讓無人機回到起飛點降落", "drone", "return_to_launch", {}, [], ["無人機"]),
    ))
    what, server, tool, args, numbers, must = choice
    t = Task("mcp", "change", f"主人想知道或要做：{what}。這台電腦接著台股資料和無人機的工具（沒有開檔案、指令和上網）。", must)
    t.text("README.txt", "workspace\n")
    servers = MCP_SERVERS
    if tool in ("land", "return_to_launch"):  # bringing it back needs it in the air
        servers = {**MCP_SERVERS, "drone": {**MCP_SERVERS["drone"],
                                            "env": {**MCP_SERVERS["drone"]["env"], "MCP_DRONE_START": "airborne"}}}
    t.extra.update({"permissions": {"edit": "deny", "shell": "deny", "web": "deny"}, "mcp_servers": servers,
                    "may_change": []})
    t.check = MCP_CHECK.format(expected=[(server, tool, args)], numbers=numbers)
    return t
