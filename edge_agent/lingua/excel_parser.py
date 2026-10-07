"""Excel formula text to a tree, with Excel's own precedence.

Negation before ^, ^ from left to right, & below + and -. What the parser
does not cover (structured table references, 3D and external references,
the @ and # operators, unions) it refuses instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from edge_agent.lingua.regex_syntax import (
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
