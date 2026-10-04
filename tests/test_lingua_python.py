"""The Python translator: every behaviour its Chinese states, run by the
same Python (3.12) in a scratch folder. The Windows default encoding claim
(cp950 on Traditional Chinese Windows) runs on the laptop only.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap

import pytest

from edge_agent.lingua import python as lingua_python

# Each snippet must print True; each backs a sentence in python.py.
CLAIMS = {
    "round halves to even": "print(round(2.5) == 2 and round(3.5) == 4 and round(-2.5) == -2)",
    "round with digits and float error": "print(round(2.675, 2) == 2.67)",
    "int cuts toward zero": "print(int(-2.7) == -2 and int(2.7) == 2)",
    "/ always gives a float": "print(4 / 2 == 2.0 and type(4 / 2) is float and 5 / 2 == 2.5)",
    "// floors": "print(7 // 2 == 3 and -7 // 2 == -4)",
    "% follows the divisor": "print(-7 % 3 == 2 and 7 % -3 == -2)",
    "sorted makes a new list": "a = [3, 1]; b = sorted(a); print(a == [3, 1] and b == [1, 3])",
    "list.sort returns None": "a = [3, 1]; r = a.sort(); print(r is None and a == [1, 3])",
    "split on whitespace": "print(' a  b \\n'.split() == ['a', 'b'])",
    "split on a separator keeps empties": "print('a,,b'.split(',') == ['a', '', 'b'])",
    "strip and replace give new strings": "s = ' x '; t = s.strip(); print(s == ' x ' and t == 'x' and 'aaa'.replace('a', 'b') == 'bbb')",
    "dict.get gives None": "print({}.get('k') is None and {}.get('k', 0) == 0)",
    "zip stops at the shortest": "print(list(zip([1, 2, 3], [4])) == [(1, 4)])",
    "enumerate counts from 0": "print(list(enumerate('ab')) == [(0, 'a'), (1, 'b')])",
    "range excludes the end": "print(list(range(1, 4)) == [1, 2, 3] and len(range(3)) == 3 and list(range(0, 7, 3)) == [0, 3, 6])",
    "a mutable default is shared": "def f(x=[]):\n    x.append(1)\n    return x\nf()\nprint(f() == [1, 1])",
    "b = a shares the object": "a = []; b = a; b.append(1); print(a == [1])",
    "b = c = [] shares one list": "b = c = []; b.append(1); print(c == [1])",
    "+= changes a list in place": "a = [1]; c = a; a += [2]; print(c == [1, 2])",
    "and/or give an operand": "print((0 or 'x') == 'x' and ('' and 5) == '' and (1 and 2) == 2 and (0 or '') == '')",
    "chained comparison": "x = 2; print((1 < x < 3) and not (1 < x > 3))",
    "slices exclude the end": "a = [0, 1, 2, 3]; print(a[1:3] == [1, 2] and a[:2] == [0, 1] and a[::2] == [0, 2])",
    "negative index counts from the end": "print([0, 1, 2][-1] == 2)",
    "is compares identity": "print([1] == [1] and not ([1] is [1]))",
    "falsy values": "print(not any([0, '', [], None, {}]) and bool([0]))",
    "any of empty is False, all of empty is True": "print(any([]) is False and all([]) is True)",
    "print separates with a space and ends with a newline":
        "import io, contextlib\nf = io.StringIO()\nwith contextlib.redirect_stdout(f):\n    print('a', 'b')\nprint(f.getvalue() == 'a b\\n')",
    "open w empties the file": "open('t', 'w').write('abc'); open('t', 'w').close(); print(open('t').read() == '')",
    "open a appends": "open('t', 'w').write('a'); open('t', 'a').write('b'); print(open('t').read() == 'ab')",
    "open x refuses an existing file":
        "open('t', 'w').close()\ntry:\n    open('t', 'x')\n    print(False)\nexcept FileExistsError:\n    print(True)",
    "open r refuses a missing file":
        "try:\n    open('missing')\n    print(False)\nexcept FileNotFoundError:\n    print(True)",
    "readlines keeps newlines": "open('t', 'w').write('a\\nb\\n'); print(open('t').readlines() == ['a\\n', 'b\\n'])",
    "write adds no newline": "f = open('t', 'w'); f.write('a'); f.write('b'); f.close(); print(open('t').read() == 'ab')",
    "with closes even on error":
        "f = None\ntry:\n    with open('t', 'w') as f:\n        raise ValueError\nexcept ValueError:\n    pass\nprint(f.closed)",
    "map, filter, reversed and generators run once":
        "m = map(str, [1]); list(m); g = (x for x in [1]); list(g); r = reversed([1]); list(r)\n"
        "print(list(m) == [] and list(g) == [] and list(r) == [])",
    "set drops duplicates": "print(len({1, 1, 2}) == 2)",
    "for-else runs without break": "out = []\nfor i in []:\n    break\nelse:\n    out.append(1)\nprint(out == [1])",
    "while-else skipped on break": "out = []\nwhile True:\n    break\nelse:\n    out.append(1)\nprint(out == [])",
    "remove takes the first and fails when missing":
        "a = [1, 2, 1]; a.remove(1)\ntry:\n    a.remove(9)\nexcept ValueError:\n    print(a == [2, 1])",
    "pop takes the last by default": "a = [1, 2]; print(a.pop() == 2 and a == [1])",
    "update overwrites keys": "d = {'a': 1}; d.update({'a': 2, 'b': 3}); print(d == {'a': 2, 'b': 3})",
    "next without default stops":
        "try:\n    next(iter([]))\nexcept StopIteration:\n    print(True)",
    "a bare except also catches KeyboardInterrupt":
        "try:\n    raise KeyboardInterrupt\nexcept:\n    print(True)",
    "except Exception does not catch KeyboardInterrupt":
        "try:\n    try:\n        raise KeyboardInterrupt\n    except Exception:\n        print(False)\nexcept KeyboardInterrupt:\n    print(True)",
    "join puts the separator between": "print('-'.join(['a', 'b']) == 'a-b')",
    "+ joins text and lists, * repeats text": "print('a' + 'b' == 'ab' and [1] + [2] == [1, 2] and 'a' * 3 == 'aaa')",
}


def run(code: str, *flags: str) -> str:
    with tempfile.TemporaryDirectory() as folder:
        done = subprocess.run([sys.executable, *flags, "-c", code], capture_output=True, text=True,
                              cwd=folder, timeout=60, check=False)
    return done.stdout.strip() or done.stderr.strip()


def test_claims_hold_in_python() -> None:
    failed = [f"{claim}: {out}" for claim, code in CLAIMS.items() if (out := run(code)).splitlines()[-1:] != ["True"]]
    assert not failed, "\n".join(failed)


def test_assert_is_skipped_with_O() -> None:
    assert run("assert False\nprint('ran')", "-O") == "ran"


@pytest.mark.skipif(sys.platform != "win32", reason="the default encoding claim is about Windows")
def test_windows_default_encoding_is_not_utf8() -> None:
    import locale

    assert locale.getpreferredencoding(False).lower() != "utf-8"


def text(code: str) -> str:
    return lingua_python.translate(textwrap.dedent(code)).text()


def test_wording_states_the_traps() -> None:
    assert "round(2.5) 是 2" in text("x = round(y)")
    assert "round(2.675, 2) 是 2.67" in text("x = round(y, 2)")
    assert "-7 // 2 是 -4" in text("x = a // b") and "-7 % 3 是 2" in text("x = a % b")
    assert "結果一定是浮點數" in text("x = a / b")
    assert "不是複製" in text("b = a") and "都指向同一個物件" in text("b = c = []")
    assert "只在定義函式時建立一次" in text("def f(x=[]):\n    return x")
    assert "回傳 None" in text("a.sort()") and "原本的不變" in text("b = sorted(a)")
    assert "會先被清空" in text("f = open(p, 'w')") and "cp950" in text("f = open(p)")
    assert "cp950" not in text("f = open(p, encoding='utf-8')") and "cp950" not in text("f = open(p, 'rb')")
    assert "不含 4" in text("for i in range(1, 4):\n    pass")
    assert "到最短的那個結束" in text("z = zip(a, b)")
    assert "會把意料之外的錯誤也吞掉" in text("try:\n    f()\nexcept Exception:\n    pass")
    assert "連 Ctrl+C" in text("try:\n    f()\nexcept:\n    pass")
    assert "python -O" in text("assert x > 0")
    assert "這裡不猜" in text("my_function(1)")
    assert "is 不是比值" in text("ok = a is b")
    assert "第 2 個之前（不含第 2 個）" in text("x = a[0:2]")


def test_syntax_errors_come_from_python() -> None:
    out = text("def f(:\n    pass")
    assert "Python 會拒絕執行" in out and "第 1 行" in out
