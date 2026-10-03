import csv
import re
from datetime import date


def monthly_totals(path):
    """Sum 金額 per month. Returns [("2026-09", total), ...]."""
    totals = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            y, m, d = (int(x) for x in re.split(r"[-/]", row["日期"].strip()))
            key = date(y, m, d).strftime("%Y-%m")
            totals[key] = totals.get(key, 0) + int(row["金額"].replace(",", ""))
    return sorted(totals.items())
