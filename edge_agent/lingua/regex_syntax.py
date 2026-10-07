"""What a regular expression is made of: flavors, flags, errors and the parsed tree.

A pattern is read into these nodes once; the wording is chosen from them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FLAVORS = ("python", "dotnet", "js")
# Flags a caller may pass, per flavor (letters as each language spells them).
FLAGS = {"python": "aimsx", "dotnet": "imnsx", "js": "ims"}


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
