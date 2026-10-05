import sys
from pathlib import Path
w = Path(sys.argv[1]) / "暫存"
w.mkdir()
for i in range(3):
    (w / f"a{i}.tmp").write_text("x")
(w / "保留.txt").write_text("keep")
