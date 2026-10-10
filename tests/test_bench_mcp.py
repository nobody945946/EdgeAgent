"""bench/mcp_eval's stand-in servers, the train-mcp tasks
(bench/tool_tasks_mcp.py) and the mcp-* exam tasks (bench/mcp_exam.py): every
schema is answered and enforced with an error naming the parameter, the stock
stand-in holds to TWStockMCPServer's own schemas, the two data variants hold
different entities behind the same tools, the calendar never reaches
training, and every check passes the right calls and fails a wrong one."""

from __future__ import annotations

import ast
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from edge_agent.mcp import validate_mcp_servers

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import mcp_exam
import tool_tasks_mcp as mcp

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
EVERYDAY = ("weather", "transit", "library", "home", "shop", "calendar", "parcel")
ALL = ("stock", "drone", *EVERYDAY)
POOLS = {"ask": mcp.ASK, "not_found": mcp.NOT_FOUND, "lookup": mcp.LOOKUP, "do": mcp.DO, "cannot": mcp.CANNOT}
SCENARIOS = [s for pool in POOLS.values() for s in pool]
EXAMS = sorted(p for p in (BENCH / "tasks").glob("mcp-*") if (p / "task.json").is_file())


def session(server: str, calls, variant: str = "train", env=()) -> list[tuple[bool, dict]]:
    """Calls in one session: (failed, payload) each; a vehicle's refusal
    (result DENIED) is its answer, not an error result, and counts as failed."""
    results = mcp.stand_in(server, [{"method": "tools/call", "params": {"name": t, "arguments": a}} for t, a in calls],
                           {"MCP_DATA_VARIANT": variant, **dict(env)})
    out = []
    for result in results:
        payload = json.loads(result["content"][0]["text"])
        out.append((bool(result.get("isError")) or (isinstance(payload, dict) and payload.get("result") == "DENIED"),
                    payload))
    return out


def text(results: list[tuple[bool, dict]]) -> str:
    return "\n".join(json.dumps(payload, ensure_ascii=False) for _, payload in results)


def facts(results: list[tuple[bool, dict]]) -> str:
    """What an answer may state from these results: the payloads, and a bus's
    EstimateTime (seconds) in minutes, as the owner would be told."""
    payloads = text(results)
    return payloads + "\n" + " ".join(str(int(s) // 60) for s in re.findall(r'"EstimateTime": (\d+)', payloads))


def first(numbers) -> list[str]:
    return [n if isinstance(n, str) else n[0] for n in numbers]


def options(args) -> list[dict]:
    return args if isinstance(args, list) else [args]


def ways(scenario: mcp.Scenario) -> list[list[tuple[str, dict]]]:
    """Each way to make the scenario's calls: the i-th alternative of every expected call."""
    count = max((len(options(a)) for _, a in scenario.expected), default=1)
    return [[*scenario.before, *((tool, options(args)[min(i, len(options(args)) - 1)]) for tool, args in scenario.expected)]
            for i in range(count)]


def grade(check: Path, answer: str, calls, tmp: Path) -> bool:
    path = tmp / "answer.json"
    path.write_text(json.dumps({"answer": answer, "calls": [{"name": f"mcp__{s}__{t}", "arguments": a} for s, t, a in calls]},
                               ensure_ascii=False), encoding="utf-8")
    run = subprocess.run([sys.executable, "-X", "utf8", str(check)], capture_output=True, text=True, encoding="utf-8",
                         check=False, env={**os.environ, "BENCH_ANSWER": str(path)})
    return run.returncode == 0


def test_every_tool_names_an_unknown_or_missing_parameter_with_the_form_it_expects() -> None:
    for server in ALL:
        tools = mcp.tool_list(server)
        assert server in ("stock", "drone") or 3 <= len(tools) <= 6
        for tool in tools:
            (failed, payload), = session(server, [(tool["name"], {"bogus": 1})])
            named = {d["parameter"]: d for d in payload["details"]}
            assert failed and named["bogus"]["received"] == 1, (server, tool["name"])
            assert all(p in tool["inputSchema"]["properties"] for p in named if p != "bogus")
            for required in tool["inputSchema"].get("required", []):
                assert named[required]["expected"], (server, tool["name"], required)
        (failed, payload), = session(server, [("no_such_tool", {})])
        assert failed and all(t["name"] in payload["error"] for t in tools)


@pytest.mark.parametrize(("server", "tool", "args", "parameter"), [
    ("weather", "get_current_observation", {"station_id": "宜蘭"}, "station_id"),
    ("weather", "get_rainfall_history", {"station_id": "467480", "from_date": "9/10", "to_date": "2026-09-15"}, "from_date"),
    ("transit", "get_rail_timetable", {"OriginStationID": "960", "DestinationStationID": "1210", "TrainDate": "2026-09-22"},
     "OriginStationID"),
    ("transit", "get_bus_arrival", {"City": "台中", "RouteName": "300", "StopName": "科博館"}, "City"),
    ("library", "get_holdings", {"isbn13": "978-957-138-622-0"}, "isbn13"),
    ("library", "place_hold", {"isbn13": "9789863847216", "pickup_branch": "東區分館"}, "pickup_branch"),
    ("home", "set_light", {"entity_id": "客廳吸頂燈", "on": False}, "entity_id"),
    ("home", "set_climate", {"entity_id": "climate.bedroom_ac", "hvac_mode": "cool", "temperature": "26"}, "temperature"),
    ("shop", "list_orders", {"date_from": "2026-09-17"}, "date_from"),
    ("shop", "ship_order", {"order_no": "SO20260916-0042", "carrier": "黑貓", "tracking_no": "905512349911"}, "carrier"),
    ("calendar", "list_events", {"time_min": "2026-09-22", "time_max": "2026-09-23"}, "time_min"),
    ("calendar", "find_free_time", {"date": "2026-09-23", "duration_minutes": 5}, "duration_minutes"),
    ("parcel", "track", {"carrier": "tcat", "tracking_no": "9055-8812-0034"}, "tracking_no"),
    ("stock", "get_stock_daily_trading", {"stock_no": "2454", "date": "2026-09-18"}, "date"),
    ("stock", "get_stock_daily_trading", {"stock_no": "聯發科"}, "stock_no"),
    ("stock", "get_stock_history", {"stock_no": "2454", "date": "2026-09"}, "date"),
    ("stock", "get_company_monthly_revenue", {"code": "2454", "start_month": "2026-07"}, "start_month"),
    ("stock", "get_company_dividend", {"code": "2454", "year_type": "cash"}, "year_type"),
    ("stock", "get_company_income_statement", {"code": "2454", "year": "2026", "season": 5}, "season"),
    ("drone", "takeoff", {"altitude_m": "fifteen"}, "altitude_m"),
])
def test_a_malformed_value_is_named_with_what_it_should_be(server: str, tool: str, args: dict, parameter: str) -> None:
    (failed, payload), = session(server, [(tool, args)])
    detail = next(d for d in payload["details"] if d["parameter"] == parameter)
    assert failed and detail["received"] == args[parameter] and detail["message"]


def test_the_stock_stand_in_publishes_twstock_s_own_tools() -> None:
    real = {t["name"]: t for t in json.loads((BENCH / "mcp_eval/twstock_tools.json").read_text(encoding="utf-8"))}
    tools = mcp.tool_list("stock")
    assert [t["name"] for t in tools] == mcp.MCP_STOCK_TOOLS
    for t in tools:
        assert t == {"name": t["name"], "description": real[t["name"]]["description"],
                     "inputSchema": real[t["name"]]["inputSchema"]}


def test_stock_name_takes_one_company_and_says_how_to_ask_for_several() -> None:
    by_name, by_part, both, listed, market = session("stock", [
        ("get_stock_daily_trading", {"name": "中華電"}), ("get_stock_daily_trading", {"name": "富邦"}),
        ("get_stock_daily_trading", {"name": "中華電 OR 富邦金"}), ("get_stock_daily_trading", {"name": "中華電、富邦金"}),
        ("get_stock_daily_trading", {})])
    assert not by_name[0] and [s["stock_no"] for s in by_name[1]["stocks"]] == ["2412"]
    assert [s["stock_no"] for s in by_part[1]["stocks"]] == ["2881"]
    for failed, payload in (both, listed):
        assert failed and payload["parameter"] == "name" and "分開呼叫" in payload["error"]
    assert {s["stock_no"] for s in market[1]["stocks"]} == {"2454", "2412", "2881"}
    (failed, payload), = session("stock", [("get_stock_daily_trading", {"name": "台積電"})])
    assert failed and payload["parameter"] == "name"  # not in the training data


def test_each_stock_tool_takes_only_the_parameter_it_declares() -> None:
    daily, dividend, history, by_month = session("stock", [
        ("get_stock_daily_trading", {"code": "2454"}), ("get_company_dividend", {"stock_no": "2454"}),
        ("get_stock_history", {"stock_no": "2454", "month": "2026-09"}),
        ("get_stock_history", {"stock_no": "2454", "date": "20260901"})])
    assert daily[0] and [d["parameter"] for d in daily[1]["details"]] == ["code"]
    assert dividend[0] and {d["parameter"] for d in dividend[1]["details"]} == {"stock_no", "code"}
    assert history[0] and {d["parameter"] for d in history[1]["details"]} == {"month", "date"}
    assert not by_month[0] and by_month[1]["month"] == "2026-09"


def test_stock_dates_are_trading_days_and_required_where_the_schema_says() -> None:
    missing, weekend, future, trading = session("stock", [
        ("get_twse_institutional_investors_summary", {}),
        ("get_twse_institutional_investors_summary", {"date": "20261003"}),
        ("get_twse_institutional_investors_summary", {"date": "20991231"}),
        ("get_twse_institutional_investors_summary", {"date": "20260918"})])
    assert missing[0] and missing[1]["details"][0]["parameter"] == "date" and "YYYYMMDD" in missing[1]["details"][0]["expected"]
    assert weekend[0] and "交易日" in weekend[1]["error"] and weekend[1]["received"] == "20261003"
    assert future[0] and "尚未公布" in future[1]["error"]
    assert not trading[0] and trading[1]["date"] == "2026-09-18" and trading[1]["foreign_net_billion"] == 42.7
    (failed, payload), = session("stock", [("get_company_income_statement", {"code": "2454", "year": "2026"})])
    assert failed and payload["parameter"] == "season"


def test_a_rejected_call_changes_nothing() -> None:
    results = session("home", [("set_light", {"entity_id": "light.kitchen", "on": False, "brightness": 0}),
                               ("get_state", {"entity_id": "light.kitchen"}),
                               ("set_light", {"entity_id": "light.kitchen", "on": False}),
                               ("get_state", {"entity_id": "light.kitchen"})])
    assert results[0][0] and results[1][1]["on"] is True
    assert not results[2][0] and results[3][1]["on"] is False
    flight = session("drone", [("arm", {}), ("takeoff", {"altitude_m": 15, "speed": 3}), ("get_position", {})])
    assert flight[1][0] and flight[2][1]["relative_alt_m"] == 0


def test_the_variants_declare_the_same_tools_over_different_entities() -> None:
    for server in ALL:
        train = mcp.stand_in(server, [{"method": "tools/list"}], {"MCP_DATA_VARIANT": "train"})
        held_out = mcp.stand_in(server, [{"method": "tools/list"}], {"MCP_DATA_VARIANT": "eval"})
        assert train == held_out, server  # one definition per tool (bench/dataset.py)
    for s in SCENARIOS:
        # Fees follow the carriers' fixed tables, the same in both variants.
        if s.numbers and s.expected[0][0] != "estimate_fee":
            calls = ways(s)[0]
            assert text(session(s.server, calls, env=s.env)) != text(session(s.server, calls, "eval", s.env)), s.what


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.what[:20])
def test_a_training_scenario_holds_its_answer_in_the_training_data(scenario: mcp.Scenario) -> None:
    declared = mcp.declared(scenario.server)
    for tool, args in (*scenario.before, *scenario.expected, *scenario.forbid):
        assert all(set(o) <= set(declared[tool]) for o in options(args)), (tool, args)
    assert (not scenario.expected) == (scenario in mcp.CANNOT)
    if not scenario.expected:
        return
    for calls in ways(scenario):  # every alternative the check accepts gets the answer
        results = session(scenario.server, calls, env=scenario.env)
        if scenario.cannot:  # the thing asked about does not exist
            assert results[-1][0] and not scenario.numbers
            continue
        assert not any(failed for failed, _ in results), text(results)
        for number in first(scenario.numbers):
            assert number in facts(results), number


def test_training_reaches_every_tool_of_the_servers_it_connects() -> None:
    used = {(s.server, tool) for s in SCENARIOS for tool, _ in (*s.before, *s.expected)}
    for server in mcp.MCP_SERVERS:
        if server != "stock":  # eleven tools picked from TWStockMCPServer's 173
            assert {(server, t["name"]) for t in mcp.tool_list(server)} <= used, server


def test_the_calendar_never_reaches_training() -> None:
    import make_tool_tasks

    assert "calendar" in mcp.SERVERS and "calendar" not in mcp.MCP_SERVERS
    assert not [s for s in SCENARIOS if s.server in mcp.HELD_OUT]
    rng = random.Random(3)
    for kind in ("mcp_ask", "mcp_lookup", "mcp_do"):
        for _ in range(60):
            task = make_tool_tasks.GENERATORS[kind](rng)
            assert "calendar" not in task.extra["mcp_servers"] and "行事曆" not in task.situation
            assert "calendar" not in task.check
    assert len([p for p in EXAMS if p.name.startswith("mcp-cal-")]) >= 2


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.what[:20])
def test_a_training_task_passes_its_solution_and_fails_a_wrong_call(scenario: mcp.Scenario, tmp_path: Path) -> None:
    task = mcp.task_of(scenario, random.Random(len(scenario.what)))
    task.write(tmp_path, "task")
    spec = json.loads((tmp_path / "task" / "task.json").read_text(encoding="utf-8"))
    servers = validate_mcp_servers(spec["mcp_servers"])
    assert spec["kind"] == "train-mcp" and scenario.server in servers and 2 <= len(servers) <= 3
    assert all(s["env"]["MCP_DATA_VARIANT"] == "train" for s in servers.values())
    check = tmp_path / "task" / "check.py"
    s = scenario.server
    answer = "、".join(first(scenario.numbers)) or ("這些工具沒辦法做到。" if scenario.cannot else "好了")
    for way in ways(scenario):
        assert grade(check, answer, [(s, tool, args) for tool, args in way], tmp_path), way
    calls = [(s, tool, args) for tool, args in ways(scenario)[0]]
    if scenario.expected and options(scenario.expected[0][1])[0]:
        tool, args = scenario.expected[0][0], options(scenario.expected[0][1])[0]
        others = [c for c in calls if c[1:] != (tool, args)]
        key = next(iter(args))
        lacks = next(k for k in ("name", "id", "query") if k not in mcp.declared(s)[tool])  # a parameter the schema lacks
        assert not grade(check, answer, [*others, (s, tool, {**{k: v for k, v in args.items() if k != key}, lacks: args[key]})],
                         tmp_path)
        # Right values plus a parameter the schema lacks: the server refused that call.
        assert not grade(check, answer, [*others, (s, tool, {**args, lacks: "x"})], tmp_path)
        required = mcp.schema(s, tool)["required"]
        if required:  # without a required parameter Pi never sends it on
            assert not grade(check, answer, [*others, (s, tool, {k: v for k, v in args.items() if k != required[0]})],
                             tmp_path)
    if scenario.numbers:
        assert not grade(check, "好了", calls, tmp_path)
    if scenario.cannot:
        assert not grade(check, "好的，已經處理好了。", calls, tmp_path)
    if scenario.absent:
        assert not grade(check, f"{answer}{scenario.absent[0]}", calls, tmp_path)
    wanted = {(s, t) for t, _ in scenario.expected} | ({(s, "arm")} if s == "drone" else set())
    unasked = [c for c in mcp.changing(spec["mcp_servers"]) if c not in wanted]
    if unasked:
        assert not grade(check, answer, [*calls, (*unasked[0], {})], tmp_path)
    if scenario.forbid:
        assert not grade(check, answer, [*calls, (s, *scenario.forbid[0])], tmp_path)


def test_the_cut_makes_three_mcp_kinds_with_a_few_impossible_asks() -> None:
    import make_tool_tasks

    assert {k for k in make_tool_tasks.GENERATORS if k.startswith("mcp")} == {"mcp_ask", "mcp_lookup", "mcp_do"}
    rng = random.Random(11)
    kinds = Counter()
    for kind in ("mcp_ask", "mcp_lookup", "mcp_do"):
        for _ in range(40):
            task = make_tool_tasks.GENERATORS[kind](rng)
            kinds[kind, "CANNOT = True" in task.check] += 1
    assert 0 < kinds["mcp_ask", True] < kinds["mcp_ask", False]
    assert 0 < kinds["mcp_do", True] < kinds["mcp_do", False]
    assert kinds["mcp_lookup", True] == 0


def constants(check: Path) -> dict:
    tree = ast.parse(check.read_text(encoding="utf-8"))
    return {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper()
            and node.targets[0].id not in ("A", "ANSWER", "CALLS", "MCP")}


def test_the_exam_files_are_the_table_s() -> None:
    assert [p.name for p in EXAMS] == sorted(name for name, _, _ in mcp_exam.EXAMS)
    for name, names, scenario in mcp_exam.EXAMS:
        for file, content in mcp_exam.files(name, names, scenario).items():
            assert (BENCH / "tasks" / name / file).read_text(encoding="utf-8") == content, (name, file)


def test_the_exam_holds_two_or_three_tasks_on_each_of_at_least_four_new_servers() -> None:
    per_server = Counter(p.name.split("-")[1] for p in EXAMS if p.name.split("-")[1] not in ("s", "d"))
    assert len(per_server) >= 4 and all(2 <= n <= 3 for n in per_server.values()), per_server


@pytest.mark.parametrize("task_dir", EXAMS, ids=lambda p: p.name)
def test_an_exam_task_is_answered_by_the_eval_data_only(task_dir: Path, tmp_path: Path) -> None:
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    servers = validate_mcp_servers(spec["mcp_servers"])
    assert spec["kind"] == "mcp" and spec["prompt"] and len(servers) >= 2
    assert not any("MCP_DATA_VARIANT" in s["env"] for s in servers.values())  # the eval data is the default
    assert "拍照" not in json.dumps(spec, ensure_ascii=False)  # no tool takes a picture
    c = constants(task_dir / "check.py")
    solution = json.loads((task_dir / "solution_answer.json").read_text(encoding="utf-8"))
    calls = [(*call["name"].removeprefix("mcp__").split("__", 1), call["arguments"]) for call in solution["calls"]]
    check = task_dir / "check.py"
    assert c["CHANGES"] == [tuple(x) for x in mcp.changing(spec["mcp_servers"])]
    assert grade(check, solution["answer"], calls, tmp_path)
    assert not grade(check, "", [], tmp_path)
    scenario = next(sc for name, _, sc in mcp_exam.EXAMS if name == task_dir.name)
    for way in ways(scenario):
        env = servers[scenario.server]["env"]
        results = session(scenario.server, way, "eval", env)
        if c["CANNOT"]:
            assert not scenario.expected or results[-1][0]
            continue
        assert not any(failed for failed, _ in results), text(results)
        for number in first(c["NUMBERS"]):
            assert number in facts(results)
        if c["NUMBERS"]:  # no training data carries the exam's answer
            trained = session(scenario.server, way, "train", env)
            assert any(failed for failed, _ in trained) or not all(n in facts(trained) for n in first(c["NUMBERS"]))
        assert grade(check, solution["answer"], [(scenario.server, t, a) for t, a in way], tmp_path)
    for server, tool, args in c["EXPECTED"]:
        args = options(args)[0]
        others = [x for x in calls if x[:2] != (server, tool)]
        lacks = next(k for k in ("name", "id", "query") if k not in mcp.declared(server)[tool])
        if args:
            key = next(iter(args))
            wrong = {**{k: v for k, v in args.items() if k != key}, lacks: args[key]}
            assert not grade(check, solution["answer"], [*others, (server, tool, wrong)], tmp_path)
        assert not grade(check, solution["answer"], [*others, (server, tool, {**args, lacks: "x"})], tmp_path)
        required = mcp.schema(server, tool)["required"]
        if required:  # a call Pi refused for lacking a required parameter is no right call
            solved = next(a for s, t, a in calls if (s, t) == (server, tool))
            assert not grade(check, solution["answer"],
                             [*others, (server, tool, {k: v for k, v in solved.items() if k != required[0]})], tmp_path)
    for forbidden in c["FORBID"][:1]:
        assert not grade(check, solution["answer"], [*calls, forbidden], tmp_path)
    for absent in c["ABSENT"][:1]:
        assert not grade(check, solution["answer"] + absent, calls, tmp_path)
    if c["CANNOT"]:
        assert not grade(check, "好的，已經處理好了。", calls, tmp_path)
    training = [o for s in SCENARIOS for _, a in (*s.before, *s.expected) for o in options(a)]
    for _, _, args in c["EXPECTED"]:
        for o in options(args):
            assert not (o and o != mcp.MARKET and o in training), o  # held-out entities
