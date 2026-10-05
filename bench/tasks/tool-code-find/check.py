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
lines = Path("/work/shop/pricing.py").read_text().splitlines()
line = next(i for i, l in enumerate(lines, 1) if l.startswith("def shipping_fee"))
need("pricing.py" in ANSWER, "file pricing.py")
need(re.search(rf"(?<!\d){line}(?!\d)", ANSWER), f"line {line} (def shipping_fee)")
done()
