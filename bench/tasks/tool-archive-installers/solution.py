import sys
from pathlib import Path
w = Path(sys.argv[1])
(w / "封存").mkdir()
for p in (w / "下載").glob("*.exe"):
    p.rename(w / "封存" / p.name)
