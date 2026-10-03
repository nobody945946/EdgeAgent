import random, sys
from pathlib import Path
root = Path(sys.argv[1]) / "下載"
root.mkdir(parents=True)
names = ["旅遊照片.jpg", "IMG_2041.JPG", "螢幕擷取畫面 2026-09-01.png", "貓.gif", "頭像.webp",
         "報價單.pdf", "會議記錄.docx", "預算.xlsx", "簡報 final.pptx", "說明.txt",
         "安裝檔備份.zip", "素材.rar", "舊專案.7z",
         "影片.mp4", "錄音 0903.m4a", "歌.mp3", "螢幕錄影.mov",
         "setup.exe", "font.ttf", "沒有副檔名"]
rng = random.Random(7)
for name in names:
    (root / name).write_bytes(name.encode() + bytes(rng.randrange(256) for _ in range(64)))
