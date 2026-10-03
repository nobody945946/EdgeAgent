import pytest
from timeparse import parse_duration, parse_timeout


@pytest.mark.parametrize("text,seconds", [
    ("1h30m", 5400), ("1小時30分", 5400), ("45秒", 45), ("2天", 172800),
    ("1天2小時", 93600), ("1h 30分", 5400), ("90s", 90), ("3分鐘", 180)])
def test_duration(text, seconds):
    assert parse_duration(text) == seconds


def test_timeout():
    assert parse_timeout("30") == 30
    assert parse_timeout("2分") == 120
    with pytest.raises(ValueError):
        parse_timeout("0")
