"""Regular expression text to the tree of regex_syntax, by each flavor's own rules.

What the parser is not sure about it refuses (`Unsupported`) rather than
guess; what the engine itself would reject it reports (`RegexError`).
"""

from __future__ import annotations

from edge_agent.lingua.regex_syntax import (
    FLAGS,
    FLAVORS,
    Alternation,
    Anchor,
    Backref,
    CharClass,
    ClassItem,
    Comment,
    Dot,
    Group,
    InlineFlags,
    Literal,
    Node,
    Property,
    Quantified,
    RegexError,
    Sequence,
    Shorthand,
    Unsupported,
    width,
)
from edge_agent.lingua.regex_wording import PROPERTIES

SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v"}
HEX = "0123456789abcdefABCDEF"


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
