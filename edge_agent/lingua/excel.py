"""Excel formulas to plain Traditional Chinese, exactly.

excel_parser reads the formula with Excel's own precedence (negation before
^, ^ from left to right, & below + and -), excel_functions holds the fixed
wording for each function, and this module words the tree. The traps a
reader misses are stated where they apply: VLOOKUP and MATCH without the
last argument search approximately, SUMIF and SUMIFS put the sum range at
opposite ends, NPV discounts its first value a whole period, ROUND rounds
halves away from zero, INT goes down. tests/test_lingua_excel.py computes
each stated behaviour in LibreOffice Calc (Excel's own engine is not on the
dev box; the laptop checks against Excel where it is installed).
"""

from __future__ import annotations

from dataclasses import dataclass

from edge_agent.lingua.excel_functions import FUNCTIONS, arg_names, required_count
from edge_agent.lingua.excel_parser import (
    CELL,
    Array,
    Binary,
    Bool,
    Call,
    Error,
    FormulaError,
    Name,
    Node,
    Number,
    Percent,
    Ref,
    Text,
    Token,
    Unary,
    parse,
)

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
