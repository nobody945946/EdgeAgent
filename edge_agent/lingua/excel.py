"""Excel formulas to plain Traditional Chinese, exactly.

A deterministic parser with Excel's own precedence (negation before ^, ^ from
left to right, & below + and -), and fixed wording for each function. The
traps a reader misses are stated where they apply: VLOOKUP and MATCH without
the last argument search approximately, SUMIF and SUMIFS put the sum range
at opposite ends, NPV discounts its first value a whole period, ROUND rounds
halves away from zero, INT goes down. tests/test_lingua_excel.py computes
each stated behaviour in LibreOffice Calc (Excel's own engine is not on the
dev box; the laptop checks against Excel where it is installed).

What the parser does not cover (structured table references, 3D and external
references, the @ and # operators, unions) it refuses instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from edge_agent.lingua.regex import (
    RegexError as _Base,  # noqa: F401  (same error style)
)


class FormulaError(ValueError):
    """Excel itself would not accept this formula."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"第 {position + 1} 個字元：{message}")
        self.position = position


class Unsupported(ValueError):
    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"第 {position + 1} 個字元：{message}（這個翻譯器還不支援，不做猜測）")
        self.position = position


# ---------------------------------------------------------------- tokens

ERRORS = ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#SPILL!", "#CALC!")
CELL = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})(?![A-Za-z0-9_.(])")
COLUMN = re.compile(r"(\$?)([A-Za-z]{1,3})(?=:)")
ROW = re.compile(r"(\$?)([0-9]{1,7})(?=:)")
COLUMN_END = re.compile(r"(\$?)([A-Za-z]{1,3})(?![A-Za-z0-9_.(])")
ROW_END = re.compile(r"(\$?)([0-9]{1,7})(?![0-9.])")
NUMBER = re.compile(r"[0-9]+(\.[0-9]*)?([eE][+-]?[0-9]+)?|\.[0-9]+([eE][+-]?[0-9]+)?")
NAME = re.compile(r"[A-Za-z_\\][A-Za-z0-9_.]*")
SHEET_WORD = r"(?:[A-Za-z_]|[^\x00-\x7f])(?:[A-Za-z0-9_.]|[^\x00-\x7f])*"
SHEET = re.compile(r"('(?:[^']|'')+'|" + SHEET_WORD + r")!")
SHEETS_3D = re.compile(SHEET_WORD + ":" + SHEET_WORD + "!")


@dataclass
class Token:
    kind: str  # num str bool err ref col row func name op ( ) , ; { }
    text: str
    start: int
    sheet: str = ""
    end: int = 0

    def __post_init__(self) -> None:
        self.end = self.end or self.start + len(self.text)


def tokenize(formula: str) -> list[Token]:
    text = formula.removeprefix("=")
    offset = len(formula) - len(text)
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        char = text[i]
        at = offset + i
        if char in " \t\r\n":
            i += 1
            continue
        if char == '"':
            j = i + 1
            value = []
            while True:
                if j >= len(text):
                    raise FormulaError("字串少了結尾的雙引號", at)
                if text[j] == '"':
                    if text[j + 1:j + 2] == '"':
                        value.append('"')
                        j += 2
                        continue
                    break
                value.append(text[j])
                j += 1
            tokens.append(Token("str", "".join(value), at, end=offset + j + 1))
            i = j + 1
            continue
        if char == "#":
            for error in ERRORS:
                if text.upper().startswith(error, i):
                    tokens.append(Token("err", error, at))
                    i += len(error)
                    break
            else:
                raise Unsupported("# 開頭的寫法（溢出參照等）", at)
            continue
        if char in "[@":
            raise Unsupported("表格的結構化參照或 @ 運算子", at)
        sheet = ""
        if SHEETS_3D.match(text, i):
            raise Unsupported("跨多個工作表的參照", at)
        match = SHEET.match(text, i)
        if match and not text.startswith("(", match.end()):
            sheet = match.group(1)
            sheet = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
            if ":" in sheet:
                raise Unsupported("跨多個工作表的參照", at)
            i = match.end()
        after_colon = len(tokens) >= 2 and tokens[-1].text == ":" and tokens[-2].kind in ("col", "row")
        patterns = [("ref", CELL), ("col", COLUMN), ("row", ROW)]
        if after_colon:
            # The end of A:A or 1:1 has no colon after it.
            patterns = [(tokens[-2].kind, COLUMN_END if tokens[-2].kind == "col" else ROW_END)]
        for kind, pattern in patterns:
            match = pattern.match(text, i)
            if match:
                tokens.append(Token(kind, match.group(0).upper(), at, sheet, offset + match.end()))
                i = match.end()
                break
        else:
            if sheet:
                raise Unsupported("工作表名稱後面接的不是儲存格", at)
            match = NUMBER.match(text, i)
            if match:
                tokens.append(Token("num", match.group(0), at))
                i = match.end()
                continue
            match = NAME.match(text, i)
            if match:
                word = match.group(0)
                after = text[match.end():].lstrip(" ")
                if after.startswith("("):
                    tokens.append(Token("func", word.upper().removeprefix("_XLFN."), at))
                elif word.upper() in ("TRUE", "FALSE"):
                    tokens.append(Token("bool", word.upper(), at))
                elif after.startswith("["):
                    raise Unsupported("表格的結構化參照", at)
                else:
                    tokens.append(Token("name", word, at))
                i = match.end()
                continue
            for op in ("<>", "<=", ">=", "+", "-", "*", "/", "^", "&", "=", "<", ">", "%", ":"):
                if text.startswith(op, i):
                    tokens.append(Token("op", op, at))
                    i += len(op)
                    break
            else:
                if char in "(),;{}":
                    tokens.append(Token(char, char, at))
                    i += 1
                else:
                    raise FormulaError(f"看不懂的字元「{char}」", at)
    return tokens


# ---------------------------------------------------------------- tree


@dataclass
class Node:
    start: int
    end: int


@dataclass
class Number(Node):
    value: str


@dataclass
class Text(Node):
    value: str


@dataclass
class Bool(Node):
    value: str


@dataclass
class Error(Node):
    value: str


@dataclass
class Ref(Node):
    first: Token
    last: Token | None = None  # a range first:last


@dataclass
class Name(Node):
    value: str


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Percent(Node):
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Call(Node):
    name: str
    args: list[Node | None] = field(default_factory=list)  # None: an empty argument


@dataclass
class Array(Node):
    rows: list[list[Node]]


BINARY = [("=", "<>", "<", ">", "<=", ">="), ("&",), ("+", "-"), ("*", "/"), ("^",)]


class Parser:
    def __init__(self, formula: str) -> None:
        self.formula = formula
        self.tokens = tokenize(formula)
        self.i = 0

    def peek(self, kind: str = "", text: str = "") -> Token | None:
        if self.i >= len(self.tokens):
            return None
        token = self.tokens[self.i]
        if (kind and token.kind != kind) or (text and token.text != text):
            return None
        return token

    def take(self) -> Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def end_of(self, node: Node) -> int:
        return node.end

    def parse(self) -> Node:
        if not self.tokens:
            raise FormulaError("公式是空的", 0)
        node = self.binary(0)
        if self.i < len(self.tokens):
            token = self.tokens[self.i]
            if token.kind in ("ref", "col", "row", "name", "func", "("):
                raise Unsupported("兩個參照之間的空格（交集運算子）", token.start)
            raise FormulaError(f"多了「{token.text}」", token.start)
        return node

    def binary(self, level: int) -> Node:
        if level == len(BINARY):
            return self.unary()
        node = self.binary(level + 1)
        while (token := self.peek("op")) and token.text in BINARY[level]:
            self.take()
            right = self.binary(level + 1)
            node = Binary(node.start, right.end, token.text, node, right)
        return node

    def unary(self) -> Node:
        # Excel: negation binds tighter than ^ and %, so -2^2 is 4.
        token = self.peek("op")
        if token and token.text in "+-":
            self.take()
            operand = self.unary()
            return Unary(token.start, operand.end, token.text, operand)
        node = self.postfix()
        return node

    def postfix(self) -> Node:
        node = self.primary()
        while (token := self.peek("op", "%")):
            self.take()
            node = Percent(node.start, token.start + 1, node)
        return node

    def primary(self) -> Node:
        token = self.peek()
        if token is None:
            raise FormulaError("公式在這裡就結束了，少了東西", len(self.formula))
        self.take()
        end = token.end
        if token.kind == "num":
            return Number(token.start, end, token.text)
        if token.kind == "str":
            return Text(token.start, end, token.text)
        if token.kind == "bool":
            return Bool(token.start, end, token.text)
        if token.kind == "err":
            return Error(token.start, end, token.text)
        if token.kind == "name":
            return Name(token.start, end, token.text)
        if token.kind in ("ref", "col", "row"):
            if self.peek("op", ":"):
                self.take()
                last = self.peek()
                if last is None or last.kind not in ("ref", "col", "row"):
                    raise FormulaError("冒號後面要接儲存格", token.start)
                self.take()
                kinds = {token.kind, last.kind}
                if len(kinds) > 1 or last.sheet:
                    raise Unsupported("這種範圍寫法", token.start)
                return Ref(token.start, last.end, token, last)
            if token.kind != "ref":
                raise FormulaError("整欄或整列要寫成 A:A、1:1 的範圍", token.start)
            return Ref(token.start, end, token)
        if token.kind == "func":
            return self.call(token)
        if token.kind == "(":
            node = self.binary(0)
            closing = self.expect(")", "括號 ( 沒有對應的 )")
            node.start, node.end = token.start, closing.start + 1
            return node
        if token.kind == "{":
            return self.array(token)
        raise FormulaError(f"這裡不能放「{token.text}」", token.start)

    def expect(self, kind: str, message: str) -> Token:
        token = self.peek(kind)
        if token is None:
            raise FormulaError(message, self.tokens[self.i].start if self.i < len(self.tokens) else len(self.formula))
        return self.take()

    def call(self, token: Token) -> Call:
        self.expect("(", "函數名稱後面要接 (")
        args: list[Node | None] = []
        if self.peek(")"):
            closing = self.take()
            return Call(token.start, closing.start + 1, token.text, args)
        while True:
            if self.peek(",") or self.peek(")"):
                args.append(None)
            else:
                args.append(self.binary(0))
            if self.peek(";"):
                raise Unsupported("用分號分隔參數的地區設定", self.tokens[self.i].start)
            nxt = self.peek()
            if nxt is not None and nxt.kind in ("ref", "col", "row", "name", "func", "("):
                raise Unsupported("兩個參照之間的空格（交集運算子）", nxt.start)
            if self.peek(","):
                self.take()
                continue
            closing = self.expect(")", f"{token.text}( 沒有對應的 )")
            return Call(token.start, closing.start + 1, token.text, args)

    def array(self, token: Token) -> Array:
        rows: list[list[Node]] = [[]]
        while True:
            element = self.unary()
            if not isinstance(element, (Number, Text, Bool, Error, Unary)):
                raise FormulaError("陣列常數 {…} 裡只能放數值、文字、TRUE／FALSE", element.start)
            rows[-1].append(element)
            if self.peek(","):
                self.take()
            elif self.peek(";"):
                self.take()
                rows.append([])
            else:
                closing = self.expect("}", "陣列常數 { 沒有對應的 }")
                if len({len(r) for r in rows}) > 1:
                    raise FormulaError("陣列常數每一列的個數要一樣", token.start)
                return Array(token.start, closing.start + 1, rows)


def parse(formula: str) -> Node:
    return Parser(formula).parse()


# ---------------------------------------------------------------- references


def column_number(letters: str) -> int:
    number = 0
    for char in letters.upper():
        number = number * 26 + ord(char) - 64
    return number


def column_letters(number: int) -> str:
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def split_cell(text: str) -> tuple[str, int, bool, bool]:
    match = CELL.fullmatch(text) or CELL.match(text)
    assert match
    return match.group(2).upper(), int(match.group(4)), bool(match.group(1)), bool(match.group(3))


def sheet_prefix(token: Token) -> str:
    return f"工作表「{token.sheet}」的" if token.sheet else ""


def describe_ref(node: Ref) -> str:
    first, last = node.first, node.last
    prefix = sheet_prefix(first)
    if last is None:
        col, row, abs_col, abs_row = split_cell(first.text)
        return f"{prefix}儲存格 {col}{row}{absolute_note(abs_col, abs_row)}"
    if first.kind == "col":
        a, b = first.text.strip("$"), last.text.strip("$")
        span = column_number(b) - column_number(a) + 1
        what = f"整個 {a} 欄" if a == b else f"整個 {a} 欄到 {b} 欄（{span} 欄）"
        return prefix + what
    if first.kind == "row":
        a, b = first.text.strip("$"), last.text.strip("$")
        return prefix + (f"整個第 {a} 列" if a == b else f"整個第 {a} 列到第 {b} 列")
    c1, r1, ac1, ar1 = split_cell(first.text)
    c2, r2, ac2, ar2 = split_cell(last.text)
    cols = abs(column_number(c2) - column_number(c1)) + 1
    rows = abs(r2 - r1) + 1
    note = absolute_note(ac1 or ac2, ar1 or ar2)
    return f"{prefix}範圍 {c1}{r1}:{c2}{r2}（{cols} 欄 × {rows} 列）{note}"


def absolute_note(abs_col: bool, abs_row: bool) -> str:
    if abs_col and abs_row:
        return "（有 $：複製公式時不會跟著改）"
    if abs_col:
        return "（欄有 $：複製公式時欄不變、列會跟著改）"
    if abs_row:
        return "（列有 $：複製公式時列不變、欄會跟著改）"
    return ""


def range_columns(node: Node | None) -> list[str] | None:
    """The column letters a range covers, left to right."""
    if not isinstance(node, Ref) or node.last is None or node.first.kind == "row":
        return None
    if node.first.kind == "col":
        a, b = node.first.text.strip("$"), node.last.text.strip("$")
    else:
        a, b = split_cell(node.first.text)[0], split_cell(node.last.text)[0]
    lo, hi = sorted((column_number(a), column_number(b)))
    return [column_letters(n) for n in range(lo, hi + 1)]


# ---------------------------------------------------------------- functions


@dataclass(frozen=True)
class Fn:
    text: str
    args: tuple[str, ...]  # names; "[name]" optional, "name..." repeats
    defaults: dict[int, str] = field(default_factory=dict)  # index of an optional arg -> what omitting means
    note: str = ""


ROUNDING = "四捨五入，剛好一半時往離 0 遠的方向進位（2.5→3，-2.5→-3）"
FUNCTIONS: dict[str, Fn] = {
    "SUM": Fn("加總", ("數值或範圍...",), note="範圍裡的文字和空白儲存格不算"),
    "AVERAGE": Fn("平均", ("數值或範圍...",), note="範圍裡的文字和空白儲存格不算進個數，但 0 會算"),
    "MAX": Fn("最大值", ("數值或範圍...",), note="範圍裡的文字和空白儲存格不算"),
    "MIN": Fn("最小值", ("數值或範圍...",), note="範圍裡的文字和空白儲存格不算"),
    "COUNT": Fn("數一數有幾個數值", ("數值或範圍...",), note="只數數值（日期也是數值）；文字和空白不算"),
    "COUNTA": Fn("數一數有幾個不是空白的", ("數值或範圍...",),
                 note="文字、數值、錯誤都算；公式算出的空字串 \"\" 也算"),
    "COUNTBLANK": Fn("數一數有幾個空白", ("範圍",), note="真正空白的和公式算出空字串 \"\" 的都算"),
    "COUNTIF": Fn("數一數範圍裡有幾格符合條件", ("範圍", "條件"),
                  note="條件不分大小寫；文字條件可用 * 和 ? 萬用字元；\">=100\" 這種寫法是比較"),
    "COUNTIFS": Fn("數一數同時符合所有條件的列有幾個", ("條件範圍", "條件..."), note="條件規則同 COUNTIF"),
    "SUMIF": Fn("把符合條件的格子對應的值加總", ("條件範圍", "條件", "[加總範圍]"),
                {2: "沒寫加總範圍：直接加總條件範圍本身"},
                note="注意參數順序：SUMIF 的加總範圍在最後，SUMIFS 的加總範圍在最前面"),
    "SUMIFS": Fn("把同時符合所有條件的列加總", ("加總範圍", "條件範圍", "條件..."),
                 note="注意參數順序：SUMIFS 的加總範圍在最前面，SUMIF 的在最後"),
    "AVERAGEIF": Fn("把符合條件的格子對應的值平均", ("條件範圍", "條件", "[平均範圍]"),
                    {2: "沒寫平均範圍：直接平均條件範圍本身"}),
    "IF": Fn("如果條件成立就用第一個結果，否則用第二個", ("條件", "[成立時]", "[不成立時]"),
             {1: "沒寫成立時的結果：成立時結果是 0", 2: "沒寫不成立時的結果：不成立時結果是 FALSE"}),
    "IFERROR": Fn("如果算出錯誤（任何錯誤，包括 #N/A 找不到）就改用替代值，否則用原結果", ("計算", "出錯時的替代值")),
    "IFNA": Fn("只有在結果是 #N/A（找不到）時改用替代值，其他錯誤照樣顯示", ("計算", "#N/A 時的替代值")),
    "AND": Fn("全部條件都成立才是 TRUE", ("條件...",)),
    "OR": Fn("任何一個條件成立就是 TRUE", ("條件...",)),
    "NOT": Fn("把 TRUE 變 FALSE、FALSE 變 TRUE", ("條件",)),
    "ROUND": Fn(f"四捨五入到指定的小數位數（{ROUNDING}）", ("數值", "小數位數"),
                note="小數位數是負數時，進位到十位、百位（例如 -2 是到百位）"),
    "ROUNDUP": Fn("無條件進位（往離 0 遠的方向）", ("數值", "小數位數")),
    "ROUNDDOWN": Fn("無條件捨去（往 0 的方向）", ("數值", "小數位數")),
    "INT": Fn("取整數：往下取到不大於它的整數（負數會變得更小，INT(-1.5) 是 -2）", ("數值",)),
    "TRUNC": Fn("直接砍掉小數部分（往 0 的方向，TRUNC(-1.5) 是 -1）", ("數值", "[保留小數位數]"),
                {1: "沒寫保留位數：砍到整數"}),
    "MOD": Fn("相除的餘數；結果的正負號跟著除數（MOD(-7,3) 是 2）", ("被除數", "除數")),
    "ABS": Fn("絕對值", ("數值",)),
    "SUMPRODUCT": Fn("把幾個範圍對應位置的值相乘，再全部加總", ("範圍...",),
                     note="範圍的大小必須一樣"),
    "VLOOKUP": Fn("在表格的第一欄找查找值，取同一列的第幾欄", ("查找值", "表格範圍", "第幾欄", "[比對方式]")),
    "HLOOKUP": Fn("在表格的第一列找查找值，取同一欄的第幾列", ("查找值", "表格範圍", "第幾列", "[比對方式]")),
    "MATCH": Fn("找查找值在範圍裡的第幾個位置", ("查找值", "範圍", "[比對方式]")),
    "INDEX": Fn("取範圍裡第幾列、第幾欄的那一格", ("範圍", "第幾列", "[第幾欄]"),
                {2: "沒寫第幾欄：範圍只有一欄時就是那一欄"}),
    "XLOOKUP": Fn("在查找範圍找查找值，取回傳範圍裡同位置的值",
                  ("查找值", "查找範圍", "回傳範圍", "[找不到時]", "[比對方式]", "[搜尋方向]"),
                  {3: "沒寫找不到時的值：找不到就是 #N/A", 4: "沒寫比對方式：完全相同才算",
                   5: "沒寫搜尋方向：從第一筆往後找，用第一個找到的"}),
    "TODAY": Fn("今天的日期（每次重新計算都會更新）", ()),
    "NOW": Fn("現在的日期和時間（每次重新計算都會更新）", ()),
    "DATE": Fn("用年、月、日組成日期；月或日超出範圍會往後進位（DATE(2024,13,1) 是 2025/1/1）",
               ("年", "月", "日")),
    "YEAR": Fn("取日期的年份", ("日期",)),
    "MONTH": Fn("取日期的月份", ("日期",)),
    "DAY": Fn("取日期是幾號", ("日期",)),
    "EOMONTH": Fn("幾個月後那個月的最後一天", ("開始日期", "幾個月後（負數是之前）")),
    "EDATE": Fn("幾個月後的同一天", ("開始日期", "幾個月後（負數是之前）")),
    "LEFT": Fn("從左邊取幾個字", ("文字", "[幾個字]"), {1: "沒寫幾個字：取 1 個"}),
    "RIGHT": Fn("從右邊取幾個字", ("文字", "[幾個字]"), {1: "沒寫幾個字：取 1 個"}),
    "MID": Fn("從第幾個字開始取幾個字", ("文字", "從第幾個字", "取幾個字")),
    "LEN": Fn("文字有幾個字（中文字也是一個算一個）", ("文字",)),
    "TRIM": Fn("去掉前後的空格，中間連續的空格縮成一個（只處理半形空格，全形空格不動）", ("文字",)),
    "UPPER": Fn("轉成大寫", ("文字",)),
    "LOWER": Fn("轉成小寫", ("文字",)),
    "FIND": Fn("找文字在另一段文字裡的第幾個字開始（區分大小寫，不能用萬用字元）",
               ("要找的文字", "在哪段文字裡找", "[從第幾個字開始]"), {2: "沒寫從第幾個字：從第 1 個"}),
    "SEARCH": Fn("找文字在另一段文字裡的第幾個字開始（不分大小寫，可用 * 和 ? 萬用字元）",
                 ("要找的文字", "在哪段文字裡找", "[從第幾個字開始]"), {2: "沒寫從第幾個字：從第 1 個"}),
    "SUBSTITUTE": Fn("把文字裡的某段字換成別的字", ("文字", "舊字", "新字", "[第幾次出現的]"),
                     {3: "沒寫第幾次：全部都換"}),
    "CONCATENATE": Fn("把幾段文字接起來", ("文字...",)),
    "CONCAT": Fn("把幾段文字或範圍接起來", ("文字或範圍...",)),
    "TEXTJOIN": Fn("用分隔字把幾段文字接起來", ("分隔字", "是否略過空白", "文字或範圍...")),
    "TEXT": Fn("把數值照格式變成文字", ("數值", "格式碼"), note="結果是文字，不能再直接當數字計算"),
    "VALUE": Fn("把看起來像數字的文字變成數值", ("文字",)),
    "PMT": Fn("每期要付多少（貸款、分期）", ("每期利率", "期數", "現值（借到的錢）", "[終值]", "[付款時間]"),
              {3: "沒寫終值：最後還清到 0", 4: "沒寫付款時間：每期期末付款"},
              note="利率要和期數用同一個單位（月付就用年利率÷12）；借到的錢是正數時，結果是負數（代表付出去）"),
    "FV": Fn("照固定利率和每期金額，最後會累積成多少", ("每期利率", "期數", "每期金額", "[現值]", "[付款時間]"),
             {3: "沒寫現值：從 0 開始", 4: "沒寫付款時間：每期期末"},
             note="付出去的錢寫成負數，結果才會是正數"),
    "PV": Fn("未來一連串固定金額，折算成現在值多少", ("每期利率", "期數", "每期金額", "[終值]", "[付款時間]"),
             {3: "沒寫終值：0", 4: "沒寫付款時間：每期期末"}),
    "NPV": Fn("把之後每期的現金流折現加總", ("每期折現率", "現金流..."),
              note="第一筆現金流也會被折現一整期（當成第 1 期期末才發生）；"
                   "第 0 期（現在）的投入要寫在 NPV 外面另外加"),
    "IRR": Fn("讓淨現值剛好等於 0 的報酬率", ("現金流範圍", "[猜測值]"), {1: "沒寫猜測值：從 10% 開始找"},
              note="現金流要等間隔，而且至少有一正一負"),
    "XNPV": Fn("照實際日期折現加總", ("年折現率", "現金流", "日期")),
    "XIRR": Fn("照實際日期計算的年化報酬率", ("現金流", "日期", "[猜測值]"), {2: "沒寫猜測值：從 10% 開始找"}),
    "RATE": Fn("每期的利率", ("期數", "每期金額", "現值", "[終值]", "[付款時間]", "[猜測值]")),
    "NPER": Fn("要幾期才能付清或達成", ("每期利率", "每期金額", "現值", "[終值]", "[付款時間]")),
    "LARGE": Fn("第幾大的值", ("範圍", "第幾大")),
    "SMALL": Fn("第幾小的值", ("範圍", "第幾小")),
    "RANK": Fn("在範圍裡排第幾名", ("數值", "範圍", "[順序]"), {2: "沒寫順序：由大到小排（最大的是第 1 名）"}),
    "ISBLANK": Fn("是不是空白儲存格（公式算出的空字串不算空白）", ("值",)),
    "ISNUMBER": Fn("是不是數值", ("值",)),
    "ISTEXT": Fn("是不是文字", ("值",)),
    "ISERROR": Fn("是不是任何一種錯誤", ("值",)),
    "ISNA": Fn("是不是 #N/A 錯誤", ("值",)),
}


def arg_names(fn: Fn, count: int) -> list[str]:
    names = [n.strip("[]") for n in fn.args]
    if names and names[-1].endswith("..."):
        base = names[-1].removesuffix("...")
        fixed = names[:-1]
        extra = count - len(fixed)
        if len(fn.args) >= 2 and fn.args[-2].endswith("範圍") and base == "條件":
            # COUNTIFS/SUMIFS pairs: 條件範圍1, 條件1, 條件範圍2, 條件2 ...
            pairs = []
            start = len(fixed) - 1
            for k in range(count - start):
                pairs.append(f"{'條件範圍' if k % 2 == 0 else '條件'} {k // 2 + 1}")
            return names[:start] + pairs
        return fixed + [f"{base} {k + 1}" if extra > 1 else base for k in range(max(extra, 0))]
    return names[:count] + [f"第 {k + 1} 個參數" for k in range(len(names), count)]


def required_count(fn: Fn) -> int:
    return sum(1 for a in fn.args if not a.startswith("[") and not a.endswith("..."))


# ---------------------------------------------------------------- wording

OPERATORS = {
    "+": "相加", "-": "相減（左邊減右邊）", "*": "相乘", "/": "相除（左邊除以右邊）",
    "^": "次方（左邊的右邊次方）", "&": "把兩邊接成一段文字（數字會先變成文字）",
    "=": "比較：兩邊相等嗎（文字比較不分大小寫）", "<>": "比較：兩邊不相等嗎（文字比較不分大小寫）",
    "<": "比較：左邊小於右邊嗎", ">": "比較：左邊大於右邊嗎", "<=": "比較：左邊小於或等於右邊嗎",
    ">=": "比較：左邊大於或等於右邊嗎",
}


@dataclass
class Line:
    depth: int
    source: str
    text: str


CRITERIA_FUNCTIONS = {"COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIF"}
CRITERIA_OPS = (("<>", "不等於"), (">=", "大於或等於"), ("<=", "小於或等於"), (">", "大於"), ("<", "小於"),
                ("=", "等於"))


def number_text(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer() and abs(number) >= 10000 and "e" not in value.lower():
        return f"{value}（{int(number):,}）"
    return value


def criteria_text(value: str) -> str:
    """COUNTIF-style criteria: a comparison, or a whole-cell match (case
    ignored) with * and ? as wildcards and ~ to take them literally."""
    for op, word in CRITERIA_OPS:
        if value.startswith(op):
            rest = value[len(op):]
            if rest == "":
                return {"<>": "條件「<>」：不是真正空白的格子（公式算出空字串 \"\" 的格子也算進來）",
                        "=": "條件「=」：真正空白的格子（公式算出空字串 \"\" 的格子不算）"}.get(op, f"條件「{value}」")
            return f"條件「{value}」：{word} {rest}（{rest} 是數字時比數值，否則比文字，不分大小寫）"
    if value == "":
        return "條件「」（空字串）：空白的格子（真正空白的，和公式算出空字串 \"\" 的都算）"
    wild = any(c in value for c in "*?") and "~" not in value
    if wild:
        return (f"條件「{value}」：整格內容符合這個樣式（* 代表任意多個字、? 代表一個字，不分大小寫）")
    return f"條件「{value}」：整格內容等於「{value}」（不分大小寫；數字格會和數值比較）"


def value_text(node: Node | None, formula: str) -> str:
    if node is None:
        return "（空的）"
    return formula[node.start:node.end]


def lookup_detail(call: Call, formula: str) -> list[str]:
    """The concrete reading of VLOOKUP/HLOOKUP/MATCH/XLOOKUP calls, traps included."""
    name, args = call.name, call.args
    out = []
    if name in ("VLOOKUP", "HLOOKUP") and len(args) >= 3:
        table, index, mode = args[1], args[2], args[3] if len(args) > 3 else None
        columns = range_columns(table) if name == "VLOOKUP" else None
        if columns and isinstance(index, Number) and index.value.isdigit():
            n = int(index.value)
            if 1 <= n <= len(columns):
                out.append(f"找的是 {columns[0]} 欄，取回的是 {columns[n - 1]} 欄（範圍裡的第 {n} 欄）")
            else:
                out.append(f"範圍只有 {len(columns)} 欄，第 {n} 欄不存在，結果會是 #REF!")
        out.append(approximate_note(mode, "第一欄" if name == "VLOOKUP" else "第一列"))
    if name == "MATCH" and len(args) >= 2:
        mode = args[2] if len(args) > 2 else None
        if mode is None or (isinstance(mode, Number) and mode.value == "1"):
            out.append("比對方式是 1（沒寫也是 1）：近似比對，找小於或等於查找值的最大一個；"
                       "範圍必須由小到大排好，否則結果可能是錯的；找不到完全相同的值也不會出錯")
        elif isinstance(mode, Number) and mode.value == "0":
            out.append("比對方式是 0：完全相同才算，找不到時結果是 #N/A")
        elif isinstance(mode, Unary) and mode.op == "-" and isinstance(mode.operand, Number) and mode.operand.value == "1":
            out.append("比對方式是 -1：找大於或等於查找值的最小一個；範圍必須由大到小排好")
    return out


def approximate_note(mode: Node | None, where: str) -> str:
    exact = (isinstance(mode, Bool) and mode.value == "FALSE") or (isinstance(mode, Number) and mode.value == "0")
    approx = mode is None or (isinstance(mode, Bool) and mode.value == "TRUE") or (
        isinstance(mode, Number) and mode.value not in ("0",))
    if exact:
        return "比對方式是 FALSE／0：完全相同才算（不分大小寫）；找不到時結果是 #N/A"
    if approx:
        said = "沒寫比對方式，預設是" if mode is None else "比對方式是 TRUE："
        return (f"{said}近似比對：找小於或等於查找值的最大一個；{where}必須由小到大排好，"
                "否則結果可能是錯的；找不到完全相同的值也不會出錯，而是給最接近的一筆")
    return "比對方式由公式決定（不是固定值），要看算出來是 TRUE 還是 FALSE"


def lines_for(node: Node | None, formula: str, depth: int) -> list[Line]:
    if node is None:
        return [Line(depth, "", "（空的參數）")]
    src = formula[node.start:node.end]
    if isinstance(node, Number):
        return [Line(depth, src, "數值 " + number_text(node.value))]
    if isinstance(node, Text):
        return [Line(depth, src, f"文字「{node.value}」" if node.value else "空字串（什麼字都沒有）")]
    if isinstance(node, Bool):
        return [Line(depth, src, f"邏輯值 {node.value}（計算時當成 {'1' if node.value == 'TRUE' else '0'}）")]
    if isinstance(node, Error):
        return [Line(depth, src, f"錯誤值 {node.value}")]
    if isinstance(node, Name):
        return [Line(depth, src, f"名稱「{node.value}」（活頁簿裡定義的名稱，內容要看定義）")]
    if isinstance(node, Ref):
        return [Line(depth, src, describe_ref(node))]
    if isinstance(node, Percent):
        if isinstance(node.operand, Number):
            return [Line(depth, src, f"數值 {node.operand.value}%（= {node.operand.value} ÷ 100）")]
        return [Line(depth, src, "除以 100（% 號）：")] + lines_for(node.operand, formula, depth + 1)
    if isinstance(node, Unary):
        if isinstance(node.operand, Number):
            sign = "-" if node.op == "-" else ""
            return [Line(depth, src, "數值 " + number_text(sign + node.operand.value))]
        what = "負號：取相反數" if node.op == "-" else "正號（不改變值）"
        return [Line(depth, src, what + "：")] + lines_for(node.operand, formula, depth + 1)
    if isinstance(node, Binary):
        text = OPERATORS[node.op]
        if node.op == "^" and isinstance(node.left, Binary) and node.left.op == "^":
            text += "；Excel 由左往右算，2^3^2 是 (2^3)^2 = 64"
        if node.op == "^" and isinstance(node.left, Unary) and node.left.op == "-":
            text += "；Excel 先算負號再算次方，-2^2 是 (-2)^2 = 4"
        return ([Line(depth, src, text + "：")] + lines_for(node.left, formula, depth + 1)
                + lines_for(node.right, formula, depth + 1))
    if isinstance(node, Array):
        size = f"{len(node.rows)} 列 × {len(node.rows[0])} 欄"
        return [Line(depth, src, f"陣列常數（{size}），內容照寫的值")]
    if isinstance(node, Call):
        return call_lines(node, formula, depth)
    raise TypeError(node)


def call_lines(node: Call, formula: str, depth: int) -> list[Line]:
    src = formula[node.start:node.end]
    fn = FUNCTIONS.get(node.name)
    if fn is None:
        out = [Line(depth, src, f"函數 {node.name}：沒有中文說明（不做猜測），參數：")]
        for k, arg in enumerate(node.args, 1):
            out.append(Line(depth + 1, value_text(arg, formula), f"第 {k} 個參數："))
            out += lines_for(arg, formula, depth + 2)
        return out
    if len(node.args) < required_count(fn):
        raise FormulaError(f"{node.name} 至少要 {required_count(fn)} 個參數", node.start)
    head = f"{node.name}：{fn.text}"
    out = [Line(depth, src, head)]
    for detail in lookup_detail(node, formula):
        out.append(Line(depth + 1, "", "⚠ " + detail if "近似" in detail or "不存在" in detail else detail))
    if fn.note:
        out.append(Line(depth + 1, "", "注意：" + fn.note))
    names = arg_names(fn, len(node.args))
    for name, arg in zip(names, node.args):
        if name.startswith("條件") and not name.startswith("條件範圍") and node.name in CRITERIA_FUNCTIONS \
                and isinstance(arg, Text):
            out.append(Line(depth + 1, value_text(arg, formula), f"{name}：{criteria_text(arg.value)}"))
            continue
        inner = lines_for(arg, formula, depth + 2)
        if len(inner) == 1:
            out.append(Line(depth + 1, inner[0].source, f"{name}：{inner[0].text}"))
        else:
            out.append(Line(depth + 1, value_text(arg, formula), f"{name}："))
            out += inner
    for index, meaning in fn.defaults.items():
        omitted = index >= len(node.args) or node.args[index] is None
        if omitted and node.name not in ("VLOOKUP", "HLOOKUP", "MATCH"):
            out.append(Line(depth + 1, "", meaning))
    return out


@dataclass
class Translation:
    formula: str
    lines: list[Line]
    notes: list[str]

    def text(self) -> str:
        body, counters = [], [0] * 64
        for line in self.lines:
            counters[line.depth] += 1
            for deeper in range(line.depth + 1, len(counters)):
                counters[deeper] = 0
            number = ".".join(str(n) for n in counters[:line.depth + 1])
            source = f"`{line.source}`：" if line.source else ""
            body.append(f"{'  ' * line.depth}{number}. {source}{line.text}")
        return "\n".join([f"Excel 公式：{self.formula}", "逐項說明："] + body + self.notes)


def translate(formula: str) -> Translation:
    text = formula if formula.startswith("=") else "=" + formula
    node = parse(text)
    return Translation(text, lines_for(node, text, 0), [])
