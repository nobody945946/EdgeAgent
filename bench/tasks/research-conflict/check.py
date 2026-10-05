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
both = "1250" in ANSWER and "980" in ANSWER
need(both or re.search(r"(不一|不同|分歧|矛盾|有出入|兩種)", ANSWER), "should show the sources disagree")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
