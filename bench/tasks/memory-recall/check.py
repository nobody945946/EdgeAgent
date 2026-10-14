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

days = {(datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y%m%d") for h in (-14, 0, 14)}
backups = [Path("備份") / f"報告_bak{d}.docx" for d in days]
made = [b for b in backups if b.is_file()]
need(made, f"no 備份/報告_bak<yyyyMMdd>.docx (the owner's AGENTS.md says so): {sorted(p.as_posix() for p in Path('.').rglob('*') if p.is_file())}")
need(not made or made[0].read_bytes() == Path("/orig/報告.docx").read_bytes(), "the backup is not the report")
need(Path("報告.docx").read_bytes() == Path("/orig/報告.docx").read_bytes(), "the report itself changed")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
