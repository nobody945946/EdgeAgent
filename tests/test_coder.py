"""edge_agent.coder: the host reproduces, localizes, splices and verifies;
the model (a stand-in here) only chooses and writes functions."""

from __future__ import annotations

import textwrap
from pathlib import Path

from edge_agent import coder

LIB = '''\
"""A tiny library."""

import math


def area(radius):
    return math.pi * radius * radius


class Basket:
    def __init__(self):
        self.items = []

    def add(self, item, count=1):
        for _ in range(count - 1):
            self.items.append(item)
        return len(self.items)
'''

TESTS = '''\
from shop.lib import Basket, area


def test_area():
    assert round(area(1), 2) == 3.14


def test_add_counts():
    basket = Basket()
    assert basket.add("apple", 2) == 2
'''

FIXED = '''\
```python
def add(self, item, count=1):
    for _ in range(count):
        self.items.append(item)
    return len(self.items)
```'''


class FakeModel:
    """Answers the choice with the listed method, a reproduction request
    with `tests` in turn and the repair with `replies` in turn."""

    def __init__(self, replies: list[str], tests: list[str] = ()):
        self.replies = list(replies)
        self.tests = list(tests)
        self.prompts: list[str] = []
        self.calls = 0
        self.tokens = {"input": 0, "output": 0}

    def chat(self, messages, **_options) -> str:
        self.calls += 1
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "只輸出 JSON" in prompt:
            number = next(line.split(".")[0] for line in prompt.splitlines() if "Basket.add" in line)
            return f'{{"edit": [{number}]}}'
        if "寫一個 pytest 測試檔" in prompt:
            return self.tests.pop(0)
        return self.replies.pop(0)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "shop" / "lib.py").write_text(LIB, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lib.py").write_text(TESTS, encoding="utf-8")
    return tmp_path


def test_a_wrong_version_is_rolled_back_and_the_right_one_kept(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    wrong = FIXED.replace("range(count)", "range(count + 1)")
    model = FakeModel([wrong, "不是程式碼", FIXED])
    result = coder.solve(root, "tests/test_lib.py 的 test_add_counts 失敗了，幫我修", model,
                         samples=4, rounds=1)
    assert result.outcome == "solved" and result.changed == ["shop/lib.py"]
    assert result.samples == 3
    text = (root / "shop" / "lib.py").read_text(encoding="utf-8")
    assert "range(count):" in text and "    def add(self, item, count=1):" in text
    assert "def area(radius):" in text  # the rest of the file is untouched
    assert "test_add_counts" in model.prompts[1] and "Basket.add" in model.prompts[0]


def test_nothing_is_left_changed_when_no_version_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    wrong = FIXED.replace("range(count)", "range(count + 1)")
    model = FakeModel([wrong] * 4)
    result = coder.solve(root, "test_add_counts 失敗", model, samples=2, rounds=2)
    assert result.outcome == "unsolved" and result.changed == []
    assert (root / "shop" / "lib.py").read_text(encoding="utf-8") == LIB
    assert "上一次改過之後" in model.prompts[-1]  # the second round sees the new error


REPRO = """```python
from shop.lib import Basket


def test_three_apples():
    assert Basket().add("apple", 3) == 3
```"""
PASSES = REPRO.replace("== 3", ">= 0")
BROKEN = REPRO.replace("from shop.lib import Basket", "from shop.nowhere import Basket")


def test_a_report_without_tests_gets_a_reproduction_first(tmp_path: Path) -> None:
    """The failing test is not there (a bug report): the model's test is kept
    only when it fails because of the code, then the usual repair runs."""
    root = _repo(tmp_path)
    (root / "tests" / "test_lib.py").write_text(TESTS.split("def test_add_counts")[0], encoding="utf-8")
    model = FakeModel([FIXED], tests=[PASSES, BROKEN, REPRO])
    result = coder.solve(root, "Basket 的 add 給 count=3 只加了兩個", model)
    assert result.outcome == "solved"
    assert result.changed == ["shop/lib.py", "tests/test_issue_repro.py"]
    assert "先寫了重現問題的測試" in result.answer
    assert "test_three_apples" in model.prompts[-1]  # the repair sees the reproduction


def test_no_reproduction_means_no_change(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "shop" / "lib.py").write_text(LIB.replace("range(count - 1)", "range(count)"), encoding="utf-8")
    result = coder.solve(root, "幫我看看 add", FakeModel([], tests=[PASSES, BROKEN, PASSES]))
    assert result.outcome == "no_failure" and result.changed == []
    assert not (root / "tests" / "test_issue_repro.py").exists()


def test_splice_places_helpers_and_imports(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    defs = coder.definitions(root, ["shop/lib.py"])
    add = next(d for d in defs if d.name == "Basket.add")
    code = textwrap.dedent('''\
        import itertools

        def _copies(item, count):
            return list(itertools.repeat(item, count))

        def add(self, item, count=1):
            self.items.extend(_copies(item, count))
            return len(self.items)
        ''')
    text = coder.splice(root, [add], code)["shop/lib.py"]
    assert text.index("import itertools") < text.index("def _copies") < text.index("class Basket")
    assert "        self.items.extend(_copies(item, count))" in text
    assert coder.splice(root, [add], "def other():\n    pass\n") is None
    assert coder.splice(root, [add], "def add(:\n") is None
