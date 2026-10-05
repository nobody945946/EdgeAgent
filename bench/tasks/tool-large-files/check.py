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
for name in ("家庭影片.mp4", "相簿.zip", "安裝檔_v2.exe"):
    need(name in ANSWER, "missing " + name)
first = [ANSWER.find(n) for n in ("家庭影片.mp4", "相簿.zip", "安裝檔_v2.exe")]
need(all(x >= 0 for x in first) and first == sorted(first), "wrong order")
done()
