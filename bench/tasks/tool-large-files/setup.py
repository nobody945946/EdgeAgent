import sys
from pathlib import Path
w = Path(sys.argv[1]) / "下載"
(w / "舊東西").mkdir(parents=True)
sizes = {"安裝檔_v2.exe": 9_400_000, "家庭影片.mp4": 15_800_000, "簡報備份.pptx": 3_100_000,
         "舊東西/相簿.zip": 12_200_000, "清單.txt": 4_000, "發票.pdf": 380_000, "截圖.png": 2_900_000}
for rel, size in sizes.items():
    (w / rel).write_bytes(bytes(size))
