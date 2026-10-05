import sys, zipfile
from pathlib import Path
w = Path(sys.argv[1])
with zipfile.ZipFile(w / "報告.zip", "w") as f:
    for p in (w / "報告").iterdir():
        f.write(p, "報告/" + p.name)
