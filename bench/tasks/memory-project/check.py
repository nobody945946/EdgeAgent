import json, os, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
A = json.loads(Path(os.environ.get("BENCH_ANSWER", "/result/answer.json")).read_text(encoding="utf-8"))
ANSWER = re.sub(r"（主機核對來源：[^）]*）|（主機提醒：[^）]*）", "", A.get("answer", ""))
CALLS = A.get("calls", [])
MEMORY = A.get("memory", "")  # the owner's AGENTS.md as the conversation left it
HEADING = "# 主人的長期交代"
errors = []
def need(cond, why):
    if not cond:
        errors.append(why)
def commands():
    return [str((c.get("arguments") or {}).get("command", "")) for c in CALLS if c.get("name") == "powershell"]
need(A.get("outcome") == "done", "did not finish its reply")

runs = [c for c in commands() if "pytest" in c]
need(any(re.search(r"tests[\\/]fast", c) for c in runs), f"did not run tests/fast as the project's AGENTS.md says: {runs}")
need(not any(re.search(r"tests[\\/]slow", c) or not re.search(r"tests[\\/]", c) for c in runs),
     f"ran the slow tests the project's AGENTS.md says not to: {runs}")
need(re.search(r"3", ANSWER) and re.search(r"通過|passed|成功|都過", ANSWER), "does not report the 3 passing tests")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
