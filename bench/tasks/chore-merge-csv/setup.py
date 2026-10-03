import random, sys
from pathlib import Path
root = Path(sys.argv[1]) / "報表"
root.mkdir(parents=True)
rng = random.Random(11)
cats = ["餐飲", "交通", "房租", "水電"]
for m in range(1, 13):
    lines = ["日期,類別,金額"]
    for d in range(1, 6):
        lines.append(f"2025/{m}/{d},{rng.choice(cats)},{rng.randrange(50, 3000)}")
    (root / f"2025-{m:02d}.csv").write_bytes(("\r\n".join(lines) + "\r\n").encode("big5"))
