import random
import sys
from pathlib import Path

root = Path(sys.argv[1]) / "相機"
root.mkdir(parents=True)
rng = random.Random(4)
times = ["2026/09/03 14:22", "2026/09/03 14:22", "2026/09/03 18:05", "2026/9/4 07:30",
         "2026/09/04 07:31", "2026/10/01 23:59", "2026/10/01 23:59", "2026/10/01 23:59"]
rows = ["檔名,拍攝時間"]
for i, t in enumerate(times, 1):
    name = f"IMG_{i:04d}.JPG"
    (root / name).write_bytes(name.encode() + bytes(rng.randrange(256) for _ in range(500)))
    rows.append(f"{name},{t}")
(root / "拍攝時間.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
