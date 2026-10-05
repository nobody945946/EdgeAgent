import json, os, re, sys
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
# The model's own words: the host's notices are not its answer.
ANSWER = re.sub(r"（主機核對來源：[^）]*）|（主機提醒：[^）]*）", "", A.get("answer", "")).replace(",", "")
CALLS = [c.get("name") for c in A.get("calls", [])]
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
need(re.search(r"\[\d+\]", ANSWER), "no cited passage numbers")
need("web_research" in CALLS, "did not research")
need("網友" in ANSWER or "看法" in ANSWER or "意見" in ANSWER, "marks them as opinions")
need(re.search(r"(冷氣|座位)", ANSWER), "reports what people said")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
