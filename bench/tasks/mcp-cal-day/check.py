import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "")
CALLS = A.get("calls", [])
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
EXPECTED = [('calendar', 'list_events', {})]
NUMBERS = ['客戶簡報', ['14:30', '14點30', '14點30分', '14點半', '2:30', '2點30', '2點30分', '2點半']]
PARAMS = {('calendar', 'list_events'): ['time_min', 'time_max', 'query']}
CHANGES = [('library', 'renew_item'), ('library', 'place_hold'), ('calendar', 'create_event'), ('calendar', 'delete_event')]
FORBID = []
CANNOT = False

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
    if re.match(r"\d{4}-\d{2}-\d{2}T", want):  # one instant, however its offset and seconds are written
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
answer = re.sub(r"[\s,]", "", ANSWER)
if CANNOT:
    need(re.search(r"(做不到|不能|無法|沒辦法|不行|不可以|沒有|查無|找不到|不存在|查不到|不支援|不提供)", answer),
         "should say it cannot be done or there is no such thing")
for forms in NUMBERS:
    forms = [forms] if isinstance(forms, str) else forms
    need(any(f in answer for f in forms), "answer lacks " + forms[0])
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
