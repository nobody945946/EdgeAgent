"""bench/ship_gate.py: the owner's ship rule on made-up runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import ship_gate


def runs(root: Path, tag: str, passed: dict[str, list[bool]], crash: str | None = None) -> None:
    (root / tag).mkdir(parents=True)
    rows = [{"task": task, "rep": rep, "passed": ok, "outcome": "done"}
            for task, oks in passed.items() for rep, ok in enumerate(oks)]
    if crash:
        rows.insert(0, {"task": crash, "rep": 0, "passed": False, "outcome": "crash"})
    (root / tag / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def cards(root: Path, tag: str, passed: dict[str, bool]) -> None:
    (root / tag).mkdir(parents=True)
    (root / tag / "verdicts.jsonl").write_text(
        "".join(json.dumps({"job": f"{card}-r0", "passed": ok}) + "\n" for card, ok in passed.items()), encoding="utf-8")


def opponent(root: Path, name: str, off: dict, high: dict, held_out: dict, python: dict) -> list[str]:
    for tag, made in ((f"{name}-off", off), (f"{name}-high", high), (f"{name}-py", python)):
        runs(root, tag, made)
    cards(root, f"{name}-cards", held_out)
    return ["--against", name, f"{name}-off", f"{name}-high", f"{name}-cards", f"{name}-py"]


def gate(tmp_path: Path, monkeypatch, *against: list[str], new_chat=(True, True),
         new_cards=None, new_python=None, new_off=None) -> tuple[int, dict]:
    monkeypatch.setattr(ship_gate, "STATE", tmp_path)
    runs(tmp_path, "new", new_off or {"chat-greeting": list(new_chat), "code-a": [True, True]}, crash="code-a")
    runs(tmp_path, "new-high", {"chat-greeting": [True, True], "code-a": [True, True]})
    cards(tmp_path, "new-cards", new_cards or {"eval-C-001": True, "eval-D-001": True})
    runs(tmp_path, "new-py", new_python or {"py-eval-001": [True], "py-eval-002": [True]})
    monkeypatch.setattr(sys, "argv", ["ship_gate", "--new", "new", "new-high", "new-cards", "new-py",
                                      *[arg for one in against for arg in one]])
    with pytest.raises(SystemExit) as stop:
        ship_gate.main()
    return stop.value.code, json.loads((tmp_path / "new" / "ship-gate.json").read_text(encoding="utf-8"))


def base(root: Path) -> list[str]:
    return opponent(root, "base", {"chat-greeting": [True, False], "code-a": [True, True]},
                    {"chat-greeting": [True, True], "code-a": [False, True]},
                    {"eval-C-001": False, "eval-D-001": True}, {"py-eval-001": [False], "py-eval-002": [True]})


def shipped(root: Path) -> list[str]:
    return opponent(root, "ms3n", {"chat-greeting": [True, True], "code-a": [True, False]},
                    {"chat-greeting": [True, True], "code-a": [True, True]},
                    {"eval-C-001": True, "eval-D-001": False}, {"py-eval-001": [True], "py-eval-002": [True]})


def test_it_ships_only_when_every_gate_passes_against_every_opponent(tmp_path: Path, monkeypatch) -> None:
    code, report = gate(tmp_path, monkeypatch, base(tmp_path), shipped(tmp_path))
    assert code == 0 and report["ship"] and set(report["against"]) == {"base", "ms3n"}
    assert report["against"]["base"]["off"]["runs"] == 4
    assert report["against"]["base"]["cards"]["won"] == ["eval-C-001-r0"]
    assert report["against"]["ms3n"]["cards"]["won"] == ["eval-D-001-r0"]


def test_losing_to_one_opponent_stops_it(tmp_path: Path, monkeypatch) -> None:
    lost_python = {"py-eval-001": [False], "py-eval-002": [True]}
    code, report = gate(tmp_path, monkeypatch, base(tmp_path), shipped(tmp_path), new_python=lost_python)
    assert code == 1 and report["against"]["base"]["passed"]
    assert report["against"]["ms3n"]["gates"]["python"] is False
    why = (tmp_path / "new" / "why.txt").read_text(encoding="utf-8")
    assert "# against ms3n, Python tasks that got worse\n== py-eval-001 r0" in why
    assert "before (ms3n-py):" in why and "after (new-py):" in why


def test_one_failed_chat_probe_stops_it(tmp_path: Path, monkeypatch) -> None:
    code, report = gate(tmp_path, monkeypatch, base(tmp_path), new_chat=(True, False))
    assert code == 1 and report["against"]["base"]["passed"]
    assert report["chat_probes"] == {"failed": ["chat-greeting r1"], "passed": False}
    why = (tmp_path / "new" / "why.txt").read_text(encoding="utf-8")
    assert "== chat-greeting r1" in why and "before (base-off):" in why and "after (new):" in why


def test_a_mode_without_chat_probes_does_not_pass_them(tmp_path: Path, monkeypatch) -> None:
    code, report = gate(tmp_path, monkeypatch, base(tmp_path), new_off={"code-a": [True, True]})
    assert code == 1 and report["chat_probes"] == {"failed": [], "passed": False}


def test_cards_must_be_strictly_better(tmp_path: Path, monkeypatch) -> None:
    code, report = gate(tmp_path, monkeypatch, base(tmp_path), new_cards={"eval-C-001": True, "eval-D-001": False})
    assert code == 1 and report["against"]["base"]["gates"]["held_out_cards"] is False


def test_tools_or_mcp_getting_worse_stops_it_even_when_the_total_rises(tmp_path: Path, monkeypatch) -> None:
    against = opponent(tmp_path, "base", {"chat-greeting": [True], "mcp-s-close": [True, True], "code-a": [False, False]},
                       {"chat-greeting": [True, True], "code-a": [True, True]},
                       {"eval-C-001": False}, {"py-eval-001": [True]})
    code, report = gate(tmp_path, monkeypatch, against, new_off={
        "chat-greeting": [True], "mcp-s-close": [True, False], "code-a": [True, True]})
    gates = report["against"]["base"]
    assert code == 1 and gates["gates"]["bench_off"] and gates["off"]["lowered"] == ["mcp"]
    assert "== mcp-s-close r1" in (tmp_path / "new" / "why.txt").read_text(encoding="utf-8")
