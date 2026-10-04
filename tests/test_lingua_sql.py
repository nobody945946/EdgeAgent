"""The SQL translator: every behaviour its Chinese states, run on SQLite and,
where docker has the images, PostgreSQL 16 and SQL Server 2022 (containers
with no network, removed afterwards).
"""

from __future__ import annotations

import pytest

from edge_agent.lingua import sql
from edge_agent.lingua.engines import Databases

T = "CREATE TABLE t (v int); INSERT INTO t VALUES (1); INSERT INTO t VALUES (NULL); INSERT INTO t VALUES (2); "
AB = ("CREATE TABLE a (id int); INSERT INTO a VALUES (1); INSERT INTO a VALUES (2); "
      "CREATE TABLE b (aid int, x int); INSERT INTO b VALUES (1, 10); ")
BOOL = "SELECT CASE WHEN {} THEN 1 ELSE 0 END"

# (sql, {dialect: rows}); "*" means every database. Each backs a sentence in sql.py.
CLAIMS = [
    ("SELECT 5/2", {"*": [["2"]]}),
    (BOOL.format("'ABC' LIKE 'abc'"), {"sqlite": [["1"]], "postgres": [["0"]], "tsql": [["1"]]}),
    (BOOL.format("'a' = 'A'"), {"sqlite": [["0"]], "postgres": [["0"]], "tsql": [["1"]]}),
    (BOOL.format("'a' = 'a '"), {"tsql": [["1"]], "sqlite": [["0"]], "postgres": [["0"]]}),
    (T + "SELECT v FROM t ORDER BY v", {"sqlite": [[""], ["1"], ["2"]], "postgres": [["1"], ["2"], [""]],
                                        "tsql": [[""], ["1"], ["2"]]}),
    (T + "SELECT v FROM t ORDER BY v DESC", {"sqlite": [["2"], ["1"], [""]], "postgres": [[""], ["2"], ["1"]],
                                             "tsql": [["2"], ["1"], [""]]}),
    (T + "SELECT COUNT(*) FROM t WHERE v = NULL", {"*": [["0"]]}),
    (T + "SELECT COUNT(*) FROM t WHERE v NOT IN (5, NULL)", {"*": [["0"]]}),
    (T + "SELECT COUNT(*) FROM t WHERE v NOT IN (SELECT v FROM t WHERE v IS NULL OR v = 2)", {"*": [["0"]]}),
    (T + "SELECT COUNT(*) FROM t WHERE NOT (v = 5)", {"*": [["2"]]}),
    (T + "SELECT COUNT(*) FROM t WHERE v BETWEEN 1 AND 2", {"*": [["2"]]}),
    (T + "SELECT COUNT(*), COUNT(v) FROM t", {"*": [["3", "2"]]}),
    (T + "SELECT AVG(v) FROM t", {"sqlite": [["1.5"]], "postgres": [["1.5000000000000000"]], "tsql": [["1"]]}),
    (T + "SELECT SUM(v) FROM t WHERE v IS NULL", {"*": [[""]]}),
    ("SELECT CASE WHEN 1 = 0 THEN 1 END", {"*": [[""]]}),
    ("SELECT COALESCE(NULL, 2, 3)", {"*": [["2"]]}),
    (BOOL.format("1 + NULL IS NULL"), {"*": [["1"]]}),
    (BOOL.format("'a' || NULL IS NULL"), {"sqlite": [["1"]], "postgres": [["1"]]}),
    (BOOL.format("'abc' LIKE 'a_c' AND 'ac' LIKE 'a%c' AND NOT 'abc' LIKE 'a_'"), {"*": [["1"]]}),
    (BOOL.format("'ABC' ILIKE 'abc'"), {"postgres": [["1"]]}),
    (AB + "SELECT COUNT(*) FROM a LEFT JOIN b ON b.aid = a.id", {"*": [["2"]]}),
    (AB + "SELECT COUNT(*) FROM a LEFT JOIN b ON b.aid = a.id WHERE b.x > 0", {"*": [["1"]]}),
    (AB + "SELECT COUNT(*) FROM a LEFT JOIN b ON b.aid = a.id WHERE b.x IS NULL", {"*": [["1"]]}),
    (AB + "INSERT INTO b VALUES (1, 20); SELECT COUNT(*) FROM a LEFT JOIN b ON b.aid = a.id", {"*": [["3"]]}),
    (AB + "SELECT COUNT(*) FROM a INNER JOIN b ON b.aid = a.id", {"*": [["1"]]}),
    (AB + "SELECT COUNT(*) FROM a CROSS JOIN b", {"*": [["2"]]}),
    ("SELECT 1 UNION SELECT 1", {"*": [["1"]]}),
    ("SELECT 1 UNION ALL SELECT 1", {"*": [["1"], ["1"]]}),
    (T + "DELETE FROM t; SELECT COUNT(*) FROM t", {"*": [["0"]]}),
    (T + "TRUNCATE TABLE t; SELECT COUNT(*) FROM t", {"postgres": [["0"]], "tsql": [["0"]]}),
    (T + "UPDATE t SET v = 7; SELECT COUNT(*) FROM t WHERE v = 7", {"*": [["3"]]}),
]
ERRORS = [  # (sql, databases that must refuse it, databases that must accept it)
    ("CREATE TABLE g (k int, v int); INSERT INTO g VALUES (1, 2); SELECT k, v FROM g GROUP BY k",
     ("postgres", "tsql"), ("sqlite",)),
]


@pytest.fixture(scope="module")
def db():
    with Databases() as databases:
        yield databases


def test_claims_hold_in_each_database(db) -> None:
    wrong = []
    for statement, expected in CLAIMS:
        for dialect in db.available():
            want = expected.get(dialect, expected.get("*"))
            if want is None:
                continue
            try:
                got = db.run(dialect, statement)
            except RuntimeError as exc:
                got = f"error: {exc}"
            if got != want:
                wrong.append(f"{dialect}: {statement[-70:]} -> {got}, claim {want}")
    assert not wrong, "\n".join(wrong)
    assert len(db.available()) == 3 or pytest.skip("only " + ", ".join(db.available()))


def test_errors_where_said(db) -> None:
    for statement, refuse, accept in ERRORS:
        for dialect in db.available():
            if dialect in refuse:
                with pytest.raises(RuntimeError):
                    db.run(dialect, statement)
            elif dialect in accept:
                db.run(dialect, statement)


def text(statement: str, dialect: str = "") -> str:
    return sql.translate(statement, dialect).text()


def test_wording_per_database() -> None:
    assert "區分大小寫（不分大小寫要用 ILIKE）" in text("SELECT * FROM t WHERE a LIKE 'x%'", "postgres")
    assert "英文字母不分大小寫" in text("SELECT * FROM t WHERE a LIKE 'x%'", "sqlite")
    assert "預設定序不分大小寫" in text("SELECT * FROM t WHERE a LIKE 'x%'", "tsql")
    assert "結尾空格也不算" in text("SELECT * FROM t WHERE a = 'x'", "tsql")
    assert "NULL 排在最後面" in text("SELECT * FROM t ORDER BY a", "postgres")
    assert "依資料庫而定" in text("SELECT * FROM t ORDER BY a")
    assert "SQL Server 會直接出錯" in text("SELECT k, v FROM g GROUP BY k", "tsql")
    assert "5/2 是 2" in text("SELECT a / b FROM t", "sqlite")
    assert "整數欄位的平均在 SQL Server 會捨去小數" in text("SELECT AVG(v) FROM t", "tsql")


def test_traps_are_said() -> None:
    joined = text("SELECT * FROM a LEFT JOIN b ON b.aid = a.id WHERE b.x > 0", "sqlite")
    assert "效果和 INNER JOIN 一樣" in joined
    assert "效果和 INNER JOIN" not in text("SELECT * FROM a LEFT JOIN b ON b.aid = a.id WHERE b.x IS NULL", "sqlite")
    assert "要寫 IS NULL" in text("SELECT * FROM t WHERE v = NULL")
    assert "一列都不會成立" in text("SELECT * FROM t WHERE v NOT IN (SELECT w FROM u)")
    assert "NOT IN 永遠不會成立" in text("SELECT * FROM t WHERE v NOT IN (1, NULL)")
    deleted = text("DELETE FROM orders", "sqlite")
    assert "刪除資料表 orders 的每一列" in deleted and "會刪除資料：orders 的每一列" in deleted
    assert "表裡每一列都會被修改" in text("UPDATE t SET a = 1")
    assert "整個資料表 t" in text("DROP TABLE t")
    top = text("SELECT TOP 5 * FROM t", "tsql")
    assert "`TOP 5`：只取前 5 列（沒有 ORDER BY，取到哪幾列不保證）" in top
    assert "NULL 不算" in text("SELECT COUNT(v) FROM t") and "包含有 NULL 的列" in text("SELECT COUNT(*) FROM t")
    assert "兩端都包含" in text("SELECT * FROM t WHERE v BETWEEN 1 AND 2")
    assert "沒有寫 ELSE" in text("SELECT CASE WHEN a = 1 THEN 2 END FROM t")


def test_not_readable_is_not_called_invalid() -> None:
    with pytest.raises(sql.Untranslatable) as caught:
        sql.translate("SELECT (", "sqlite")
    assert "不代表資料庫一定會拒絕" in str(caught.value)
