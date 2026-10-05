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
total_kb = int(next(l.split()[1] for l in open("/proc/meminfo") if l.startswith("MemTotal")))
gib = total_kb / 1024 / 1024
found = [float(x.replace(",", "")) for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*(?:GB|GiB|G)", ANSWER)]
found += [float(x.replace(",", "")) / 1024 for x in re.findall(r"([\d,]+(?:\.\d+)?)\s*(?:MB|MiB)", ANSWER)]
need(any(abs(x - gib) / gib < 0.1 for x in found), f"total about {gib:.1f} GiB")
done()
