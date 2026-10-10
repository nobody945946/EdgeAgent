"""bench/mcp_eval's everyday stand-in servers, the train-mcp tasks
(bench/tool_tasks_mcp.py) and the mcp-* exam tasks on those servers: every
schema is answered and enforced with an error naming the parameter, the two
data variants hold different entities behind the same tools, and every check
passes the right calls and fails a wrong parameter."""

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

import tool_tasks_mcp as mcp

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
EVERYDAY = ("weather", "transit", "library", "home", "shop", "calendar", "parcel")
POOLS = {"ask": mcp.ASK, "not_found": mcp.NOT_FOUND, "lookup": mcp.LOOKUP, "do": mcp.DO, "cannot": mcp.CANNOT}
SCENARIOS = [s for pool in POOLS.values() for s in pool]


def session(server: str, calls, variant: str = "train", env=()) -> list[tuple[bool, dict]]:
    """Calls in one session: (failed, payload) each. The stock and drone
    stand-ins answer a failure in the payload without isError."""
    results = mcp.stand_in(server, [{"method": "tools/call", "params": {"name": t, "arguments": a}} for t, a in calls],
                           {"MCP_DATA_VARIANT": variant, **dict(env)})
    out = []
    for result in results:
        payload = json.loads(result["content"][0]["text"])
        failed = bool(result.get("isError")) or (isinstance(payload, dict) and (
            "error" in payload or payload.get("result") == "DENIED"))
        out.append((failed, payload))
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


def grade(check: Path, answer: str, calls, tmp: Path) -> bool:
    path = tmp / "answer.json"
    path.write_text(json.dumps({"answer": answer, "calls": [{"name": f"mcp__{s}__{t}", "arguments": a} for s, t, a in calls]},
                               ensure_ascii=False), encoding="utf-8")
    run = subprocess.run([sys.executable, "-X", "utf8", str(check)], capture_output=True, text=True, encoding="utf-8",
                         check=False, env={**os.environ, "BENCH_ANSWER": str(path)})
    return run.returncode == 0


def test_every_tool_names_an_unknown_or_missing_parameter_with_the_form_it_expects() -> None:
    for server in EVERYDAY:
        tools = mcp.tool_list(server)
        assert 3 <= len(tools) <= 6
        for tool in tools:
            (failed, payload), = session(server, [(tool["name"], {"bogus": 1})])
            named = {d["parameter"]: d for d in payload["details"]}
            assert failed and "bogus" in named, (server, tool["name"])
            assert all(p in tool["inputSchema"]["properties"] for p in named if p != "bogus")
            for required in tool["inputSchema"].get("required", []):
                assert named[required]["expected"] == tool["inputSchema"]["properties"][required]["description"]
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
])
def test_a_malformed_value_is_named_with_what_it_should_be(server: str, tool: str, args: dict, parameter: str) -> None:
    (failed, payload), = session(server, [(tool, args)])
    detail = next(d for d in payload["details"] if d["parameter"] == parameter)
    assert failed and detail["received"] == args[parameter] and detail["message"]


def test_a_rejected_call_changes_nothing() -> None:
    results = session("home", [("set_light", {"entity_id": "light.kitchen", "on": False, "brightness": 0}),
                               ("get_state", {"entity_id": "light.kitchen"}),
                               ("set_light", {"entity_id": "light.kitchen", "on": False}),
                               ("get_state", {"entity_id": "light.kitchen"})])
    assert results[0][0] and results[1][1]["on"] is True
    assert not results[2][0] and results[3][1]["on"] is False


def test_the_variants_declare_the_same_tools_over_different_entities() -> None:
    for server in EVERYDAY:
        train = mcp.stand_in(server, [{"method": "tools/list"}], {"MCP_DATA_VARIANT": "train"})
        held_out = mcp.stand_in(server, [{"method": "tools/list"}], {"MCP_DATA_VARIANT": "eval"})
        assert train == held_out, server  # one definition per tool (bench/dataset.py)
    for s in SCENARIOS:
        # Fees follow the carriers' fixed tables, the same in both variants.
        if s.server in EVERYDAY and s.numbers and s.expected[0][0] != "estimate_fee":
            calls = [*s.before, *s.expected]
            assert text(session(s.server, calls)) != text(session(s.server, calls, "eval")), s.what


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.what[:20])
def test_a_training_scenario_holds_its_answer_in_the_training_data(scenario: mcp.Scenario) -> None:
    declared = mcp.declared(scenario.server)
    for tool, args in (*scenario.before, *scenario.expected, *scenario.forbid):
        assert set(args) <= set(declared[tool]), (tool, args)
    assert (not scenario.expected) == (scenario in mcp.CANNOT)
    if not scenario.expected:
        return
    results = session(scenario.server, [*scenario.before, *scenario.expected], env=scenario.env)
    if scenario.cannot:  # the thing asked about does not exist
        assert results[-1][0] and not scenario.numbers
        return
    assert not any(failed for failed, _ in results), text(results)
    for number in first(scenario.numbers):
        assert number in facts(results), number


def test_training_reaches_every_tool_of_the_everyday_servers() -> None:
    used = {(s.server, tool) for s in SCENARIOS for tool, _ in (*s.before, *s.expected)}
    for server in EVERYDAY:
        assert {(server, t["name"]) for t in mcp.tool_list(server)} <= used, server


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
    calls = [(s, tool, args) for tool, args in (*scenario.before, *scenario.expected)]
    answer = "、".join(first(scenario.numbers)) or ("這些工具沒辦法做到。" if scenario.cannot else "好了")
    assert grade(check, answer, calls, tmp_path)
    if scenario.expected and scenario.expected[0][1]:
        tool, args = scenario.expected[0]
        others = [c for c in calls if c[1:] != (tool, args)]
        key = next(iter(args))
        lacks = next(k for k in ("name", "id", "query") if k not in mcp.declared(s)[tool])  # a parameter the schema lacks
        wrong = {**{k: v for k, v in args.items() if k != key}, lacks: args[key]}
        assert not grade(check, answer, [*others, (s, tool, wrong)], tmp_path)
        # Right values plus a parameter the schema lacks: the server refused that call.
        assert not grade(check, answer, [*others, (s, tool, {**args, lacks: "x"})], tmp_path)
    if scenario.numbers:
        assert not grade(check, "好了", calls, tmp_path)
    if scenario.cannot:
        assert not grade(check, "好的，已經處理好了。", calls, tmp_path)
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
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("EXPECTED", "NUMBERS", "FORBID", "CANNOT", "PARAMS", "CHANGES")}


EXAMS = sorted(p for p in (BENCH / "tasks").glob("mcp-*")
               if set(json.loads((p / "task.json").read_text(encoding="utf-8"))["mcp_servers"]) - {"stock", "drone"})


def test_the_exam_holds_two_or_three_tasks_on_each_of_at_least_four_new_servers() -> None:
    per_server = Counter(p.name.split("-")[1] for p in EXAMS)
    assert len(per_server) >= 4 and all(2 <= n <= 3 for n in per_server.values()), per_server


@pytest.mark.parametrize("task_dir", EXAMS, ids=lambda p: p.name)
def test_an_exam_task_is_answered_by_the_eval_data_only(task_dir: Path, tmp_path: Path) -> None:
    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    servers = validate_mcp_servers(spec["mcp_servers"])
    assert spec["kind"] == "mcp" and spec["prompt"] and len(servers) == 3
    assert not any(s["env"] for s in servers.values())  # the eval data is the default
    c = constants(task_dir / "check.py")
    solution = json.loads((task_dir / "solution_answer.json").read_text(encoding="utf-8"))
    calls = [(*call["name"].removeprefix("mcp__").split("__", 1), call["arguments"]) for call in solution["calls"]]
    check = task_dir / "check.py"
    assert c["CHANGES"] == [tuple(x) for x in mcp.changing(spec["mcp_servers"])]
    assert grade(check, solution["answer"], calls, tmp_path)
    assert not grade(check, "", [], tmp_path)
    for server in {s for s, _, _ in calls}:
        mine = [(t, a) for s, t, a in calls if s == server]
        results = session(server, mine, "eval")
        assert not any(failed for failed, _ in results), text(results)
        for number in first(c["NUMBERS"]):
            assert number in facts(results)
        if c["NUMBERS"]:  # no training data carries the exam's answer
            trained = session(server, mine, "train")
            assert any(failed for failed, _ in trained) or not all(n in facts(trained) for n in first(c["NUMBERS"]))
    for server, tool, args in c["EXPECTED"]:
        key = next(iter(args), None)
        if key:
            lacks = next(k for k in ("name", "id", "query") if k not in mcp.declared(server)[tool])
            wrong = {**{k: v for k, v in args.items() if k != key}, lacks: args[key]}
            assert not grade(check, solution["answer"], [x for x in calls if x[:2] != (server, tool)]
                             + [(server, tool, wrong)], tmp_path)
    for forbidden in c["FORBID"][:1]:
        assert not grade(check, solution["answer"], [*calls, forbidden], tmp_path)
    if c["CANNOT"]:
        assert not grade(check, "好的，已經處理好了。", [], tmp_path)
    training_args = [args for s in SCENARIOS for _, args in (*s.before, *s.expected)]
    for _, _, args in c["EXPECTED"]:
        assert not (args and args in training_args), args  # held-out entities
