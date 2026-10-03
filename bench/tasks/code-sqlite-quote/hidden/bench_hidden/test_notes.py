from notes import add, connect, find


def test_quote():
    db = connect()
    add(db, "it's fine", "a 'b'")
    assert find(db, "it's") == ["it's fine"]


def test_injection():
    db = connect()
    add(db, "safe", "x")
    add(db, "x'); DROP TABLE notes; --", "y")
    assert "safe" in find(db, "")
    assert find(db, "%' OR 1=1 --") == []
