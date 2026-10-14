import json, os, re, sys
from pathlib import Path
orig = Path(os.environ["BENCH_ORIG"]) / "logs"
want = {}
for p in sorted(orig.glob("*.log")):
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\S+) \S+ \[(\w+)\] (\w+):", line)
        if m and m.group(2) == "ERROR":
            want.setdefault(m.group(1), {}).setdefault(m.group(3), 0)
            want[m.group(1)][m.group(3)] += 1
try:
    got = json.loads(Path("錯誤統計.json").read_text(encoding="utf-8-sig"))
except Exception as exc:
    print(f"unreadable: {exc}"); sys.exit(1)
# A wrong shape is a wrong answer said plainly, not a grader crash.
if not isinstance(got, dict) or not all(isinstance(v, dict) for v in got.values()):
    print(f"not a {{date: {{module: count}}}} object: {str(got)[:200]}"); sys.exit(1)
got = {d: {m: n for m, n in v.items() if n} for d, v in got.items()}
print("ok" if got == want else f"got {got}\nwant {want}"); sys.exit(0 if got == want else 1)
