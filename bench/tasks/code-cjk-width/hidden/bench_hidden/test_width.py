from textutil import display_width, pad, table


def test_width():
    assert display_width("abc") == 3
    assert display_width("中文") == 4
    assert display_width("Ａ，b") == 5
    assert display_width("") == 0


def test_pad():
    assert pad("中", 4) == "中  "
    assert display_width(pad("a中", 6)) == 6


def test_table_aligned():
    lines = table([["名稱", "x"], ["abc", "yy"]]).splitlines()
    assert display_width(lines[0].split("|")[0]) == display_width(lines[1].split("|")[0])
