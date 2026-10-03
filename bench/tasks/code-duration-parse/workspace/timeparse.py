import re


def parse_duration(text):
    """'1h30m' -> 5400 seconds. Units: d h m s."""
    total = 0
    for amount, unit in re.findall(r"(\d+)([dhms])", text):
        total += int(amount) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_timeout(text):
    """Like parse_duration but a bare number means seconds, and 0 is not allowed."""
    if text.strip().isdigit():
        value = int(text)
    else:
        value = 0
        for amount, unit in re.findall(r"(\d+)([dhms])", text):
            value += int(amount) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    if value <= 0:
        raise ValueError("timeout must be positive")
    return value
