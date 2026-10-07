import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = re.sub(r"（主機核對來源：[^）]*）|（主機提醒：[^）]*）", "", A.get("answer", ""))
CALLS = [c.get("name") for c in A.get("calls", [])]
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
need(A.get("outcome") == "done", "did not finish its reply")
need(len(ANSWER.strip()) >= 2, "no answer")
need(not CALLS, f"used tools for plain conversation: {CALLS}")
need(re.search(r"本機|這台電腦|你的電腦|你這台|筆電|電腦上", ANSWER), "does not say it runs on this computer")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
