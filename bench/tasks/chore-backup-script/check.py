import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

want = {"報告.docx", "預算.xlsx", "照片/旅行.jpg", "合約/2026/租約.pdf"}
def listing(folder):
    # A backup that copied itself into itself nests too deep to walk: say so.
    try:
        return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    except RecursionError:
        return {"(the backup copied itself into itself)"}
days = {(datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y%m%d") for h in (-14, 0, 14)}
errors = []
script = Path("備份.ps1")
if not script.is_file():
    print("備份.ps1 missing"); sys.exit(1)
made = [Path("備份") / d for d in days if (Path("備份") / d).is_dir()]
if not made or listing(made[0]) != want:
    errors.append(f"first run produced {[listing(m) for m in made]}")
shutil.rmtree("備份", ignore_errors=True)
run = subprocess.run(["pwsh", "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=120)
made = [Path("備份") / d for d in days if (Path("備份") / d).is_dir()]
if run.returncode != 0 or not made or listing(made[0]) != want:
    errors.append(f"rerun exit {run.returncode}: {[listing(m) for m in made]} {run.stderr[-300:]}")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
