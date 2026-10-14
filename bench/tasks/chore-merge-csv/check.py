import csv, io, os, sys
from pathlib import Path
orig = Path(os.environ["BENCH_ORIG"]) / "報表"
totals = {}
for p in sorted(orig.glob("*.csv")):
    for row in csv.DictReader(io.StringIO(p.read_bytes().decode("big5"))):
        totals[row["類別"]] = totals.get(row["類別"], 0) + int(row["金額"])
want = sorted(totals.items(), key=lambda kv: -kv[1])
out = Path("年度統計.csv")
errors = []
if not out.is_file():
    errors.append("年度統計.csv missing")
else:
    raw = out.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        errors.append("no UTF-8 BOM")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    # A short or non-numeric row is a wrong answer said plainly, not a grader crash.
    try:
        got = [(r[0].strip(), int(float(r[1].replace(",", "")))) for r in rows[1:] if r]
    except (IndexError, ValueError) as exc:
        got = f"malformed rows ({exc}): {rows[1:4]}"
    if not rows or [c.strip() for c in rows[0]] != ["類別", "總金額"]:
        errors.append(f"header {rows[:1]}")
    if got != want:
        errors.append(f"rows {got} want {want}")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
