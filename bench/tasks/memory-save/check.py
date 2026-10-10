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

need(HEADING in MEMORY, "the owner's AGENTS.md lost its heading")
need(re.search(r"BOM", MEMORY) and re.search(r"UTF-?8", MEMORY, re.I), f"not written into the owner's AGENTS.md: {MEMORY[-200:]!r}")
need(not [p for p in Path(".").rglob("*") if p.is_file()], "wrote files in the work folder instead")
need(len(ANSWER.strip()) >= 2, "no answer")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
