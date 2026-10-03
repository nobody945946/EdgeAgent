import random
import sys
from pathlib import Path

root = Path(sys.argv[1]) / "專案"
rng = random.Random(9)
paths = ["影片/成果展.mp4", "影片/raw/拍攝_01.mov", "資料/匯出.csv", "資料/備份/舊資料庫.bak",
         "src/main.py", "src/vendor/lib.so", "文件/規格書.pdf", "文件/圖/封面.png",
         "node_modules/pkg/index.js", "log/app.log", "log/old/app-2025.log", "build/out.bin"]
sizes = [9_100_000, 15_300_000, 2_200_000, 7_800_000, 4_000, 3_100_000, 1_500_000,
         600_000, 20_000, 5_200_000, 11_900_000, 6_400_000]
for rel, size in zip(paths, sizes, strict=True):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(rng.randrange(256) for _ in range(256)) * (size // 256))
