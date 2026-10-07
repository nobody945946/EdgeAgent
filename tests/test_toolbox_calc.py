"""edge_agent.toolbox.calc: exact calculation and date arithmetic."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from edge_agent.toolbox import ToolError
from tests.toolbox_helpers import run


@pytest.mark.parametrize(("expression", "phrase"), [
    ("0.1 + 0.2", "結果：0.3（精確值）"),
    ("1,234,567 * 2", "結果：2,469,134（精確值）"),
    ("15% * 3200", "結果：480（精確值）"),
    ("10 % 3", "結果：1（精確值）"),
    ("2^10", "結果：1024（精確值）"),
    ("1/3", "結果：1/3（分數，精確值）≈ 0.3333333333"),
    ("sqrt(16/9)", "4/3"),
    ("sqrt(2)", "結果 ≈ 1.414213562"),
    ("max(1,234)", "結果：234"),
    ("round(2.675, 2)", "結果：2.68"),
    ("３×４", "結果：12"),
    ("(1,200 + 350) * 1.05", "結果：1627.5（精確值）"),
])
def test_calculate(expression: str, phrase: str) -> None:
    out = run("calculate", Path.cwd(), expression=expression)
    assert phrase in out and out.startswith("算式（解讀為）："), out


@pytest.mark.parametrize(("expression", "reason"), [
    ("__import__('os').system('x')", "只能算數學算式"), ("1/0", "除以零"), ("2**100000", "位數太多"),
    ("open('f')", "只能算數學算式"), ("2(3+4)", "乘法要寫"), ("a + 1", "只能算數學算式"), ("sqrt(-1)", "負數"),
])
def test_calculate_refuses(expression: str, reason: str) -> None:
    with pytest.raises(ToolError, match=reason):
        run("calculate", Path.cwd(), expression=expression)


def test_date_calc() -> None:
    here = Path.cwd()
    assert "相差 278 天（39 週又 5 天）" in run("date_calc", here, op="diff", date="2026-01-01", other="2026-10-06")
    assert "2024-02-29（星期四）" in run("date_calc", here, op="add", date="2024-03-01", days=-1)
    soon = date.today() + timedelta(days=100)
    assert soon.isoformat() in run("date_calc", here, op="add", date="今天", days="100")
    assert run("date_calc", here, op="weekday", date="民國115年10月6日") == "2026-10-06 是星期二。"
    assert "用今天" in run("date_calc", here, op="diff", date="2000-01-01")
    with pytest.raises(ToolError, match="沒有「2026-02-30」這一天"):
        run("date_calc", here, op="weekday", date="2026-02-30")
    with pytest.raises(ToolError, match="要給 days"):
        run("date_calc", here, op="add", date="2026-01-01")
