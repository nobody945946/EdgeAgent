import sys
from pathlib import Path

root = Path(sys.argv[1])
files = sorted((p for p in (root / "專案").rglob("*") if p.is_file()), key=lambda p: -p.stat().st_size)
lines = [f"{p.relative_to(root / '專案').as_posix()}\t{p.stat().st_size / 1e6:.1f} MB" for p in files[:5]]
(root / "最大檔案.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
