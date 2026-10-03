import csv, io, sys
from pathlib import Path
root = Path(sys.argv[1])
totals = {}
for p in sorted((root / "報表").glob("*.csv")):
    for row in csv.DictReader(io.StringIO(p.read_bytes().decode("big5"))):
        totals[row["類別"]] = totals.get(row["類別"], 0) + int(row["金額"])
rows = sorted(totals.items(), key=lambda kv: -kv[1])
text = "類別,總金額\n" + "".join(f"{k},{v}\n" for k, v in rows)
(root / "年度統計.csv").write_bytes(text.encode("utf-8-sig"))
