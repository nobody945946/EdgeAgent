from parser import parse_kv


def test_equals_in_value():
    assert parse_kv("url=a=b; x = 1") == {"url": "a=b", "x": "1"}


def test_basic():
    assert parse_kv("a=1;b=2;") == {"a": "1", "b": "2"}
