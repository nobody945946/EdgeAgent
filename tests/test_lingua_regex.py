"""The regex translator against the real engines.

Two kinds of checks:
- parity: for every pattern in CORPUS and every flavor, what the parser says
  (invalid / valid with these groups / not covered) must agree with the engine;
- claims: every flavor-dependent sentence of edge_agent/lingua/regex*.py has
  cases here that the engine must answer the way the sentence says.
A missing engine (no pwsh or node) skips that flavor; the laptop has both.
"""

from __future__ import annotations

import pytest

from edge_agent.lingua import regex, regex_parser, regex_syntax
from edge_agent.lingua.engines import RUNNERS

CORPUS = [
    "abc", "a|b", "(a)(b)", "(?:a)", "a*", "a+?", "a{2}", "a{2,}", "a{2,3}", "a{,3}", "a{,}",
    "a{3,2}", "a**", "a*+", "a?+", "a{2}{3}", "*a", "{2}", "x{a}", "x{2", "a{1,2,3}",
    "[abc]", "[^a-z]", "[z-a]", "[\\d-a]", "[a-]", "[-a]", "[]a]", "[a", "(a", "a)", "\\",
    "\\d\\w\\s\\D\\W\\S", "\\bx\\B", "\\Ax\\Z", "\\z", "\\G", "\\p{L}", "\\P{Lu}", "\\p{Foo}",
    "\\k<n>(?<n>a)", "(?<n>a)\\k<n>", "(?P<n>a)(?P=n)", "(?'n'a)", "(?<n>a)(b)",
    "(a)(?<n>b)(c)", "(?<1>a)", "(?<$x>a)", "(?=a)", "(?!a)", "(?<=ab)", "(?<=a|bc)",
    "(?<!a+)", "(?>a)", "(?#c)a", "(?i)a", "a(?i)b", "(?i:a)b", "(?-i:a)", "(?x) a b",
    "(?im)^a$", "(?s).", "(?a)\\w", "(?L)a", "\\1(a)", "(a)\\1", "(a)\\2", "(a)\\10",
    "\\0", "\\x41", "\\x4", "\\u0041", "\\u004", "\\q", "\\e", "\\a", "\\.", "\\/", "(?)",
    "(?P<n>a)(?P<n>b)", "(?<n>a)|(?<n>b)", "[\\b]", "[\\A]", "[a-\\d]", "x{2}?", "x{2}+",
    "(?(1)a|b)", "(a)|\\1", "a]", "a}", "(?<=a\\d{2})", "(?<=\\b)", ".", "^$", "", "(|a)",
    "a||b", "^(?P<year>\\d{4})-(\\d{2})$", "^(?<year>\\d{4})-(\\d{2})$", "(?i)(?m)a",
    "[\\w.-]+@[\\w-]+\\.[a-z]{2,}", "^\\s*#.*$", "(\\d+)(?:\\.(\\d+))?", "(?<!\\d)\\d{3}(?!\\d)",
    "[\\u4e00-\\u9fff]+", "\\p{IsCJKUnifiedIdeographs}+", "a{0}", "(a|b)*?c", "(?:a+)+$",
    "[\\s\\S]", "[^\\w\\s]", "\\x", "\\u", "(?P<n>", "(?<n>a))", "[\\x41-\\x5a]",
    "(?i)[a-z]", "a(?-i)b", "(?n)(a)(?<x>b)", "\\k'n'(?'n'a)", "(?'n'a)\\k'n'",
]
FLAGGED = [("a b # c", "x"), ("^a$", "m"), (".", "s"), ("A", "i"), ("\\w", "a"),
           ("(a)(?<x>b)", "n"), ("[ a]", "x")]


def cases_for(flavor: str) -> list[tuple[str, str]]:
    allowed = set(regex_syntax.FLAGS[flavor])
    return [(p, "") for p in CORPUS] + [(p, f) for p, f in FLAGGED if set(f) <= allowed]


def engine(flavor: str, cases: list[dict]) -> list[dict]:
    results = RUNNERS[flavor](cases)
    if results is None:
        pytest.skip(f"no {flavor} engine here")
    return results


@pytest.mark.parametrize("flavor", regex_syntax.FLAVORS)
def test_parser_agrees_with_engine(flavor: str) -> None:
    cases = cases_for(flavor)
    results = engine(flavor, [{"pattern": p, "flags": f, "inputs": []} for p, f in cases])
    wrong, covered = [], 0
    for (pattern, flags), result in zip(cases, results):
        try:
            _, groups = regex_parser.parse(pattern, flavor, flags)
        except regex_syntax.Unsupported:
            continue
        except regex_syntax.RegexError as exc:
            covered += 1
            if result["ok"]:
                wrong.append(f"{pattern!r}/{flags}: parser says invalid ({exc}), engine accepts")
            continue
        covered += 1
        if not result["ok"]:
            wrong.append(f"{pattern!r}/{flags}: parser accepts, engine says {result['error']}")
            continue
        mine = [[g.number, g.name] for g in groups]
        if flavor == "js":
            if len(mine) != result["count"] or sorted(n for _, n in mine if n) != sorted(result["names"]):
                wrong.append(f"{pattern!r}/{flags}: groups {mine} vs engine {result}")
        elif mine != result["groups"]:
            wrong.append(f"{pattern!r}/{flags}: groups {mine} vs engine {result['groups']}")
    assert not wrong, "\n".join(wrong)
    assert covered >= len(cases) * 0.75, f"only {covered}/{len(cases)} covered"


# (flavor, pattern, flags, input, the engine must say) for each claim.
CLAIMS = [
    # \d: Unicode digits in Python and .NET, ASCII only in JavaScript.
    *[(f, "^\\d$", "", s, f != "js") for f in ("python", "dotnet", "js") for s in ("５", "٣")],
    ("python", "^\\d$", "a", "５", False),
    # \w: letters of every script, but only ASCII in JavaScript.
    *[(f, "^\\w$", "", s, f != "js") for f in ("python", "dotnet", "js") for s in ("中", "é")],
    *[(f, "^\\w$", "", "_", True) for f in regex_syntax.FLAVORS],
    ("dotnet", "^\\w$", "", "‿", True), ("python", "^\\w$", "", "‿", False),
    ("dotnet", "^\\w$", "", "́", True), ("python", "^\\w$", "", "́", False),
    ("python", "^\\w$", "a", "中", False),
    # \s: full-width and no-break space everywhere; the exact sets differ.
    *[(f, "^\\s$", "", s, True) for f in regex_syntax.FLAVORS for s in ("　", " ", "\t")],
    ("python", "^\\s$", "", "\x1c", True), ("dotnet", "^\\s$", "", "\x1c", False),
    ("js", "^\\s$", "", "\x1c", False),
    ("python", "^\\s$", "", "\x85", True), ("dotnet", "^\\s$", "", "\x85", True),
    ("js", "^\\s$", "", "\x85", False),
    ("js", "^\\s$", "", "﻿", True), ("python", "^\\s$", "", "﻿", False),
    ("dotnet", "^\\s$", "", "﻿", False),
    ("python", "^\\s$", "a", "　", False), ("python", "^\\s$", "a", " ", False),
    # . : everything but \n; JavaScript also stops at \r, U+2028, U+2029.
    *[(f, "^.$", "", "\n", False) for f in regex_syntax.FLAVORS],
    *[(f, "^.$", "s", "\n", True) for f in regex_syntax.FLAVORS],
    *[(f, "^.$", "", s, f != "js") for f in regex_syntax.FLAVORS for s in ("\r", " ", " ")],
    # One "character" is one UTF-16 unit in .NET and JavaScript.
    *[(f, "^.$", "", "\U0001F600", f == "python") for f in regex_syntax.FLAVORS],
    # $ before a final newline (not in JavaScript); multiline line starts.
    *[(f, "a$", "", "a\n", f != "js") for f in regex_syntax.FLAVORS],
    *[(f, "a$", "", "a\nb", False) for f in regex_syntax.FLAVORS],
    *[(f, "^b", "m", "a\nb", True) for f in regex_syntax.FLAVORS],
    *[(f, "^b", "m", "a\rb", f == "js") for f in regex_syntax.FLAVORS],
    *[(f, "a$", "m", "a\rb", f == "js") for f in regex_syntax.FLAVORS],
    *[(f, "^b", "", "a\nb", False) for f in regex_syntax.FLAVORS],
    # \Z: the very end in Python, also before a final \n in .NET; \z in .NET.
    ("python", "a\\Z", "", "a\n", False), ("dotnet", "a\\Z", "", "a\n", True),
    ("dotnet", "a\\z", "", "a\n", False), ("dotnet", "a\\z", "", "a", True),
    *[(f, "\\Aa", "m", "b\na", False) for f in ("python", "dotnet")],
    # In JavaScript \A, \Z, \z, \G are just letters.
    *[("js", f"^\\{c}$", "", c, True) for c in "AZzG"],
    # \b follows \w: Chinese characters are word characters except in JavaScript.
    *[(f, "\\b中", "", "a 中", f != "js") for f in regex_syntax.FLAVORS],
    # {,n} is a count in Python and plain text elsewhere.
    ("python", "^a{,3}$", "", "aaa", True), ("python", "^a{,3}$", "", "a{,3}", False),
    *[(f, "^a{,3}$", "", "a{,3}", True) for f in ("dotnet", "js")],
    # .NET numbers named groups after the unnamed ones.
    ("dotnet", "^(?<y>a)(b)\\1$", "", "abb", True), ("dotnet", "^(?<y>a)(b)\\1$", "", "aba", False),
    ("python", "^(?P<y>a)(b)\\1$", "", "aba", True),
    ("js", "^(?<y>a)(b)\\1$", "", "aba", True),
    # Possessive counts never give back (Python).
    ("python", "^a*+a$", "", "aaa", False), ("python", "^a*a$", "", "aaa", True),
    # Explicit capture (.NET n): unnamed groups do not count.
    ("dotnet", "^(a)(?<x>b)\\1$", "n", "abb", True),
    # x: spaces in the pattern are ignored, but not inside [ ].
    *[(f, "^a b$", "x", "ab", True) for f in ("python", "dotnet")],
    ("python", "^[ a]$", "x", " ", True),
]


@pytest.mark.parametrize("flavor", regex_syntax.FLAVORS)
def test_claims_hold_in_engine(flavor: str) -> None:
    claims = [c for c in CLAIMS if c[0] == flavor]
    results = engine(flavor, [{"pattern": p, "flags": f, "inputs": [s]} for _, p, f, s, _ in claims])
    wrong = [f"{p!r}/{f} on {s!r}: engine {r.get('matches', r)}, claim {want}"
             for (_, p, f, s, want), r in zip(claims, results)
             if not r["ok"] or r["matches"] != [want]]
    assert not wrong, "\n".join(wrong)


def text(pattern: str, flavor: str, flags: str = "") -> str:
    return regex.translate(pattern, flavor, flags).text()


def test_wording_states_the_claims() -> None:
    assert "全形數字不算" in text("\\d", "js")
    assert "全形數字" in text("\\d", "python") and "全形數字不算" not in text("\\d", "python")
    assert "中文字不算" in text("\\w", "js") and "包含中文字" in text("\\w", "dotnet")
    assert "中文字不算" in text("\\w", "python", "a")
    assert "U+2028" in text(".", "js") and "U+2028" not in text(".", "python")
    assert "那個換行之前" in text("a$", "python") and "換行之前" not in text("a$", "js")
    assert "結尾換行之前不算" in text("\\Z", "python") and "結尾換行（\\n）之前" in text("\\Z", "dotnet")
    assert "就是字母 Z" in text("\\Z", "js")
    assert "UTF-16" in text(".", "dotnet") and "UTF-16" not in text(".", "python")
    assert "第 2 組，名稱「y」" in text("(?<y>a)(b)", "dotnet")
    assert "第 1 組，名稱「y」" in text("(?<y>a)(b)", "js")
    assert "最多 3 次" in text("a{,3}", "python") and "文字「a{,3}」" in text("a{,3}", "dotnet")
    assert "而且不退讓" in text("a*+", "python")
    assert "（不分大小寫）" in text("(?i)ab", "python") and "不分大小寫" not in text("(?i)\\.", "python").split("逐項說明")[1].split("\n", 2)[2]


def test_refuses_instead_of_guessing() -> None:
    for pattern, flavor in [("[]a]", "python"), ("(?<1>a)", "dotnet"), ("\\p{L}", "js"),
                            ("(?(1)a|b)", "python"), ("[a-[b]]", "dotnet"), ("\\10", "python")]:
        with pytest.raises(regex_syntax.Unsupported):
            regex_parser.parse(pattern, flavor)


def test_errors_point_at_the_problem() -> None:
    with pytest.raises(regex_syntax.RegexError) as caught:
        regex_parser.parse("ab(c", "python")
    assert caught.value.position == 2
    assert "第 3 個字元" in str(caught.value)
