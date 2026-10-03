import unicodedata


def display_width(text: str) -> int:
    """Columns the text occupies in a terminal."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    """Left-align text in a column of the given display width."""
    return text + " " * (width - display_width(text))


def table(rows: list[list[str]]) -> str:
    widths = [max(display_width(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(" | ".join(pad(cell, w) for cell, w in zip(row, widths)) for row in rows)
