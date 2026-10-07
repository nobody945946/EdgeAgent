"""The fixed Chinese for each part of a regular expression, per flavor.

Every sentence that depends on the flavor is checked against the real
engines in tests/test_lingua_regex.py.
"""

from __future__ import annotations

import unicodedata

from edge_agent.lingua.regex_syntax import CharClass, Quantified

FLAVOR_NAMES = {
    "python": "Python 的 re 模組",
    "dotnet": ".NET（PowerShell 的 -match、-replace、[regex]）",
    "js": "JavaScript（沒有 u／v 旗標）",
}
FLAG_NAMES = {
    "i": "不分大小寫",
    "m": "多行模式（^ 和 $ 也對每一行的開頭和結尾成立）",
    "s": "點號 . 也比對換行",
    "x": "忽略樣式裡的空白和 # 註解",
    "a": "\\d、\\w、\\s、\\b 只認半形 ASCII",
    "n": "沒有名稱的括號不擷取",
}

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
