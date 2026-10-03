import os
import sys
from pathlib import Path

orig = Path(os.environ["BENCH_ORIG"]) / "專案"
files = sorted((p for p in orig.rglob("*") if p.is_file()), key=lambda p: -p.stat().st_size)
want = [p.relative_to(orig).as_posix() for p in files[:5]]
out = Path("最大檔案.txt")
if not out.is_file():
    print("最大檔案.txt missing"); sys.exit(1)
lines = [line.replace("\\", "/") for line in out.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
got = []
for line in lines:
    hit = [w for w in want if w in line] or [w for w in want if w.split("/")[-1] in line]
    if hit:
        got.append(hit[0])
ok = got == want and len(lines) == 5
print("ok" if ok else f"got {got} ({len(lines)} lines) want {want}"); sys.exit(0 if ok else 1)
