import csv
import sys
from datetime import datetime
from pathlib import Path

root = Path(sys.argv[1]) / "相機"
seen = {}
for row in csv.DictReader((root / "拍攝時間.csv").open(encoding="utf-8")):
    stem = datetime.strptime(row["拍攝時間"], "%Y/%m/%d %H:%M").strftime("%Y-%m-%d_%H%M")
    seen[stem] = seen.get(stem, 0) + 1
    suffix = "" if seen[stem] == 1 else f"_{seen[stem]}"
    (root / row["檔名"]).rename(root / f"{stem}{suffix}.jpg")
