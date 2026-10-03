from textutil import display_width


def test_ascii():
    assert display_width("abc") == 3
