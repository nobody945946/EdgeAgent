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
    (root / tag / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def cards(root: Path, tag: str, passed: dict[str, bool]) -> None:
    (root / tag).mkdir(parents=True)
    (root / tag / "verdicts.jsonl").write_text(
        "".join(json.dumps({"job": f"{card}-r0", "passed": ok}) + "\n" for card, ok in passed.items()))


def gate(tmp_path: Path, monkeypatch, new_chat: list[bool], new_cards: dict[str, bool]) -> int:
    monkeypatch.setattr(ship_gate, "STATE", tmp_path)
    runs(tmp_path, "base", {"chat-greeting": [True, False], "code-a": [True, True]})
    runs(tmp_path, "new", {"chat-greeting": new_chat, "code-a": [True, True]}, crash="code-a")
    runs(tmp_path, "base-high", {"chat-greeting": [True, True], "code-a": [False, True]})
    runs(tmp_path, "new-high", {"chat-greeting": [True, True], "code-a": [True, True]})
    cards(tmp_path, "cards-base", {"eval-C-001": False, "eval-D-001": True})
    cards(tmp_path, "cards-new", new_cards)
    monkeypatch.setattr(sys, "argv", ["ship_gate", "--off", "base", "new", "--high", "base-high", "new-high",
                                      "--cards", "cards-base", "cards-new"])
    with pytest.raises(SystemExit) as stop:
        ship_gate.main()
    return stop.value.code


def test_it_ships_only_when_every_gate_passes(tmp_path: Path, monkeypatch) -> None:
    assert gate(tmp_path, monkeypatch, [True, True], {"eval-C-001": True, "eval-D-001": True}) == 0
    report = json.loads((tmp_path / "new" / "ship-gate.json").read_text())
    assert report["off"]["runs"] == 4 and report["cards"]["won"] == ["eval-C-001-r0"]


def test_one_failed_chat_probe_stops_it(tmp_path: Path, monkeypatch) -> None:
    assert gate(tmp_path, monkeypatch, [True, False], {"eval-C-001": True, "eval-D-001": True}) == 1
    assert json.loads((tmp_path / "new" / "ship-gate.json").read_text())["chat_failed"] == ["chat-greeting r1"]
    why = (tmp_path / "new" / "why.txt").read_text()
    assert "== chat-greeting r1" in why and "before (base):" in why and "after (new):" in why


def test_cards_must_be_strictly_better(tmp_path: Path, monkeypatch) -> None:
    assert gate(tmp_path, monkeypatch, [True, True], {"eval-C-001": True, "eval-D-001": False}) == 1
