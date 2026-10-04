"""Regular expressions to plain Traditional Chinese, deterministically.

Three flavors, because the same pattern means different things:
python (Python `re`), dotnet (.NET: PowerShell -match, [regex]) and js
(JavaScript without the u/v flags). Every flavor-dependent sentence below is
checked against the real engines in tests/test_lingua_regex.py.

What the parser is not sure about it refuses (`Unsupported`) rather than
guess; what the engine itself would reject it reports (`RegexError`).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

FLAVORS = ("python", "dotnet", "js")
FLAVOR_NAMES = {
    "python": "Python 的 re 模組",
    "dotnet": ".NET（PowerShell 的 -match、-replace、[regex]）",
    "js": "JavaScript（沒有 u／v 旗標）",
}
# Flags a caller may pass, per flavor (letters as each language spells them).
FLAGS = {"python": "aimsx", "dotnet": "imnsx", "js": "ims"}
FLAG_NAMES = {
    "i": "不分大小寫",
    "m": "多行模式（^ 和 $ 也對每一行的開頭和結尾成立）",
    "s": "點號 . 也比對換行",
    "x": "忽略樣式裡的空白和 # 註解",
    "a": "\\d、\\w、\\s、\\b 只認半形 ASCII",
    "n": "沒有名稱的括號不擷取",
}


class RegexError(ValueError):
    """The engine itself would reject this pattern."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"第 {position + 1} 個字元：{message}")
        self.position = position


class Unsupported(ValueError):
    """This translator does not cover it; it says so instead of guessing."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"第 {position + 1} 個字元：{message}（這個翻譯器還不支援，不做猜測）")
        self.position = position


# ---------------------------------------------------------------- nodes


@dataclass
class Node:
    start: int
    end: int


@dataclass
class Literal(Node):
    char: str
    flags: frozenset = frozenset()
    note: str = ""


@dataclass
class Shorthand(Node):
    letter: str  # d D w W s S
    flags: frozenset = frozenset()


@dataclass
class Property(Node):
    name: str
    negated: bool


@dataclass
class Dot(Node):
    flags: frozenset = frozenset()


@dataclass
class Anchor(Node):
    kind: str  # ^ $ A Z z G b B
    flags: frozenset = frozenset()


@dataclass
class ClassItem:
    kind: str  # char, range, shorthand, property
    low: str = ""
    high: str = ""
    letter: str = ""
    name: str = ""
    negated: bool = False


@dataclass
class CharClass(Node):
    negated: bool
    items: list[ClassItem]
    flags: frozenset = frozenset()


@dataclass
class Group(Node):
    kind: str  # capture named noncapture lookahead neglookahead lookbehind neglookbehind atomic scoped
    child: Node
    name: str = ""
    number: int = 0
    flags_on: str = ""
    flags_off: str = ""


@dataclass
class InlineFlags(Node):
    on: str
    off: str


@dataclass
class Comment(Node):
    text: str


@dataclass
class Backref(Node):
    number: int = 0
    name: str = ""
    flags: frozenset = frozenset()


@dataclass
class Quantified(Node):
    child: Node
    low: int
    high: int | None
    mode: str  # greedy lazy possessive


@dataclass
class Sequence(Node):
    items: list[Node] = field(default_factory=list)


@dataclass
class Alternation(Node):
    branches: list[Node] = field(default_factory=list)


ATOMS = (Literal, Shorthand, Property, Dot, CharClass, Backref)
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v"}
HEX = "0123456789abcdefABCDEF"


# ---------------------------------------------------------------- parser


class Parser:
    def __init__(self, pattern: str, flavor: str, flags: str) -> None:
        if flavor not in FLAVORS:
            raise ValueError(f"unknown flavor {flavor!r}")
        bad = set(flags) - set(FLAGS[flavor])
        if bad:
            raise ValueError(f"flags {''.join(sorted(bad))!r} are not {flavor} flags")
        self.pattern, self.flavor, self.pos = pattern, flavor, 0
        self.flags = set(flags)
        self.groups: list[Group] = []
        self.names: dict[str, Group] = {}
        self.backrefs: list[Backref] = []
        self.open: list[Group] = []
        self.leading_end = 0  # Python: global flag groups may only come first

    # -- helpers

    def peek(self, text: str = "") -> bool:
        return self.pattern.startswith(text, self.pos) if text else self.pos < len(self.pattern)

    def error(self, message: str, at: int | None = None) -> RegexError:
        return RegexError(message, self.pos if at is None else at)

    def unsupported(self, message: str, at: int | None = None) -> Unsupported:
        return Unsupported(message, self.pos if at is None else at)

    def frozen(self) -> frozenset:
        return frozenset(self.flags)

    def skip_verbose(self) -> None:
        while "x" in self.flags and self.peek():
            char = self.pattern[self.pos]
            if char in " \t\n\r\f\v":
                self.pos += 1
            elif char == "#":
                while self.peek() and self.pattern[self.pos] != "\n":
                    self.pos += 1
            else:
                return

    # -- grammar

    def parse(self) -> Node:
        node = self.alternation()
        if self.peek():
            raise self.error("多了一個右括號 )，前面沒有對應的左括號")
        self.number_groups()
        for ref in self.backrefs:
            self.resolve(ref)
        return node

    def alternation(self) -> Node:
        start = self.pos
        saved = set(self.flags)
        branches = [self.sequence()]
        while self.peek("|"):
            if self.flags != saved:
                raise self.unsupported("在 | 的某一種裡切換旗標，後面幾種的規則")
            self.pos += 1
            branches.append(self.sequence())
        if len(branches) == 1:
            return branches[0]
        return Alternation(start, self.pos, branches)

    def sequence(self) -> Node:
        start = self.pos
        items: list[Node] = []
        while True:
            self.skip_verbose()
            if not self.peek() or self.peek("|") or self.peek(")"):
                break
            atom = self.atom()
            if atom is None:
                continue
            self.skip_verbose()
            items.append(self.quantifier(atom))
        return Sequence(start, self.pos, items)

    def quantifier(self, atom: Node) -> Node:
        start = self.pos
        spec = self.read_quantifier()
        if spec is None:
            return atom
        if isinstance(atom, (Anchor, InlineFlags, Comment)) or (
                isinstance(atom, Group) and atom.kind in (
                    "lookahead", "neglookahead", "lookbehind", "neglookbehind")):
            raise self.unsupported("對錨點、往前／往後看或旗標加上重複次數", start)
        low, high = spec
        mode = "greedy"
        if self.peek("?"):
            self.pos += 1
            mode = "lazy"
        elif self.peek("+"):
            if self.flavor != "python":
                raise self.error("重複次數後面不能再接 +（這個語言沒有「不退讓」的重複）")
            self.pos += 1
            mode = "possessive"
        node = Quantified(atom.start, self.pos, atom, low, high, mode)
        self.skip_verbose()
        if self.read_quantifier(peek_only=True) is not None:
            raise self.error("重複次數後面又接了一個重複次數")
        return node

    def read_quantifier(self, peek_only: bool = False) -> tuple[int, int | None] | None:
        if not self.peek():
            return None
        char = self.pattern[self.pos]
        simple = {"*": (0, None), "+": (1, None), "?": (0, 1)}
        if char in simple:
            if not peek_only:
                self.pos += 1
            return simple[char]
        if char != "{":
            return None
        end = self.pattern.find("}", self.pos)
        if end < 0:
            return None
        body = self.pattern[self.pos + 1:end]
        low_text, comma, high_text = body.partition(",")
        if not (low_text.isdigit() or (low_text == "" and comma and self.flavor == "python")):
            return None  # not a quantifier: a literal {
        if comma and high_text and not high_text.isdigit():
            return None
        if not low_text.isascii() or not high_text.isascii():
            return None
        low = int(low_text) if low_text else 0
        high = (int(high_text) if high_text else None) if comma else low
        if high is not None and low > high:
            raise self.error(f"重複次數的下限 {low} 大於上限 {high}")
        if not peek_only:
            self.pos = end + 1
        return low, high

    def atom(self) -> Node | None:
        start = self.pos
        char = self.pattern[self.pos]
        if char in "*+?" or (char == "{" and self.read_quantifier(peek_only=True) is not None):
            raise self.error("重複次數前面沒有可以重複的東西")
        if char == "(":
            return self.group()
        if char == "[":
            return self.char_class()
        if char == ".":
            self.pos += 1
            return Dot(start, self.pos, self.frozen())
        if char in "^$":
            self.pos += 1
            return Anchor(start, self.pos, char, self.frozen())
        if char == "\\":
            return self.escape()
        self.pos += 1
        if char == "]" and self.flavor == "js":
            raise self.unsupported("JavaScript 裡單獨的 ]", start)
        return Literal(start, self.pos, char, self.frozen())

    def escape(self) -> Node:
        start = self.pos
        self.pos += 1
        if not self.peek():
            raise self.error("樣式結尾多了一個反斜線 \\")
        char = self.pattern[self.pos]
        self.pos += 1
        flavor = self.flavor
        if char in "dDwWsS":
            return Shorthand(start, self.pos, char, self.frozen())
        if char in "bB":
            return Anchor(start, self.pos, char, self.frozen())
        if char in SIMPLE_ESCAPES:
            return Literal(start, self.pos, SIMPLE_ESCAPES[char], self.frozen())
        if char in "AZzG":
            if flavor == "js":
                return Literal(start, self.pos, char, self.frozen(),
                               note=f"JavaScript 裡 \\{char} 不是特殊符號，就是字母 {char}")
            if flavor == "python" and char in "zG":
                raise self.error(f"Python 的 re 不認得 \\{char}")
            return Anchor(start, self.pos, char, self.frozen())
        if char == "x":
            return Literal(start, self.pos, self.hex_digits(2, start), self.frozen())
        if char == "u":
            return Literal(start, self.pos, self.hex_digits(4, start), self.frozen())
        if char == "0" and not (self.peek() and self.pattern[self.pos].isdigit()):
            return Literal(start, self.pos, "\0", self.frozen())
        if char.isdigit():
            if self.peek() and self.pattern[self.pos].isdigit():
                raise self.unsupported("兩位數以上的反向參照或八進位字元")
            ref = Backref(start, self.pos, number=int(char), flags=self.frozen())
            self.backrefs.append(ref)
            return ref
        if char == "k" and flavor in ("dotnet", "js"):
            close = {"<": ">", "'": "'"}.get(self.pattern[self.pos] if self.peek() else "")
            if close is None or (close == "'" and flavor == "js"):
                raise self.unsupported("\\k 後面不是 <名稱>")
            end = self.pattern.find(close, self.pos + 1)
            if end < 0:
                raise self.error("\\k<名稱> 沒有結尾")
            name = self.pattern[self.pos + 1:end]
            self.pos = end + 1
            ref = Backref(start, self.pos, name=name, flags=self.frozen())
            self.backrefs.append(ref)
            return ref
        if char in "pP":
            if flavor == "dotnet":
                return self.property(start, char == "P")
            if flavor == "python":
                raise self.error("Python 的 re 不支援 \\p{…}")
            raise self.unsupported("JavaScript 沒有 u 旗標時的 \\p")
        if char.isascii() and char.isalpha():
            if flavor == "js":
                return Literal(start, self.pos, char, self.frozen(),
                               note=f"JavaScript 裡 \\{char} 不是特殊符號，就是字母 {char}")
            if char in "aecNgk":
                raise self.unsupported(f"\\{char}")
            raise self.error(f"不認得的跳脫字元 \\{char}")
        return Literal(start, self.pos, char, self.frozen())

    def hex_digits(self, count: int, start: int) -> str:
        digits = self.pattern[self.pos:self.pos + count]
        if len(digits) != count or any(d not in HEX for d in digits):
            if self.flavor == "js":
                raise self.unsupported("不完整的 \\x／\\u（JavaScript 會當成字母）", start)
            raise self.error("\\x 後面要接 2 個十六進位數字，\\u 要接 4 個", start)
        self.pos += count
        return chr(int(digits, 16))

    def property(self, start: int, negated: bool) -> Property:
        if not self.peek("{"):
            raise self.error("\\p 後面要接 {名稱}")
        end = self.pattern.find("}", self.pos)
        if end < 0:
            raise self.error("\\p{ 沒有結尾 }")
        name = self.pattern[self.pos + 1:end]
        self.pos = end + 1
        if name not in PROPERTIES:
            raise self.unsupported(f"Unicode 類別 {name}")
        return Property(start, self.pos, name, negated)

    def char_class(self) -> CharClass:
        start = self.pos
        self.pos += 1
        negated = self.peek("^")
        if negated:
            self.pos += 1
        if self.peek("]"):
            raise self.unsupported("字元集合一開頭就是 ]（各語言規則不同）")
        items: list[ClassItem] = []
        while True:
            if not self.peek():
                raise self.error("字元集合 [ 沒有結尾的 ]", start)
            if self.peek("]"):
                self.pos += 1
                break
            if self.flavor == "dotnet" and self.peek("-["):
                raise self.unsupported(".NET 的集合相減 -[…]")
            item_start = self.pos
            item = self.class_atom()
            if self.peek("-") and not self.pattern.startswith("-]", self.pos):
                if self.flavor == "dotnet" and self.peek("-["):
                    raise self.unsupported(".NET 的集合相減 -[…]")
                self.pos += 1
                high = self.class_atom()
                if item.kind != "char" or high.kind != "char":
                    raise self.unsupported("範圍的一端是 \\d、\\w 這類代號", item_start)
                if ord(item.low) > ord(high.low):
                    raise self.error(f"範圍 {item.low}-{high.low} 的開頭比結尾大", item_start)
                items.append(ClassItem("range", low=item.low, high=high.low))
            else:
                items.append(item)
        return CharClass(start, self.pos, negated, items, self.frozen())

    def class_atom(self) -> ClassItem:
        char = self.pattern[self.pos]
        if char != "\\":
            self.pos += 1
            return ClassItem("char", low=char)
        start = self.pos
        self.pos += 1
        if not self.peek():
            raise self.error("樣式結尾多了一個反斜線 \\")
        char = self.pattern[self.pos]
        self.pos += 1
        if char in "dDwWsS":
            return ClassItem("shorthand", letter=char)
        if char == "b":
            return ClassItem("char", low="\b")
        if char in SIMPLE_ESCAPES:
            return ClassItem("char", low=SIMPLE_ESCAPES[char])
        if char == "x":
            return ClassItem("char", low=self.hex_digits(2, start))
        if char == "u":
            return ClassItem("char", low=self.hex_digits(4, start))
        if char in "pP" and self.flavor == "dotnet":
            prop = self.property(start, char == "P")
            return ClassItem("property", name=prop.name, negated=prop.negated)
        if char.isascii() and char.isalnum():
            if self.flavor == "js" and char.isalpha() and char not in "cpPk":
                return ClassItem("char", low=char)
            raise self.unsupported(f"字元集合裡的 \\{char}", start)
        return ClassItem("char", low=char)

    def group(self) -> Node:
        start = self.pos
        self.pos += 1
        flavor = self.flavor
        if not self.peek("?"):
            kind = "noncapture" if "n" in self.flags else "capture"
            return self.group_body(start, kind)
        self.pos += 1
        if self.peek(":"):
            self.pos += 1
            return self.group_body(start, "noncapture")
        for prefix, kind in (("=", "lookahead"), ("!", "neglookahead"),
                             ("<=", "lookbehind"), ("<!", "neglookbehind")):
            if self.peek(prefix):
                self.pos += len(prefix)
                return self.group_body(start, kind)
        if self.peek(">"):
            if flavor == "js":
                raise self.error("JavaScript 沒有原子組 (?>…)")
            self.pos += 1
            return self.group_body(start, "atomic")
        if self.peek("#"):
            if flavor == "js":
                raise self.error("JavaScript 沒有 (?#…) 註解")
            end = self.pattern.find(")", self.pos)
            if end < 0:
                raise self.error("(?# 註解沒有結尾", start)
            text = self.pattern[self.pos + 1:end]
            self.pos = end + 1
            return Comment(start, self.pos, text)
        if self.peek("P<"):
            if flavor != "python":
                raise self.error("(?P<名稱>…) 是 Python 的寫法")
            self.pos += 2
            return self.named(start, ">")
        if self.peek("P="):
            if flavor != "python":
                raise self.error("(?P=名稱) 是 Python 的寫法")
            end = self.pattern.find(")", self.pos)
            if end < 0:
                raise self.error("(?P= 沒有結尾", start)
            ref = Backref(start, end + 1, name=self.pattern[self.pos + 2:end], flags=self.frozen())
            self.pos = end + 1
            self.backrefs.append(ref)
            return ref
        if self.peek("<"):
            if flavor == "python":
                raise self.error("Python 的 re 要寫成 (?P<名稱>…)")
            self.pos += 1
            return self.named(start, ">")
        if self.peek("'"):
            if flavor != "dotnet":
                raise self.error("(?'名稱'…) 是 .NET 的寫法")
            self.pos += 1
            return self.named(start, "'")
        if self.peek("("):
            raise self.unsupported("條件式 (?(…)…|…)")
        return self.flag_group(start)

    def named(self, start: int, close: str) -> Node:
        end = self.pattern.find(close, self.pos)
        if end < 0:
            raise self.error("名稱沒有結尾", start)
        name = self.pattern[self.pos:end]
        if not name:
            raise self.error("組的名稱是空的", start)
        if not (name.isascii() and (name[0].isalpha() or name[0] == "_")
                and all(c.isalnum() or c == "_" for c in name)):
            # Digits (.NET), $ (JavaScript), other scripts, balancing groups:
            # each language has its own rules, so no claim either way.
            raise self.unsupported(f"組名稱「{name}」", start)
        if name in self.names:
            raise self.unsupported("兩個組用同一個名稱", start)
        self.pos = end + 1
        return self.group_body(start, "named", name)

    def flag_group(self, start: int) -> Node:
        allowed = {"python": "aimsx", "dotnet": "imnsx", "js": ""}[self.flavor]
        if not allowed:
            raise self.error("JavaScript 不能在樣式裡改旗標", start)
        on = ""
        while self.peek() and self.pattern[self.pos] in allowed:
            on += self.pattern[self.pos]
            self.pos += 1
        off = ""
        if self.peek("-"):
            self.pos += 1
            while self.peek() and self.pattern[self.pos] in allowed:
                off += self.pattern[self.pos]
                self.pos += 1
            if self.flavor == "python" and (not off or "a" in off):
                raise self.error("(?-…) 後面要接可以關掉的旗標（i、m、s、x）", start)
        if self.peek() and self.pattern[self.pos] in "LuU" and self.flavor == "python":
            raise self.unsupported("旗標 L／u", start)
        if self.peek(":"):
            self.pos += 1
            if self.flavor == "python" and "x" in on + off:
                raise self.unsupported("在一組裡局部開關 x", start)
            return self.group_body(start, "scoped", flags_on=on, flags_off=off)
        if not self.peek(")") or not (on or off):
            raise self.error("看不懂的 (? 寫法", start)
        self.pos += 1
        if self.flavor == "python":
            if off or start != self.leading_end:
                raise self.error("Python 的全域旗標 (?…) 只能寫在樣式最前面", start)
            self.leading_end = self.pos
        elif "x" in on + off and start != 0:
            raise self.unsupported("樣式中途切換 x", start)
        self.flags |= set(on)
        self.flags -= set(off)
        return InlineFlags(start, self.pos, on, off)

    def group_body(self, start: int, kind: str, name: str = "",
                   flags_on: str = "", flags_off: str = "") -> Node:
        group = Group(start, start, kind, Sequence(start, start), name=name,
                      flags_on=flags_on, flags_off=flags_off)
        if kind in ("capture", "named"):
            self.groups.append(group)
            if name:
                self.names[name] = group
        saved = set(self.flags)
        self.flags = (self.flags | set(flags_on)) - set(flags_off)
        self.open.append(group)
        group.child = self.alternation()
        self.open.pop()
        self.flags = saved
        if not self.peek(")"):
            raise self.error("左括號 ( 沒有對應的右括號 )", start)
        self.pos += 1
        group.end = self.pos
        if kind in ("lookbehind", "neglookbehind") and self.flavor == "python":
            low, high = width(group.child)
            if low != high:
                raise self.error("Python 的往前看 (?<=…) 只能放固定長度的內容", start)
        return group

    def number_groups(self) -> None:
        if self.flavor == "dotnet":
            # .NET numbers the unnamed groups first, then the named ones.
            ordered = [g for g in self.groups if not g.name] + [g for g in self.groups if g.name]
        else:
            ordered = self.groups
        for number, group in enumerate(ordered, 1):
            group.number = number

    def resolve(self, ref: Backref) -> None:
        if ref.name:
            if self.flavor == "js" and not self.names:
                raise self.unsupported("JavaScript 沒有命名組時的 \\k", ref.start)
            group = self.names.get(ref.name)
            if group is None:
                raise self.error(f"沒有名稱叫「{ref.name}」的組", ref.start)
            ref.number = group.number
            if group.start > ref.start or group.end > ref.start:
                raise self.unsupported("參照尚未結束或寫在後面的組", ref.start)
            return
        matches = [g for g in self.groups if g.number == ref.number]
        if not matches:
            if self.flavor == "js":
                raise self.unsupported("JavaScript 會把參照不存在的組當成八進位字元", ref.start)
            raise self.error(f"沒有第 {ref.number} 組可以參照", ref.start)
        group = matches[0]
        if group.start > ref.start or group.end > ref.start:
            raise self.unsupported("參照尚未結束或寫在後面的組", ref.start)


def width(node: Node) -> tuple[int, int | None]:
    """Shortest and longest text a node can match (None: unbounded)."""
    if isinstance(node, (Literal, Shorthand, Property, Dot, CharClass)):
        return 1, 1
    if isinstance(node, (Anchor, InlineFlags, Comment)):
        return 0, 0
    if isinstance(node, Backref):
        raise Unsupported("往前看裡的反向參照", node.start)
    if isinstance(node, Group):
        if node.kind in ("lookahead", "neglookahead", "lookbehind", "neglookbehind"):
            return 0, 0
        return width(node.child)
    if isinstance(node, Quantified):
        low, high = width(node.child)
        top = None if node.high is None or high is None else high * node.high
        return low * node.low, top
    if isinstance(node, Sequence):
        lows, highs = zip(*(width(item) for item in node.items)) if node.items else ((0,), (0,))
        return sum(lows), None if None in highs else sum(highs)
    if isinstance(node, Alternation):
        spans = [width(b) for b in node.branches]
        lows = [s[0] for s in spans]
        highs = [s[1] for s in spans]
        return min(lows), None if None in highs else max(highs)
    raise TypeError(node)


def parse(pattern: str, flavor: str, flags: str = "") -> tuple[Node, list[Group]]:
    parser = Parser(pattern, flavor, flags)
    if flavor == "python":
        _python_leading_flags(parser)
    node = parser.parse()
    return node, sorted(parser.groups, key=lambda g: g.number)


def _python_leading_flags(parser: Parser) -> None:
    """Python's global (?aimsx) at the very start apply to the whole pattern,
    so verbose mode is known before anything else is read."""
    at = 0
    while parser.pattern.startswith("(?", at):
        i = at + 2
        while i < len(parser.pattern) and parser.pattern[i] in "aimsx":
            i += 1
        if i == at + 2 or parser.pattern[i:i + 1] != ")":
            return
        parser.flags |= set(parser.pattern[at + 2:i])
        at = i + 1


# ---------------------------------------------------------------- wording
# Every sentence that depends on the flavor is checked in the tests.

SHORTHAND = {
    ("d", "python"): "一個十進位數字：0–9，也包含全形數字（０–９）等所有 Unicode 十進位數字",
    ("d", "python-a"): "一個半形數字 0–9",
    ("d", "dotnet"): "一個十進位數字：0–9，也包含全形數字（０–９）等所有 Unicode 十進位數字",
    ("d", "js"): "一個半形數字 0–9（全形數字不算）",
    ("w", "python"): "一個文字字元：任何語言的字母（包含中文字）、數字，或底線 _",
    ("w", "python-a"): "一個半形英文字母、半形數字 0–9 或底線 _（中文字不算）",
    ("w", "dotnet"): "一個文字字元：任何語言的字母（包含中文字）、數字，或底線 _ 等連接符號",
    ("w", "js"): "一個半形英文字母、半形數字 0–9 或底線 _（中文字不算）",
    ("s", "python"): "一個空白字元：空格、Tab、換行、歸位等，也包含全形空格和不斷行空格",
    ("s", "python-a"): "一個半形空白字元：空格、Tab、換行、歸位、換頁、垂直定位（全形空格、不斷行空格不算）",
    ("s", "dotnet"): "一個空白字元：空格、Tab、換行、歸位等，也包含全形空格和不斷行空格",
    ("s", "js"): "一個空白字元：空格、Tab、換行、歸位等，也包含全形空格和不斷行空格",
}
EXACT = {
    ("d", "python"): "Unicode 類別 Nd 的字元",
    ("d", "python-a"): "[0-9]",
    ("d", "dotnet"): "\\p{Nd}",
    ("d", "js"): "[0-9]",
    ("w", "python"): "str.isalnum() 為真的字元，加上 _",
    ("w", "python-a"): "[a-zA-Z0-9_]",
    ("w", "dotnet"): "[\\p{L}\\p{Mn}\\p{Nd}\\p{Pc}]",
    ("w", "js"): "[A-Za-z0-9_]",
    ("s", "python"): "str.isspace() 為真的字元（也包含 \\x1c–\\x1f）",
    ("s", "python-a"): "[ \\t\\n\\r\\f\\v]",
    ("s", "dotnet"): "[\\f\\n\\r\\t\\v\\x85\\p{Z}]",
    ("s", "js"): "[\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]",
}
PROPERTIES = {
    "L": "任何語言的字母（包含中文字）", "Lu": "大寫字母", "Ll": "小寫字母",
    "N": "任何數字字元", "Nd": "十進位數字（包含全形數字）", "P": "標點符號",
    "S": "符號", "Z": "分隔字元（空格等）", "Zs": "空格類字元（包含全形空格）",
    "IsCJKUnifiedIdeographs": "中日韓統一表意文字區塊（U+4E00–U+9FFF，常用漢字）",
    "IsHiragana": "平假名區塊", "IsKatakana": "片假名區塊",
    "IsBasicLatin": "基本拉丁區塊（U+0000–U+007F，半形 ASCII）",
}
CONTROL_NAMES = {
    "\n": "換行字元（\\n）", "\r": "歸位字元（\\r）", "\t": "Tab 字元（\\t）",
    "\f": "換頁字元（\\f）", "\v": "垂直定位字元（\\v）", "\0": "NUL 字元（\\0）",
    "\b": "退格字元（\\b，在字元集合裡）", " ": "空格", "　": "全形空格（U+3000）",
}


def shorthand_key(letter: str, flavor: str, flags: frozenset) -> tuple[str, str]:
    base = letter.lower()
    if flavor == "python" and "a" in flags:
        return base, "python-a"
    return base, flavor


def describe_char(char: str) -> str:
    if char in CONTROL_NAMES:
        return CONTROL_NAMES[char]
    if not char.isprintable() or unicodedata.category(char) in ("Zs", "Cf"):
        return f"U+{ord(char):04X} 字元（{unicodedata.name(char, '無名稱')}）"
    return f"「{char}」"


def describe_text(text: str) -> str:
    if len(text) == 1:
        described = describe_char(text)
        return "字元" + described if described.startswith("「") else described
    if all(c.isprintable() and c not in CONTROL_NAMES for c in text):
        return f"文字「{text}」"
    return "依序是：" + "、".join(describe_char(c) for c in text)


def case_note(flags: frozenset, text: str = "") -> str:
    """Only where it changes something: some letter has another case."""
    cased = any(c.lower() != c.upper() for c in text) if text else True
    return "（不分大小寫）" if "i" in flags and cased else ""


def describe_shorthand(letter: str, flavor: str, flags: frozenset) -> str:
    key = shorthand_key(letter, flavor, flags)
    text = SHORTHAND[key]
    if letter.isupper():
        text = "任何一個字元，但不能是" + text.removeprefix("一個")
    return f"{text}。精確定義：{'不屬於 ' if letter.isupper() else ''}{EXACT[key]}"


def describe_dot(flavor: str, flags: frozenset) -> str:
    if "s" in flags:
        text = "任何一個字元（包含換行）"
    elif flavor == "js":
        text = "任何一個字元，但換行（\\n）、歸位（\\r）、行分隔符（U+2028）、段落分隔符（U+2029）除外"
    else:
        text = "任何一個字元，但換行字元（\\n）除外"
    return text


def describe_anchor(kind: str, flavor: str, flags: frozenset) -> str:
    multiline = "m" in flags
    if kind == "^":
        if not multiline:
            return "整段文字的開頭"
        if flavor == "js":
            return "每一行的開頭：整段開頭，或換行（\\n）、歸位（\\r）、U+2028、U+2029 之後"
        return "每一行的開頭：整段開頭，或換行字元（\\n）之後"
    if kind == "$":
        if not multiline:
            if flavor == "js":
                return "整段文字的結尾"
            return "整段文字的結尾；如果最後一個字元是換行（\\n），也可以在那個換行之前"
        if flavor == "js":
            return "每一行的結尾：整段結尾，或換行（\\n）、歸位（\\r）、U+2028、U+2029 之前"
        return "每一行的結尾：整段結尾，或換行字元（\\n）之前"
    if kind == "A":
        return "整段文字的開頭（不受多行模式影響）"
    if kind == "Z":
        if flavor == "python":
            return "整段文字的最結尾（不受多行模式影響；結尾換行之前不算）"
        return "整段文字的結尾，或結尾換行（\\n）之前（不受多行模式影響）"
    if kind == "z":
        return "整段文字的最結尾（不受多行模式影響）"
    if kind == "G":
        return "上一次比對結束的位置（第一次比對時就是開始搜尋的位置）"
    word = SHORTHAND[shorthand_key("w", flavor, flags)].removeprefix("一個")
    if kind == "b":
        return f"文字邊界：一邊是文字字元、另一邊不是（或是開頭／結尾）。文字字元指{word}"
    return f"不是文字邊界的位置（兩邊都是文字字元，或兩邊都不是）。文字字元指{word}"


def describe_class(node: CharClass, flavor: str) -> str:
    parts = []
    for item in node.items:
        if item.kind == "char":
            parts.append(describe_char(item.low))
        elif item.kind == "range":
            parts.append(f"{describe_char(item.low)} 到 {describe_char(item.high)}"
                         f"（U+{ord(item.low):04X}–U+{ord(item.high):04X}）")
        elif item.kind == "shorthand":
            parts.append(describe_shorthand(item.letter, flavor, node.flags).split("。")[0])
        else:
            parts.append(("不是" if item.negated else "") + PROPERTIES[item.name])
    body = "；".join(parts)
    head = "任何一個字元，但不能是以下任一：" if node.negated else "以下任一個字元："
    letters = "".join(i.low + i.high for i in node.items if i.kind in ("char", "range"))
    return head + body + case_note(node.flags, letters)


def describe_count(node: Quantified) -> str:
    low, high = node.low, node.high
    if high == low:
        count = f"剛好 {low} 次"
    elif high is None:
        count = {0: "0 次以上（可以完全沒有）", 1: "1 次以上"}.get(low, f"至少 {low} 次")
    elif low == 0 and high == 1:
        count = "0 或 1 次（可有可無）"
    elif low == 0:
        count = f"最多 {high} 次（可以完全沒有）"
    else:
        count = f"{low} 到 {high} 次"
    if high == low:
        return count
    mode = {"greedy": "盡量多，比不下去時才一次次退讓",
            "lazy": "盡量少，不夠時才一次次增加",
            "possessive": "盡量多，而且不退讓"}[node.mode]
    return f"{count}，{mode}"


GROUP_HEADS = {
    "noncapture": "一組（只用來分組，不擷取）",
    "lookahead": "往後看（不消耗文字）：接下來必須是",
    "neglookahead": "往後看（不消耗文字）：接下來不能是",
    "lookbehind": "往前看（不消耗文字）：前面必須剛好是",
    "neglookbehind": "往前看（不消耗文字）：前面不能是",
    "atomic": "原子組（一旦比對成功就不再回頭重試）",
}


@dataclass
class Line:
    depth: int
    source: str
    text: str


def lines_for(node: Node, pattern: str, flavor: str, depth: int) -> list[Line]:
    src = pattern[node.start:node.end]
    if isinstance(node, Sequence):
        out: list[Line] = []
        run: list[Literal] = []

        def flush() -> None:
            if run:
                text = "".join(lit.char for lit in run)
                notes = "；".join(dict.fromkeys(lit.note for lit in run if lit.note))
                out.append(Line(depth, pattern[run[0].start:run[-1].end],
                                describe_text(text) + case_note(run[0].flags, text)
                                + (f"（{notes}）" if notes else "")))
                run.clear()

        for item in node.items:
            if isinstance(item, Literal) and (not run or run[-1].flags == item.flags):
                run.append(item)
                continue
            flush()
            if isinstance(item, Literal):
                run.append(item)
            else:
                out += lines_for(item, pattern, flavor, depth)
        flush()
        if not node.items:
            out.append(Line(depth, "", "（空的：不比對任何文字，直接成立）"))
        return out
    if isinstance(node, Alternation):
        out = [Line(depth, src, "以下任一種（從左到右依序嘗試，先成功的就採用）：")]
        for number, branch in enumerate(node.branches, 1):
            out.append(Line(depth + 1, pattern[branch.start:branch.end], f"第 {number} 種："))
            out += lines_for(branch, pattern, flavor, depth + 2)
        return out
    if isinstance(node, Quantified):
        child = node.child
        if isinstance(child, ATOMS):
            return [Line(depth, src, f"{atom_text(child, flavor)}。次數：{describe_count(node)}")]
        return [Line(depth, src, f"下面這部分的次數：{describe_count(node)}")] + lines_for(
            child, pattern, flavor, depth + 1)
    if isinstance(node, Group):
        if node.kind == "capture":
            head = f"第 {node.number} 組（擷取下來，之後可用編號 {node.number} 取用），內容是"
        elif node.kind == "named":
            head = f"第 {node.number} 組，名稱「{node.name}」（擷取下來），內容是"
        elif node.kind == "scoped":
            changes = [FLAG_NAMES[f] for f in node.flags_on] + [
                "取消" + FLAG_NAMES[f] for f in node.flags_off]
            head = f"一組（這組裡改成：{'、'.join(changes)}），內容是"
        else:
            head = GROUP_HEADS[node.kind]
        return [Line(depth, src, head + "：")] + lines_for(node.child, pattern, flavor, depth + 1)
    if isinstance(node, InlineFlags):
        changes = [FLAG_NAMES[f] for f in node.on] + ["取消" + FLAG_NAMES[f] for f in node.off]
        return [Line(depth, src, "從這裡開始改成：" + "、".join(changes))]
    if isinstance(node, Comment):
        return [Line(depth, src, f"註解，不影響比對：「{node.text}」")]
    if isinstance(node, Anchor):
        return [Line(depth, src, "位置：" + describe_anchor(node.kind, flavor, node.flags))]
    return [Line(depth, src, atom_text(node, flavor))]


def atom_text(node: Node, flavor: str) -> str:
    if isinstance(node, Literal):
        note = f"（{node.note}）" if node.note else ""
        return describe_text(node.char) + case_note(node.flags, node.char) + note
    if isinstance(node, Shorthand):
        return describe_shorthand(node.letter, flavor, node.flags)
    if isinstance(node, Property):
        return ("任何一個不是「" if node.negated else "一個「") + PROPERTIES[node.name] + "」的字元"
    if isinstance(node, Dot):
        return describe_dot(flavor, node.flags)
    if isinstance(node, CharClass):
        return describe_class(node, flavor)
    if isinstance(node, Backref):
        label = f"第 {node.number} 組" + (f"（{node.name}）" if node.name else "")
        return f"和{label}剛才比對到的文字一模一樣{case_note(node.flags)}"
    raise TypeError(node)


@dataclass
class Translation:
    pattern: str
    flavor: str
    flags: str
    lines: list[Line]
    groups: list[tuple[int, str]]
    notes: list[str]

    def text(self) -> str:
        head = [f"正規表示式：{self.pattern}", f"規則：{FLAVOR_NAMES[self.flavor]}"]
        if self.flags:
            head.append("旗標：" + "、".join(FLAG_NAMES[f] for f in self.flags))
        body, counters = [], [0] * 64
        for line in self.lines:
            counters[line.depth] += 1
            for deeper in range(line.depth + 1, len(counters)):
                counters[deeper] = 0
            number = ".".join(str(n) for n in counters[:line.depth + 1])
            source = f"`{line.source}`：" if line.source else ""
            body.append(f"{'  ' * line.depth}{number}. {source}{line.text}")
        groups = "、".join(f"第 {n} 組" + (f"（{name}）" if name else "") for n, name in self.groups)
        tail = [f"擷取的組：{groups}" if groups else "擷取的組：沒有"] + self.notes
        return "\n".join(head + ["逐項說明："] + body + tail)


def translate(pattern: str, flavor: str, flags: str = "") -> Translation:
    node, groups = parse(pattern, flavor, flags)
    notes = []
    if flavor in ("dotnet", "js"):
        notes.append("注意：這裡的「一個字元」是一個 UTF-16 單位，大多數表情符號和罕用字算兩個字元。")
    if flavor == "dotnet" and any(g.name for g in groups) and any(not g.name for g in groups):
        notes.append("注意：.NET 先替沒有名稱的組編號，有名稱的組排在後面。")
    return Translation(pattern, flavor, "".join(sorted(set(flags))),
                       lines_for(node, pattern, flavor, 0),
                       [(g.number, g.name) for g in groups], notes)
