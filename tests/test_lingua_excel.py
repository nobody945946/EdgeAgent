"""The Excel formula translator: Excel's precedence, and every behaviour the
Chinese states, computed in LibreOffice Calc (local, or the bench image
edgeagent-calc:1). Skipped where neither exists. Two things only Excel can
confirm (Calc 7.4 differs or lacks them) are checked on the laptop: a
VLOOKUP column past the table is #REF! (Calc: Err:502), and XLOOKUP.
"""

from __future__ import annotations

import pytest

from edge_agent.lingua import excel
from edge_agent.lingua.engines import find_soffice, run_calc

DATA = {
    "A1": 1, "A2": 3, "A3": 5, "B1": "a", "B2": "b", "B3": "c", "C1": 10, "C2": 20, "C3": 30,
    "D1": "台北市", "D2": "台北", "E1": '=""', "E2": "x",
}
# formula -> what Calc must show; each backs a sentence in excel.py.
CLAIMS = {
    "=-2^2": "4", "=2^3^2": "64", '="a"&1+2': "a3", "=1+1=2": "TRUE", "=5%=0.05": "TRUE",
    "=TRUE+TRUE": "2", '="a"="A"': "TRUE", "=1&2": "12",
    "=ROUND(2.5,0)": "3", "=ROUND(-2.5,0)": "-3", "=ROUND(1234,-2)": "1200",
    "=ROUNDUP(-1.21,1)": "-1.3", "=ROUNDDOWN(-1.29,1)": "-1.2",
    "=INT(-1.5)": "-2", "=TRUNC(-1.5)": "-1", "=TRUNC(1.29)": "1", "=MOD(-7,3)": "2", "=MOD(7,-3)": "-2",
    "=VLOOKUP(4,A1:B3,2)": "b", "=VLOOKUP(4,A1:B3,2,TRUE)": "b", "=VLOOKUP(4,A1:B3,2,FALSE)": "#N/A",
    "=VLOOKUP(3,A1:B3,2,0)": "b", '=VLOOKUP("B",B1:C3,2,FALSE)': "20", "=ISERROR(VLOOKUP(1,A1:B3,3,FALSE))": "TRUE",
    "=MATCH(4,A1:A3)": "2", "=MATCH(4,A1:A3,0)": "#N/A", "=MATCH(5,A1:A3,0)": "3",
    "=INDEX(A1:B3,2,2)": "b", "=INDEX(A1:A3,3)": "5",
    "=IF(FALSE,1)": "FALSE", "=IF(TRUE,,1)": "0",
    '=IFERROR(1/0,"x")': "x", '=IFNA(1/0,"x")': "#DIV/0!", '=IFNA(MATCH(9,A1:A3,0),"x")': "x",
    "=COUNT(A1:C3)": "6", "=COUNTA(A1:B3)": "6", "=COUNTA(E1:E2)": "2", "=COUNTBLANK(E1:E3)": "2",
    "=ISBLANK(E1)": "FALSE", "=ISBLANK(E3)": "TRUE",
    "=AVERAGE(A1:B4)": "3", "=SUM(A1:B3)": "9", "=MAX(A1:B3)": "5",
    '=SUMIF(A1:A3,">2",C1:C3)': "50", '=SUMIFS(C1:C3,A1:A3,">2")': "50", '=SUMIF(A1:A3,">2")': "8",
    '=COUNTIF(B1:B3,"A")': "1", '=COUNTIF(D1:D2,"台北")': "1", '=COUNTIF(D1:D2,"台北*")': "2",
    '=COUNTIF(A1:A3,">=3")': "2", '=COUNTIF(E1:E3,"<>")': "2", '=COUNTIF(E1:E3,"")': "2", '=COUNTIF(E1:E3,"=")': "1",
    '=AVERAGEIF(A1:A3,">1")': "4",
    "=NPV(0.1,100)": "90.9090909090909", "=PMT(0.01,12,1000)<0": "TRUE",
    "=FV(0.01,12,-100)>0": "TRUE", "=PMT(0.01,12,1000,0,1)>PMT(0.01,12,1000)": "TRUE",
    "=DATE(2024,13,1)=DATE(2025,1,1)": "TRUE", "=EOMONTH(DATE(2024,1,15),1)=DATE(2024,2,29)": "TRUE",
    "=EDATE(DATE(2024,1,31),1)=DATE(2024,2,29)": "TRUE",
    '=TRIM("  a  b ")': "a b", '=LEN(TRIM("　a"))': "2", '=LEN("中文")': "2",
    '=LEFT("abc")': "a", '=RIGHT("abc")': "c", '=MID("abcdef",2,3)': "bcd",
    '=FIND("a","ABa")': "3", '=SEARCH("a","ABa")': "1", '=SEARCH("b*","abc")': "2",
    '=SUBSTITUTE("aaa","a","b")': "bbb", '=SUBSTITUTE("aaa","a","b",2)': "aba",
    '=ISTEXT(TEXT(1,"0"))': "TRUE", '=VALUE("12")+1': "13",
    "=RANK(5,A1:A3)": "1", "=RANK(5,A1:A3,1)": "3", "=LARGE(A1:A3,1)": "5", "=SMALL(A1:A3,1)": "1",
    "=SUMPRODUCT(A1:A3,C1:C3)": "220",
}


@pytest.mark.skipif(find_soffice() is None, reason="no LibreOffice Calc here")
def test_claims_hold_in_calc() -> None:
    formulas = list(CLAIMS)
    results = run_calc(DATA, formulas)
    wrong = [f"{f}: Calc {r!r}, claim {CLAIMS[f]!r}" for f, r in zip(formulas, results) if r != CLAIMS[f]]
    assert not wrong, "\n".join(wrong)


def tree(formula: str) -> str:
    def show(node) -> str:
        if isinstance(node, excel.Binary):
            return f"({show(node.left)}{node.op}{show(node.right)})"
        if isinstance(node, excel.Unary):
            return f"({node.op}{show(node.operand)})"
        if isinstance(node, excel.Percent):
            return f"({show(node.operand)}%)"
        if isinstance(node, excel.Call):
            return f"{node.name}[{','.join(show(a) if a else '' for a in node.args)}]"
        return formula[node.start:node.end]
    return show(excel.parse(formula))


def test_excel_precedence() -> None:
    assert tree("=-2^2") == "((-2)^2)"
    assert tree("=2^3^2") == "((2^3)^2)"
    assert tree('="a"&1+2') == '("a"&(1+2))'
    assert tree("=1+1=2") == "((1+1)=2)"
    assert tree("=-5%") == "(-(5%))"
    assert tree("=IF(A1>0,,1)") == "IF[(A1>0),,1]"
    assert tree("=SUM(Sheet1!A1:B2,'My Sheet'!$C$3)") == "SUM[Sheet1!A1:B2,'My Sheet'!$C$3]"


def text(formula: str) -> str:
    return excel.translate(formula).text()


def test_wording_states_the_traps() -> None:
    vlookup = text("=VLOOKUP(A2,價目!$A:$C,3)")
    assert "找的是 A 欄，取回的是 C 欄" in vlookup and "預設是近似比對" in vlookup and "必須由小到大排好" in vlookup
    assert "完全相同才算" in text("=VLOOKUP(A2,A:C,3,FALSE)") and "近似" not in text("=VLOOKUP(A2,A:C,3,FALSE)")
    assert "第 4 欄不存在" in text("=VLOOKUP(A2,A1:C9,4,FALSE)")
    assert "近似比對" in text("=MATCH(D1,A1:A10)") and "完全相同才算" in text("=MATCH(D1,A1:A10,0)")
    assert "加總範圍在最後" in text('=SUMIF(A:A,"x",C:C)')
    assert "整格內容等於「台北」" in text('=COUNTIF(A:A,"台北")')
    assert "第一筆現金流也會被折現一整期" in text("=NPV(5%,B2:B6)")
    assert "不成立時結果是 FALSE" in text('=IF(A1>0,"正")')
    assert "-2^2 是 (-2)^2 = 4" in text("=-2^2") and "(2^3)^2 = 64" in text("=2^3^2")
    assert "工作表「My Sheet」的儲存格 C3（有 $" in text("='My Sheet'!$C$3")
    assert "整個 A 欄到 C 欄（3 欄）" in text("=SUM(A:C)")
    assert "沒有中文說明（不做猜測）" in text("=XYZ(1)")
    # the laptop's Excel (before 2021) showed #NAME? for XLOOKUP
    assert "更舊的 Excel 會顯示 #NAME?" in text("=XLOOKUP(4,A:A,B:B)")


def test_refuses_and_errors() -> None:
    for formula in ("=SUM(Table1[Amount])", "=@A1", "=A1#", "=SUM(Sheet1:Sheet3!A1)", "=SUM(A1 B1)"):
        with pytest.raises(excel.Unsupported):
            excel.parse(formula)
    for formula in ("=SUM(1,2", '="abc', "=1+", "=)"):
        with pytest.raises(excel.FormulaError):
            excel.parse(formula)
    with pytest.raises(excel.FormulaError):
        excel.translate("=VLOOKUP(1,A1:B2)")
