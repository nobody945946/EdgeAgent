import pytest
from net import retry


def test_succeeds_after_failures():
    waits, calls = [], []

    @retry(attempts=4, delay=0.5, exceptions=(ValueError,), sleep=waits.append)
    def flaky():
        """doc"""
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("x")
        return "ok"

    assert flaky() == "ok"
    assert waits == [0.5, 1.0]
    assert flaky.__name__ == "flaky" and flaky.__doc__ == "doc"


def test_reraises_last():
    waits = []

    @retry(attempts=3, delay=1, exceptions=(KeyError,), sleep=waits.append)
    def bad():
        raise KeyError("k")

    with pytest.raises(KeyError):
        bad()
    assert waits == [1, 2]


def test_other_exception_not_retried():
    calls = []

    @retry(attempts=5, delay=1, exceptions=(KeyError,), sleep=lambda s: None)
    def boom():
        calls.append(1)
        raise TypeError()

    with pytest.raises(TypeError):
        boom()
    assert len(calls) == 1


def test_arguments_pass_through():
    @retry(sleep=lambda s: None)
    def add(a, b=2):
        return a + b

    assert add(1, b=5) == 6
