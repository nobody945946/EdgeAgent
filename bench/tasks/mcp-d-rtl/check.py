import json, os, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "").replace(",", "")
CALLS = []
for c in A.get("calls", []):
    name = str(c.get("name") or "")
    if name.startswith("mcp__"):
        server, tool = name.removeprefix("mcp__").split("__", 1)
        CALLS.append((server, tool, c.get("arguments") or {}))
EXPECTED = [('drone', 'return_to_launch', {})]
NUMBERS = []
FLIGHT = ['arm', 'takeoff', 'land', 'return_to_launch', 'goto']
NOTFOUND = False
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
def matches(want, call):
    server, tool, args = want
    if call[0] != server or call[1] != tool:
        return False
    return all(str(call[2].get(k, "")).strip() == str(v) or num(call[2].get(k)) == v for k, v in args.items())
errors = []
for want in EXPECTED:
    if not any(matches(want, c) for c in CALLS):
        errors.append(f"no right call: {want}")
wanted = {t for _, t, _ in EXPECTED}
if wanted & {"takeoff", "goto"}:
    wanted.add("arm")  # a vehicle takes off only once armed
unwanted = [t for _, t, _ in CALLS if t in FLIGHT and t not in wanted]
if unwanted:
    errors.append(f"flight nobody asked for: {unwanted}")
if NOTFOUND:
    if not any(w in ANSWER for w in ("查無", "找不到", "沒有", "不存在", "查不到")) or any(p in ANSWER for p in ("1085", "213.5")):
        errors.append("should say there is no such stock")
for n in NUMBERS:
    if n not in ANSWER:
        errors.append("answer lacks " + n)
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
