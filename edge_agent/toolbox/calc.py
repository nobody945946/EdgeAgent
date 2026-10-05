"""Arithmetic and calendar questions, answered exactly instead of guessed.

calculate parses the expression with `ast` and evaluates only numbers, the
arithmetic operators and a short list of math functions; nothing is passed to
eval. Rational results stay exact (Fraction), so 0.1 + 0.2 is 0.3; only
functions such as sqrt or sin, and pi or e, make the answer approximate, and
the result says which it is. date_calc counts days with the real calendar.
"""

from __future__ import annotations

import ast
import math
import re
import unicodedata
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, register

Number = Fraction | float
# name: (fewest, most) arguments
FUNCTIONS = {"sqrt": (1, 1), "log": (1, 2), "log10": (1, 1), "exp": (1, 1), "sin": (1, 1), "cos": (1, 1),
             "tan": (1, 1), "round": (1, 2), "abs": (1, 1), "min": (1, 50), "max": (1, 50), "floor": (1, 1),
             "ceil": (1, 1), "pow": (2, 2)}
CONSTANTS = {"pi": math.pi, "e": math.e}
MAX_DIGITS = 20000


def _strip_thousands(text: str) -> str:
    """1,234,567 -> 1234567, but not the commas between a function's arguments."""
    out: list[str] = []
    calls: list[bool] = []  # for each open bracket: is it a function call's?
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            calls.append(bool(re.search(r"[A-Za-z_]\w*\s*$", "".join(out))))
        elif ch == ")" and calls:
            calls.pop()
        elif ch.isdigit() and not (out and (out[-1].isalnum() or out[-1] in "._")) and not (calls and calls[-1]):
            m = re.match(r"\d{1,3}(?:,\d{3})+(?![\d,])", text[i:])
            if m:
                out.append(m.group().replace(",", ""))
                i += m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def prepare(expression: str) -> tuple[str, list[str]]:
    """Calculator spelling to Python spelling: × ÷ ^ ² 15% and full-width characters."""
    text = expression.replace("²", "^2").replace("³", "^3")
    text = unicodedata.normalize("NFKC", text).strip()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("–", "-")
    text = re.sub(r"(?<=[\d)])\s+[xX]\s+(?=[\d(])", " * ", text)
    text = _strip_thousands(text)
    notes: list[str] = []

    def percent(m: re.Match) -> str:
        notes.append(f"{m.group(1)}% 當成 {m.group(1)}/100")
        return f"({m.group(1)}/100)"

    text = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(.A-Za-z_])", percent, text)
    return text.replace("^", "**"), notes


def _power(a: Number, b: Number) -> Number:
    if isinstance(a, Fraction) and isinstance(b, Fraction) and b.denominator == 1:
        exponent = int(b)
        if a == 0 and exponent < 0:
            raise ToolError("0 的負數次方是除以零，沒有答案")
        width = max(len(str(abs(a.numerator))), len(str(a.denominator)))
        if a not in (0, 1, -1) and abs(exponent) * width > MAX_DIGITS:
            raise ToolError("結果的位數太多（超過兩萬位），不算")
        return a ** exponent
    base, exponent = float(a), float(b)
    if base < 0 and not exponent.is_integer():
        raise ToolError("負數的非整數次方不是實數")
    try:
        return math.pow(base, exponent)
    except OverflowError:
        raise ToolError("數字太大，超出範圍") from None


def _round_half_up(x: Number, places: int) -> Fraction:
    value = Fraction(Decimal(repr(x))) if isinstance(x, float) else x
    scaled = value * Fraction(10) ** places
    rounded = math.floor(abs(scaled) + Fraction(1, 2)) * (1 if scaled >= 0 else -1)
    return Fraction(rounded) / Fraction(10) ** places


def _call(name: str, args: list[Number]) -> Number:
    low, high = FUNCTIONS[name]
    if not low <= len(args) <= high:
        raise ToolError(f"{name} 要 {low}" + (f" 到 {high}" if high != low else "") + f" 個數字，收到 {len(args)} 個")
    x = args[0]
    if name == "sqrt":
        if x < 0:
            raise ToolError("負數不能開平方根")
        if isinstance(x, Fraction):
            n, d = math.isqrt(x.numerator), math.isqrt(x.denominator)
            if n * n == x.numerator and d * d == x.denominator:
                return Fraction(n, d)
        return math.sqrt(x)
    if name in ("log", "log10"):
        if x <= 0 or (len(args) == 2 and (args[1] <= 0 or args[1] == 1)):
            raise ToolError("對數只能算正數（底數也要是正數且不是 1）")
        return math.log10(x) if name == "log10" else math.log(x, *(float(a) for a in args[1:]))
    if name in ("exp", "sin", "cos", "tan"):
        try:
            return getattr(math, name)(float(x))
        except OverflowError:
            raise ToolError("數字太大，超出範圍") from None
    if name == "round":
        places = args[1] if len(args) == 2 else Fraction(0)
        if not (isinstance(places, Fraction) and places.denominator == 1 and abs(places) <= 100):
            raise ToolError("round 的第二個數字是小數位數，要是整數")
        return _round_half_up(x, int(places))
    if name == "abs":
        return abs(x)
    if name in ("min", "max"):
        return min(args) if name == "min" else max(args)
    if name in ("floor", "ceil"):
        return Fraction((math.floor if name == "floor" else math.ceil)(x))
    return _power(x, args[1])


def _evaluate(node: ast.AST, source: str) -> Number:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, source)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ToolError(f"不認得：{ast.get_source_segment(source, node) or node.value!r}")
        literal = (ast.get_source_segment(source, node) or "").replace("_", "")
        try:
            return Fraction(literal)  # "0.1" stays exactly one tenth
        except ValueError:
            return Fraction(node.value) if isinstance(node.value, int) else Fraction(Decimal(repr(node.value)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _evaluate(node.operand, source)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        a, b = _evaluate(node.left, source), _evaluate(node.right, source)
        if isinstance(node.op, ast.Pow):
            return _power(a, b)
        if isinstance(node.op, ast.Div | ast.FloorDiv | ast.Mod) and b == 0:
            raise ToolError("除以零，沒有答案")
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.FloorDiv):
            return Fraction(math.floor(a / b))
        if isinstance(node.op, ast.Mod):
            return a % b
    if isinstance(node, ast.Name) and node.id in CONSTANTS:
        return CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS \
            and not node.keywords:
        return _call(node.func.id, [_evaluate(arg, source) for arg in node.args])
    shown = ast.get_source_segment(source, node) or type(node).__name__
    hint = "乘法要寫 *，例如 2*(3+4)；" if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name) else ""
    raise ToolError(f"只能算數學算式；不認得「{shown}」"
                    f"（{hint}可以用 + - * / // % ^ 和 {'、'.join(FUNCTIONS)}、pi、e）")


def _exact_decimal(x: Fraction) -> str | None:
    """The exact decimal digits when the fraction ends (denominator only 2s and 5s)."""
    d, twos, fives = x.denominator, 0, 0
    while d % 2 == 0:
        d, twos = d // 2, twos + 1
    while d % 5 == 0:
        d, fives = d // 5, fives + 1
    if d != 1:
        return None
    places = max(twos, fives)
    digits = str(abs(x.numerator) * 10 ** places // x.denominator).rjust(places + 1, "0")
    sign = "-" if x < 0 else ""
    return sign + (f"{digits[:-places]}.{digits[-places:]}" if places else digits)


def _ten_digits(x: Number) -> str:
    if isinstance(x, float):
        return format(x, ".10g")
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 10, ROUND_HALF_UP
        d = Decimal(x.numerator) / Decimal(x.denominator)
    text = format(d, "f") if -6 <= d.adjusted() < 15 else format(d, "e")
    return text.rstrip("0").rstrip(".") if "." in text and "e" not in text else text


def calculate_run(params: dict, root: Path) -> str:
    expression = params["expression"]
    if len(expression) > 300:
        raise ToolError("算式太長（最多 300 個字）")
    text, notes = prepare(expression)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ToolError(f"看不懂這個算式：「{expression}」（乘法要寫 *，例如 2*(3+4)）") from None
    try:
        value = _evaluate(tree, text)
    except RecursionError:
        raise ToolError("算式的括號太多層") from None
    lines = [f"算式（解讀為）：{ast.unparse(tree)}", *notes]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ToolError("結果不是有限的數字")
        lines.append(f"結果 ≈ {_ten_digits(value)}（10 位有效數字；用到 sqrt、sin、log、pi 這類函數，是近似值）")
        return "\n".join(lines)
    if value.denominator == 1:
        digits = str(abs(value.numerator))
        if len(digits) > 60:
            lines.append(f"結果（精確值，共 {len(digits)} 位數）：{str(value)[:200]}" + ("…" if len(digits) > 200 else ""))
            lines.append(f"約 {_ten_digits(value)}（10 位有效數字）")
        else:
            lines.append(f"結果：{value.numerator:,}（精確值）" if abs(value) >= 10000 else f"結果：{value}（精確值）")
        return "\n".join(lines)
    exact = _exact_decimal(value)
    if exact is not None and len(exact) <= 40:
        lines.append(f"結果：{exact}（精確值）")
        if len(exact.lstrip("-0.").replace(".", "")) > 10:
            lines.append(f"約 {_ten_digits(value)}（10 位有效數字）")
    else:
        lines.append(f"結果：{value}（分數，精確值）≈ {_ten_digits(value)}（10 位有效數字）")
    return "\n".join(lines)


register(Tool(
    name="calculate",
    summary="Evaluate an arithmetic expression exactly (fractions kept exact; ^ power, 15% = 0.15, sqrt, log…).",
    keywords="計算 算 加減乘除 數學 百分比 平方根 次方 多少錢 計算機 calculator math arithmetic percent sum "
             "compute 等於多少",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string",
                           "description": "算式，例如「(1,200 + 350) * 1.05」「2^10」「15% * 3200」「sqrt(2)」"},
        },
        "required": ["expression"],
    },
    kind="read", destructive=False, windows_only=False,
    run=calculate_run, preview=None,
))


# ---------------------------------------------------------------- date_calc

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
RELATIVE = {"今天": 0, "today": 0, "明天": 1, "tomorrow": 1, "昨天": -1, "yesterday": -1, "後天": 2, "前天": -2}


def parse_date(text: str) -> date:
    t = unicodedata.normalize("NFKC", text).strip()
    if t.casefold() in RELATIVE:
        return date.today() + timedelta(days=RELATIVE[t.casefold()])
    m = re.fullmatch(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", t)
    roc = re.fullmatch(r"民國\s*(\d{1,3})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", t)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if roc:
            return date(int(roc.group(1)) + 1911, int(roc.group(2)), int(roc.group(3)))
    except ValueError:
        raise ToolError(f"沒有「{text}」這一天") from None
    raise ToolError(f"看不懂日期「{text}」；請用 YYYY-MM-DD（例如 2026-10-06）或「今天」")


def _day(d: date) -> str:
    return f"{d.isoformat()}（{WEEKDAYS[d.weekday()]}）"


def date_run(params: dict, root: Path) -> str:
    op = params["op"]
    start = parse_date(params["date"])
    if op == "weekday":
        return f"{start.isoformat()} 是{WEEKDAYS[start.weekday()]}。"
    if op == "add":
        if params["days"] is None:
            raise ToolError("op 是 add 時要給 days（要加幾天，負數就是往前算）")
        days = params["days"]
        try:
            end = start + timedelta(days=days)
        except OverflowError:
            raise ToolError("超出日曆範圍（西元 1 到 9999 年）") from None
        verb = f"加 {days} 天" if days >= 0 else f"減 {-days} 天"
        return f"{_day(start)}{verb}是 {_day(end)}。"
    other_text = params["other"] or "今天"
    other = parse_date(other_text)
    delta = (other - start).days
    weeks, rest = divmod(abs(delta), 7)
    span = f"{abs(delta):,} 天" + (f"（{weeks} 週又 {rest} 天）" if weeks else "")
    lines = [f"從 {_day(start)}到 {_day(other)}" + (f"：{other.isoformat()} 比較早，" if delta < 0 else "：")
             + f"相差 {span}。"]
    if delta:
        lines.append(f"（不算起始那天；如果頭尾兩天都算，是 {abs(delta) + 1:,} 天。）")
    if not params["other"]:
        lines.append(f"沒有給 other，用今天（{date.today().isoformat()}）。")
    return "\n".join(lines)


register(Tool(
    name="date_calc",
    summary="Date arithmetic: days between two dates, the date N days later, or the weekday of a date.",
    keywords="日期 幾天 天數 相差 倒數 幾號 星期幾 禮拜幾 加幾天 到期 date days between add days weekday "
             "deadline countdown calendar 民國",
    parameters={
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["diff", "add", "weekday"],
                   "description": "diff 兩個日期相差幾天、add 加減天數後是哪天、weekday 是星期幾"},
            "date": {"type": "string", "description": "日期，YYYY-MM-DD 或「今天」「明天」「昨天」"},
            "other": {"type": "string", "description": "diff 用的另一個日期；不填就是今天"},
            "days": {"type": "integer", "minimum": -3650000, "maximum": 3650000,
                     "description": "add 用：要加幾天（負數是往前）"},
        },
        "required": ["op", "date"],
    },
    kind="read", destructive=False, windows_only=False,
    run=date_run, preview=None,
))
