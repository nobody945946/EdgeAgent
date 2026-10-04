"""SQL to plain Traditional Chinese, exactly.

sqlglot reads the statement; the wording is fixed here. A SELECT is told in
the order the database works it out (FROM and JOIN, WHERE, GROUP BY, HAVING,
the output columns, DISTINCT, ORDER BY, the row limit), which is not the
order it is written in. The traps are said where they apply: = NULL is never
true, NOT IN over a NULL keeps nothing, a WHERE on the right table of a LEFT
JOIN drops its unmatched rows, UPDATE or DELETE without WHERE touches every
row, COUNT(column) skips NULL.

What differs between databases (integer division, LIKE and = on letter
case, where NULL sorts, bare columns in GROUP BY, ROUND) is said for the
database named, and only for the ones checked against the real engine in
tests/test_lingua_sql.py: SQLite, PostgreSQL and SQL Server. For any other
or an unnamed one the sentence says it depends, with what each checked one
does.

sqlglot is not the database: a statement it cannot read is reported as
not translatable, never as invalid SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

DIALECTS = {"sqlite": "SQLite", "postgres": "PostgreSQL", "tsql": "SQL Server"}
READ = {"sqlite": "sqlite", "postgres": "postgres", "tsql": "tsql", "": None}

# Behaviour that differs, per checked database (tests/test_lingua_sql.py).
VARIES = {
    "int_division": {
        "sqlite": "兩個整數相除會捨去小數（5/2 是 2）",
        "postgres": "兩個整數相除會捨去小數（5/2 是 2）",
        "tsql": "兩個整數相除會捨去小數（5/2 是 2）",
    },
    "like_case": {
        "sqlite": "英文字母不分大小寫",
        "postgres": "區分大小寫（不分大小寫要用 ILIKE）",
        "tsql": "大小寫依欄位的定序；預設定序不分大小寫",
    },
    "equal_case": {
        "sqlite": "文字比較區分大小寫（'a' = 'A' 不成立）",
        "postgres": "文字比較區分大小寫（'a' = 'A' 不成立）",
        "tsql": "文字比較依定序；預設定序不分大小寫（'a' = 'A' 成立），結尾空格也不算（'a' = 'a ' 成立）",
    },
    "nulls_order": {
        "sqlite": "由小到大時 NULL 排在最前面，由大到小時排在最後面",
        "postgres": "由小到大時 NULL 排在最後面，由大到小時排在最前面",
        "tsql": "由小到大時 NULL 排在最前面，由大到小時排在最後面",
    },
    "bare_group_column": {
        "sqlite": "SQLite 允許，但這一欄的值是組裡某一列的值，是哪一列不保證",
        "postgres": "PostgreSQL 會直接出錯",
        "tsql": "SQL Server 會直接出錯",
    },
}


def varies(key: str, dialect: str) -> str:
    table = VARIES[key]
    if dialect in table:
        return table[dialect]
    return "依資料庫而定：" + "；".join(f"{DIALECTS[d]} {text}" for d, text in table.items())


class Untranslatable(ValueError):
    """sqlglot could not read it; that does not make it invalid SQL."""


@dataclass
class Line:
    depth: int
    source: str
    text: str


@dataclass
class Translation:
    sql: str
    dialect: str
    lines: list[Line]
    effects: list[str]
    unknown: list[str]

    def text(self) -> str:
        head = [f"SQL：{self.sql.strip()}",
                "資料庫：" + (DIALECTS.get(self.dialect) or "沒有指定（各資料庫不同的地方會分別列出）")]
        body, counters = [], [0] * 64
        for line in self.lines:
            counters[line.depth] += 1
            for deeper in range(line.depth + 1, len(counters)):
                counters[deeper] = 0
            number = ".".join(str(n) for n in counters[:line.depth + 1])
            source = f"`{line.source}`：" if line.source else ""
            body.append(f"{'  ' * line.depth}{number}. {source}{line.text}")
        tail = ["影響：" + ("；".join(self.effects) if self.effects else "只讀取資料，不改變任何東西")]
        if self.unknown:
            tail.append("沒有中文說明的部分（原樣列出，不做猜測）：" + "、".join(self.unknown))
        return "\n".join(head + ["逐項說明："] + body + tail)


class Writer:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self.lines: list[Line] = []
        self.effects: list[str] = []
        self.unknown: list[str] = []

    def sql(self, node: exp.Expression) -> str:
        return node.sql(dialect=READ.get(self.dialect) or None)

    def add(self, depth: int, node: exp.Expression | None, text: str) -> None:
        self.lines.append(Line(depth, self.sql(node) if node is not None else "", text))

    # -------------------------------------------------------- statements

    def statement(self, node: exp.Expression, depth: int) -> None:
        if isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            self.query(node, depth)
        elif isinstance(node, exp.Insert):
            self.insert(node, depth)
        elif isinstance(node, exp.Update):
            self.update(node, depth)
        elif isinstance(node, exp.Delete):
            self.delete(node, depth)
        elif isinstance(node, exp.Drop):
            kind = str(node.args.get("kind") or "").upper()
            what = {"TABLE": "資料表", "VIEW": "檢視", "INDEX": "索引"}.get(kind, kind)
            targets = node.args.get("tables") or ([node.this] if node.this is not None else [])
            name = "、".join(self.sql(t) for t in targets)
            exists = "（不存在時不出錯）" if node.args.get("exists") else ""
            text = f"刪除整個{what} {name}{exists}"
            if kind == "TABLE":
                text += "：表的結構和裡面所有資料都會消失"
            self.add(depth, node, text)
            self.effects.append(f"會刪除{what}：{name}")
        elif isinstance(node, exp.TruncateTable):
            names = "、".join(self.sql(t) for t in node.expressions)
            self.add(depth, node, f"清空資料表 {names} 的所有資料（表本身保留）")
            self.effects.append(f"會刪除資料：{names} 的每一列")
        elif isinstance(node, exp.Create):
            kind = str(node.args.get("kind") or "").upper()
            what = {"TABLE": "資料表", "VIEW": "檢視", "INDEX": "索引"}.get(kind, kind)
            target = node.this.this if isinstance(node.this, exp.Schema) else node.this
            self.add(depth, node, f"建立{what} {self.sql(target)}")
            if isinstance(node.this, exp.Schema):
                for column in node.this.expressions:
                    self.add(depth + 1, column, "欄位定義")
            if node.expression is not None:
                self.add(depth + 1, None, "內容來自這個查詢：")
                self.statement(node.expression, depth + 2)
            self.effects.append(f"會建立{what}：{self.sql(target)}")
        else:
            self.add(depth, node, f"{type(node).__name__} 敘述（沒有中文說明）")
            self.unknown.append(type(node).__name__)

    def query(self, node: exp.Expression, depth: int) -> None:
        with_ = node.args.get("with_")
        if with_ is not None:
            for cte in with_.expressions:
                self.add(depth, cte, f"先算出暫時的結果「{cte.alias}」（只在這個敘述裡有效）：")
                self.query(cte.this, depth + 1)
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            distinct = node.args.get("distinct")
            if isinstance(node, exp.Union):
                text = ("把兩個查詢的結果上下接在一起，再去掉完全重複的列（UNION）" if distinct
                        else "把兩個查詢的結果上下接在一起，重複的列也保留（UNION ALL）")
            elif isinstance(node, exp.Intersect):
                text = "只留下兩個查詢都有的列（INTERSECT）"
            else:
                text = "第一個查詢的結果，去掉第二個查詢也有的列（EXCEPT）"
            self.add(depth, None, text + "：")
            self.add(depth + 1, None, "第一個查詢：")
            self.query(node.this, depth + 2)
            self.add(depth + 1, None, "第二個查詢：")
            self.query(node.expression, depth + 2)
            self.order_limit(node, depth)
            return
        if isinstance(node, exp.Subquery):
            self.query(node.this, depth)
            return
        if not isinstance(node, exp.Select):
            self.statement(node, depth)
            return
        self.select(node, depth)

    def select(self, node: exp.Select, depth: int) -> None:
        self.add(depth, None, "查詢（以下依資料庫實際處理的順序說明）：")
        d = depth + 1
        source = node.args.get("from_")
        left_tables: list[str] = []
        right_tables: list[str] = []
        if source is not None:
            self.add(d, source, f"從{self.table_text(source.this)} 開始")
            left_tables.append(alias_of(source.this))
        for join in node.args.get("joins") or []:
            self.join(join, d)
            if str(join.args.get("side") or "").upper() == "LEFT":
                right_tables.append(alias_of(join.this))
        where = node.args.get("where")
        if where is not None:
            self.add(d, where, "只留下符合這個條件的列（WHERE）：")
            self.condition(where.this, d + 1)
            hit = [t for t in right_tables if uses_table(where.this, t)]
            if hit:
                self.add(d + 1, None, f"⚠ WHERE 用到了 LEFT JOIN 右邊 {'、'.join(hit)} 的欄位："
                                      f"{'、'.join(hit)} 沒有對應的列，這些欄位是 NULL，條件不會成立而被去掉，"
                                      "效果和 INNER JOIN 一樣（除非條件是 IS NULL）")
        group = node.args.get("group")
        if group is not None:
            keys = "、".join(self.sql(g) for g in group.expressions)
            self.add(d, group, f"依 {keys} 分組：值相同的列合成一組，每一組在結果裡只剩一列（GROUP BY）")
            bare = [self.sql(e) for e in node.expressions if is_bare(e, group.expressions)]
            if bare:
                self.add(d + 1, None, f"⚠ 輸出欄位 {'、'.join(bare)} 不在分組依據裡、也不是彙總：{varies('bare_group_column', self.dialect)}")
        having = node.args.get("having")
        if having is not None:
            self.add(d, having, "分組之後，只留下符合這個條件的組（HAVING）：")
            self.condition(having.this, d + 1)
        self.add(d, None, "輸出這些欄位：" if node.expressions else "輸出：")
        for expression in node.expressions:
            self.output(expression, d + 1, grouped=group is not None)
        if node.args.get("distinct") is not None:
            self.add(d, None, "去掉完全重複的列（DISTINCT）")
        self.order_limit(node, d)

    def order_limit(self, node: exp.Expression, depth: int) -> None:
        order = node.args.get("order")
        if order is not None:
            parts = []
            for ordered in order.expressions:
                way = "由大到小（DESC）" if ordered.args.get("desc") else "由小到大"
                parts.append(f"{self.sql(ordered.this)} {way}")
            self.add(depth, order, "排序：" + "，相同時再依 ".join(parts) + "；NULL 的位置："
                     + varies("nulls_order", self.dialect))
        limit = node.args.get("limit")
        offset = node.args.get("offset")
        if offset is not None:
            self.add(depth, offset, f"跳過前 {self.sql(offset.expression)} 列")
        if limit is not None:
            count = limit.expression if limit.expression is not None else limit.this
            text = f"只取前 {self.sql(count)} 列"
            if order is None:
                text += "（沒有 ORDER BY，取到哪幾列不保證）"
            # SQL Server writes it TOP n; sqlglot keeps it as a limit.
            label = f"TOP {self.sql(count)}" if self.dialect == "tsql" else self.sql(limit)
            self.lines.append(Line(depth, label, text))

    def join(self, join: exp.Join, depth: int) -> None:
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        table = self.table_text(join.this)
        if kind == "CROSS" or (not side and join.args.get("on") is None and not join.args.get("using")):
            text = f"和{table}做所有組合：每一列都配上對方的每一列（列數相乘）"
        elif side == "LEFT":
            text = (f"接上{table}（LEFT JOIN）：左邊每一列都保留；對得上的接上對應的列，"
                    "一列對到多列就變成多列；對不上的，右邊的欄位都是 NULL")
        elif side == "RIGHT":
            text = (f"接上{table}（RIGHT JOIN）：右邊每一列都保留；左邊對不上的，左邊的欄位都是 NULL")
        elif side == "FULL":
            text = f"接上{table}（FULL JOIN）：兩邊的列都保留；對不上的那一邊欄位是 NULL"
        else:
            text = f"接上{table}（INNER JOIN）：只留下兩邊對得上的列；一列對到多列就變成多列"
        self.add(depth, join, text)
        if join.args.get("on") is not None:
            self.add(depth + 1, None, "對得上的條件（ON）：")
            self.condition(join.args["on"], depth + 2)
        if join.args.get("using"):
            names = "、".join(self.sql(u) for u in join.args["using"])
            self.add(depth + 1, None, f"對得上的條件：兩邊的 {names} 相等（USING）")

    def table_text(self, node: exp.Expression) -> str:
        if isinstance(node, exp.Table):
            alias = f"（簡稱 {node.alias}）" if node.alias else ""
            return f"資料表 {self.sql(exp.Table(this=node.this, db=node.args.get('db'), catalog=node.args.get('catalog')))}{alias}"
        if isinstance(node, exp.Subquery):
            return f"子查詢的結果「{node.alias or '（沒有名稱）'}」"
        return self.sql(node)

    def output(self, node: exp.Expression, depth: int, grouped: bool) -> None:
        name = ""
        if isinstance(node, exp.Alias):
            name = f"，取名為 {node.alias}"
            node = node.this
        if isinstance(node, exp.Star):
            self.add(depth, node, "所有欄位" + name)
            return
        text = self.value(node, depth, grouped)
        if text is not None:
            self.add(depth, node, text + name)

    # -------------------------------------------------------- expressions

    def value(self, node: exp.Expression, depth: int, grouped: bool = False) -> str | None:
        """One-line meaning of a value, or None when it wrote its own lines."""
        if isinstance(node, exp.Column):
            return f"資料表 {node.table} 的欄位 {node.name}" if node.table else f"欄位 {node.name}"
        if isinstance(node, exp.Literal):
            return f"文字「{node.this}」" if node.is_string else f"數值 {node.this}"
        if isinstance(node, exp.Null):
            return "NULL（沒有值）"
        if isinstance(node, exp.Boolean):
            return "TRUE" if node.this else "FALSE"
        if isinstance(node, exp.Count):
            arg = node.this
            if isinstance(arg, exp.Star):
                return "數一數有幾列（包含有 NULL 的列）" + ("，每一組各自算" if grouped else "")
            if isinstance(arg, exp.Distinct):
                return f"數一數 {', '.join(self.sql(e) for e in arg.expressions)} 有幾種不同的值（NULL 不算）"
            return f"數一數 {self.sql(arg)} 不是 NULL 的有幾列（NULL 不算）"
        aggregates = {exp.Sum: "總和", exp.Avg: "平均", exp.Max: "最大值", exp.Min: "最小值"}
        for kind, word in aggregates.items():
            if isinstance(node, kind):
                each = "每一組各自算，" if grouped else ""
                extra = ""
                if isinstance(node, exp.Avg):
                    extra = "；分母只算不是 NULL 的列"
                    if self.dialect in ("sqlite", "postgres", "tsql"):
                        extra += "" if self.dialect != "tsql" else "；整數欄位的平均在 SQL Server 會捨去小數"
                return f"{self.sql(node.this)} 的{word}（{each}NULL 不算；全部都是 NULL 時結果是 NULL{extra}）"
        if isinstance(node, exp.Coalesce):
            items = [self.sql(node.this)] + [self.sql(e) for e in node.expressions]
            return f"依序看 {'、'.join(items)}，取第一個不是 NULL 的值"
        if isinstance(node, exp.Case):
            self.case(node, depth)
            return None
        if isinstance(node, exp.Cast):
            return f"把 {self.sql(node.this)} 轉成 {self.sql(node.to)} 型別"
        if isinstance(node, exp.Div):
            return f"{self.sql(node.this)} 除以 {self.sql(node.expression)}（{varies('int_division', self.dialect)}）"
        arithmetic = {exp.Add: "加", exp.Sub: "減", exp.Mul: "乘", exp.Mod: "除以後的餘數"}
        for kind, word in arithmetic.items():
            if isinstance(node, kind):
                null = "；任何一邊是 NULL，結果就是 NULL"
                if isinstance(node, exp.Mod):
                    return f"{self.sql(node.this)} 除以 {self.sql(node.expression)} 的餘數{null}"
                return f"{self.sql(node.this)} {word} {self.sql(node.expression)}{null}"
        if isinstance(node, exp.DPipe):
            return f"把 {self.sql(node.this)} 和 {self.sql(node.expression)} 接成一段文字（任何一邊是 NULL，結果就是 NULL）"
        if isinstance(node, exp.Window):
            parts = []
            if node.args.get("partition_by"):
                parts.append("依 " + "、".join(self.sql(p) for p in node.args["partition_by"]) + " 分區")
            if node.args.get("order") is not None:
                parts.append("區內依 " + self.sql(node.args["order"]).removeprefix("ORDER BY ") + " 排序")
            func = self.value(node.this, depth) or self.sql(node.this)
            return f"視窗計算：{'，'.join(parts) or '整個結果一起'}，再算「{func}」；每一列都保留，不會合併成一列"
        if isinstance(node, (exp.Subquery, exp.Exists)):
            self.add(depth, node, "子查詢" + ("：有沒有任何一列（EXISTS）" if isinstance(node, exp.Exists) else "") + "：")
            self.query(node.this, depth + 1)
            return None
        if isinstance(node, exp.Paren):
            return self.value(node.this, depth, grouped)
        if isinstance(node, (exp.Predicate, exp.Connector, exp.Not)):
            self.condition(node, depth)
            return None
        if isinstance(node, exp.Func):
            name = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
            if isinstance(node, exp.Anonymous):
                name = str(node.this).upper()
            self.unknown.append(name)
            return f"函數 {name}（沒有中文說明，不做猜測）"
        self.unknown.append(type(node).__name__)
        return f"（{type(node).__name__}，沒有中文說明）"

    def case(self, node: exp.Case, depth: int) -> None:
        self.add(depth, node, "依條件選值（CASE）：從上到下，第一個成立的條件決定結果：")
        for branch in node.args.get("ifs") or []:
            condition = self.sql(branch.this)
            result = self.value(branch.args["true"], depth + 2) or self.sql(branch.args["true"])
            self.add(depth + 1, None, f"如果 {condition}：{result}")
        default = node.args.get("default")
        if default is not None:
            self.add(depth + 1, default, f"都不成立時：{self.value(default, depth + 2) or self.sql(default)}")
        else:
            self.add(depth + 1, None, "都不成立時：NULL（沒有寫 ELSE）")

    def condition(self, node: exp.Expression, depth: int) -> None:
        if isinstance(node, exp.Paren):
            self.condition(node.this, depth)
            return
        if isinstance(node, exp.And):
            self.add(depth, node, "以下全部成立（AND）：")
            for part in flatten(node, exp.And):
                self.condition(part, depth + 1)
            return
        if isinstance(node, exp.Or):
            self.add(depth, node, "以下任一個成立（OR）：")
            for part in flatten(node, exp.Or):
                self.condition(part, depth + 1)
            return
        if isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, exp.In):
                self.in_list(inner, depth, negated=True)
                return
            if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                self.add(depth, node, f"{self.sql(inner.this)} 不是 NULL（有值）")
                return
            self.add(depth, node, "以下不成立（NOT）；裡面的結果是 NULL 時，NOT 之後仍然是 NULL，列一樣不會留下：")
            self.condition(inner, depth + 1)
            return
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            self.add(depth, node, f"{self.sql(node.this)} 是 NULL（沒有值）")
            return
        if isinstance(node, exp.In):
            self.in_list(node, depth, negated=False)
            return
        if isinstance(node, exp.Between):
            self.add(depth, node, f"{self.sql(node.this)} 介於 {self.sql(node.args['low'])} 和 "
                                  f"{self.sql(node.args['high'])} 之間（兩端都包含）")
            return
        if isinstance(node, (exp.Like, exp.ILike)):
            case = "不分大小寫（ILIKE）" if isinstance(node, exp.ILike) else varies("like_case", self.dialect)
            self.add(depth, node, f"{self.sql(node.this)} 符合樣式 {self.sql(node.expression)}："
                                  f"% 代表任意多個字（包含 0 個），_ 代表剛好一個字；{case}")
            return
        comparisons = {exp.EQ: "等於", exp.NEQ: "不等於", exp.GT: "大於", exp.GTE: "大於或等於",
                       exp.LT: "小於", exp.LTE: "小於或等於"}
        for kind, word in comparisons.items():
            if isinstance(node, kind):
                text = f"{self.sql(node.this)} {word} {self.sql(node.expression)}"
                if isinstance(node.expression, exp.Null) or isinstance(node.this, exp.Null):
                    text += "；⚠ 和 NULL 比較永遠不會成立（結果是 NULL），要寫 IS NULL／IS NOT NULL"
                elif any(isinstance(side, exp.Literal) and side.is_string for side in (node.this, node.expression)):
                    text += "；" + varies("equal_case", self.dialect)
                else:
                    text += "；任何一邊是 NULL 時不成立"
                self.add(depth, node, text)
                return
        if isinstance(node, exp.Exists):
            self.add(depth, node, "下面的子查詢至少有一列（EXISTS）：")
            self.query(node.this, depth + 1)
            return
        text = self.value(node, depth)
        if text is not None:
            self.add(depth, node, text)

    def in_list(self, node: exp.In, depth: int, negated: bool) -> None:
        word = "不在" if negated else "在"
        query = node.args.get("query")
        shown = self.sql(node).replace(" IN ", " NOT IN ", 1) if negated else self.sql(node)
        if query is not None:
            text = f"{self.sql(node.this)} {word}下面子查詢的結果裡"
            if negated:
                text += "；⚠ 子查詢的結果只要有一個 NULL，NOT IN 就一列都不會成立"
            self.lines.append(Line(depth, shown, text + "："))
            self.query(query.this if isinstance(query, exp.Subquery) else query, depth + 1)
            return
        items = "、".join(self.sql(e) for e in node.expressions)
        text = f"{self.sql(node.this)} {word}這些值裡：{items}"
        if negated and any(isinstance(e, exp.Null) for e in node.expressions):
            text += "；⚠ 清單裡有 NULL，NOT IN 永遠不會成立"
        self.lines.append(Line(depth, shown, text))

    # -------------------------------------------------------- changes

    def insert(self, node: exp.Insert, depth: int) -> None:
        target = node.this
        columns = ""
        if isinstance(target, exp.Schema):
            columns = "，欄位依序是 " + "、".join(self.sql(c) for c in target.expressions)
            target = target.this
        name = self.sql(target)
        source = node.expression
        if isinstance(source, exp.Values):
            rows = len(source.expressions)
            self.add(depth, node, f"在資料表 {name} 新增 {rows} 列{columns}：")
            for row in source.expressions:
                self.add(depth + 1, row, "一列的值")
        else:
            self.add(depth, node, f"在資料表 {name} 新增下面查詢得到的每一列{columns}：")
            if source is not None:
                self.query(source, depth + 1)
        self.effects.append(f"會新增資料：{name}")

    def update(self, node: exp.Update, depth: int) -> None:
        name = self.sql(node.this)
        self.add(depth, node, f"修改資料表 {name} 裡的資料：")
        for assignment in node.expressions:
            self.add(depth + 1, assignment, f"把 {self.sql(assignment.this)} 改成 {self.sql(assignment.expression)}")
        where = node.args.get("where")
        if where is None:
            self.add(depth + 1, None, "⚠ 沒有 WHERE：表裡每一列都會被修改")
            self.effects.append(f"會修改資料：{name} 的每一列")
        else:
            self.add(depth + 1, where, "只改符合這個條件的列：")
            self.condition(where.this, depth + 2)
            self.effects.append(f"會修改資料：{name} 裡符合條件的列")

    def delete(self, node: exp.Delete, depth: int) -> None:
        name = self.sql(node.this)
        where = node.args.get("where")
        if where is None:
            self.add(depth, node, f"刪除資料表 {name} 的每一列（⚠ 沒有 WHERE；表本身保留）")
            self.effects.append(f"會刪除資料：{name} 的每一列")
        else:
            self.add(depth, node, f"刪除資料表 {name} 裡符合這個條件的列：")
            self.condition(where.this, depth + 1)
            self.effects.append(f"會刪除資料：{name} 裡符合條件的列")


def flatten(node: exp.Expression, kind: type) -> list[exp.Expression]:
    if isinstance(node, kind):
        return flatten(node.this, kind) + flatten(node.expression, kind)
    return [node]


def alias_of(node: exp.Expression) -> str:
    return node.alias_or_name if hasattr(node, "alias_or_name") else ""


def uses_table(condition: exp.Expression, table: str) -> bool:
    """A condition on the table's columns other than IS NULL."""
    for column in condition.find_all(exp.Column):
        if column.table == table:
            parent = column.parent
            if isinstance(parent, exp.Is) and isinstance(parent.expression, exp.Null):
                continue
            return True
    return False


def is_bare(node: exp.Expression, keys: list[exp.Expression]) -> bool:
    target = node.this if isinstance(node, exp.Alias) else node
    if not isinstance(target, exp.Column):
        return False
    return all(target.sql() != key.sql() and target.name != getattr(key, "name", None) for key in keys)


def translate(sql: str, dialect: str = "") -> Translation:
    if dialect not in READ:
        raise ValueError(f"unknown dialect {dialect!r}")
    try:
        statements = sqlglot.parse(sql, read=READ[dialect])
    except sqlglot.errors.SqlglotError as exc:
        reason = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))[:200]  # sqlglot underlines with terminal codes
        raise Untranslatable(f"這段 SQL 解析不了，無法翻譯（不代表資料庫一定會拒絕）：{reason}") from exc
    writer = Writer(dialect)
    for statement in statements:
        if statement is not None:
            writer.statement(statement, 0)
    return Translation(sql, dialect, writer.lines, list(dict.fromkeys(writer.effects)),
                       list(dict.fromkeys(writer.unknown)))
