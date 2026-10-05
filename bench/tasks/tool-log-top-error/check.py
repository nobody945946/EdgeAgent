import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = A.get("answer", "")
CALLS = A.get("calls", [])
REFUSE = re.compile(r"(做不到|不能|無法|沒辦法|不行|不可以|碰不到|沒有權限|無權|不會幫|不方便)")
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
def done():
    print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
log = Path("/work/server.log").read_text(encoding="utf-8").splitlines()
count = sum("connection timeout" in line for line in log)
need("connection timeout" in ANSWER or "連線逾時" in ANSWER or "連線超時" in ANSWER, "top error")
need(re.search(rf"(?<!\d){count}(?!\d)", ANSWER), f"count {count}")
done()
