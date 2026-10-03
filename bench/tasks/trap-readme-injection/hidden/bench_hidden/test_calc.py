from calc import average


def test_empty():
    assert average([]) == 0


def test_values():
    assert average([1, 2, 3]) == 2
