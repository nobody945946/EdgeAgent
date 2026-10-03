import csv
from datetime import datetime


def monthly_totals(path):
    """Sum 金額 per month. Returns [("2026-09", total), ...]."""
    totals = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            day = datetime.strptime(row["日期"], "%Y-%m-%d")
            key = day.strftime("%Y-%m")
            totals[key] = totals.get(key, 0) + int(row["金額"])
    return list(totals.items())
