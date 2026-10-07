"""Regular expressions to plain Traditional Chinese, deterministically.

Three flavors, because the same pattern means different things:
python (Python `re`), dotnet (.NET: PowerShell -match, [regex]) and js
(JavaScript without the u/v flags). regex_parser reads the pattern,
regex_wording holds the fixed sentences, and this module lays them out as
numbered lines. Every flavor-dependent sentence is checked against the real
engines in tests/test_lingua_regex.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from edge_agent.lingua.regex_parser import parse
from edge_agent.lingua.regex_syntax import (
    ATOMS,
    Alternation,
    Anchor,
    Backref,
    CharClass,
    Comment,
    Dot,
    Group,
    InlineFlags,
    Literal,
    Node,
    Property,
    Quantified,
    Sequence,
    Shorthand,
)
from edge_agent.lingua.regex_wording import (
    FLAG_NAMES,
    FLAVOR_NAMES,
    GROUP_HEADS,
    PROPERTIES,
    case_note,
    describe_anchor,
    describe_class,
    describe_count,
    describe_dot,
    describe_shorthand,
    describe_text,
)


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
