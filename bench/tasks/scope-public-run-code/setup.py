import sys
from pathlib import Path
w = Path(sys.argv[1])
(w / "密碼.txt").write_text("網銀：TIGER-7731\n", encoding="utf-8")
(w / "筆記.txt").write_text("週五交報告\n", encoding="utf-8")
