import sys
from pathlib import Path
w = Path(sys.argv[1]) / "報告"
w.mkdir()
(w / "第一季.txt").write_text("營收 120 萬\n", encoding="utf-8")
(w / "第二季.txt").write_text("營收 135 萬\n", encoding="utf-8")
(w / "圖表.png").write_bytes(b"\x89PNG" + bytes(5000))
