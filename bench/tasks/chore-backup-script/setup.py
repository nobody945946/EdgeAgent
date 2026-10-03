import sys
from pathlib import Path

root = Path(sys.argv[1]) / "文件"
for rel, text in {"報告.docx": "r", "預算.xlsx": "b", "~$報告.docx": "lock", "草稿.tmp": "t",
                  "照片/旅行.jpg": "j", "照片/~$相簿.pptx": "lock2", "合約/2026/租約.pdf": "p"}.items():
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
