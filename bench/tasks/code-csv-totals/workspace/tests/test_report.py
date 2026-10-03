from report import monthly_totals


def test_simple(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("日期,金額,類別\n2026-09-01,100,餐\n2026-09-02,50,餐\n", encoding="utf-8")
    assert monthly_totals(p) == [("2026-09", 150)]
