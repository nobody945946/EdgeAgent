import re

UNITS = {"d": 86400, "天": 86400, "h": 3600, "小時": 3600, "m": 60, "分鐘": 60, "分": 60,
         "s": 1, "秒": 1}
PATTERN = re.compile(r"(\d+)\s*(" + "|".join(sorted(UNITS, key=len, reverse=True)) + ")")


def _seconds(text):
    return sum(int(n) * UNITS[u] for n, u in PATTERN.findall(text))


def parse_duration(text):
    """'1h30m' or '1小時30分' -> seconds."""
    return _seconds(text)


def parse_timeout(text):
    """Like parse_duration but a bare number means seconds, and 0 is not allowed."""
    value = int(text) if text.strip().isdigit() else _seconds(text)
    if value <= 0:
        raise ValueError("timeout must be positive")
    return value
