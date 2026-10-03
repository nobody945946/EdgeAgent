from report import monthly_totals


def test_mixed(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        '日期,金額,類別\n2026/9/3,"1,234",餐\n2026-08-31,100,交通\n'
        '2026/10/1,"2,000",房租\n2026-09-15,6,餐\n', encoding="utf-8")
    assert monthly_totals(p) == [("2026-08", 100), ("2026-09", 1240), ("2026-10", 2000)]


def test_plain_still_works(tmp_path):
    p = tmp_path / "b.csv"
    p.write_text("日期,金額,類別\n2026-01-05,7,x\n", encoding="utf-8")
    assert monthly_totals(p) == [("2026-01", 7)]
