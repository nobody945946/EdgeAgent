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
need("海邊.jpg" in ANSWER and "IMG_0412.jpg" in ANSWER, "missing pair 海邊/IMG_0412")
need("生日.jpg" in ANSWER and "生日 (1).jpg" in ANSWER, "missing pair 生日")
done()
