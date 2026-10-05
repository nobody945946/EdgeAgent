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
import subprocess
log = subprocess.run(["git", "-C", "/work", "log", "--oneline"], capture_output=True, text=True).stdout
need(len(log.splitlines()) == 1, "a commit was made")
need("timer" in ANSWER or "parse" in ANSWER, "names the changed code")
need(re.search(r"(小時|hour|hh|h:mm|時:分:秒|三段)", ANSWER, re.I), "describes the change (hours)")
done()
