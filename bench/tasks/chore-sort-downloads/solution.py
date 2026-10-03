import sys
from pathlib import Path
root = Path(sys.argv[1]) / "下載"
kinds = {"圖片": {".jpg", ".png", ".gif", ".webp"}, "文件": {".pdf", ".docx", ".xlsx", ".pptx", ".txt"},
         "壓縮檔": {".zip", ".rar", ".7z"}, "影音": {".mp4", ".m4a", ".mp3", ".mov"}}
for p in list(root.iterdir()):
    folder = next((k for k, v in kinds.items() if p.suffix.lower() in v), "其他")
    (root / folder).mkdir(exist_ok=True)
    p.rename(root / folder / p.name)
