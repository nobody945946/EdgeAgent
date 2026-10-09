"""bench/grade.py: the verdict on a finished workspace."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

import grade


def test_a_workspace_left_with_big_test_files_fails_without_being_copied(tmp_path: Path, monkeypatch,
                                                                        capsys) -> None:
    task, orig, final = tmp_path / "task", tmp_path / "orig", tmp_path / "work"
    for folder in (task, orig, final / "_archive"):
        folder.mkdir(parents=True)
    (task / "task.json").write_text(json.dumps({"check": ["true"]}), encoding="utf-8")
    (orig / "a.log").write_text("x", encoding="utf-8")
    (final / "a.log").write_text("x", encoding="utf-8")
    with open(final / "_archive" / "f.log", "wb") as big:  # sparse: no real disk used here
        big.truncate(500 * 1024 * 1024)
    monkeypatch.setattr(grade, "TASK", task)
    monkeypatch.setattr(grade, "ORIG", orig)
    monkeypatch.setattr(grade, "FINAL", final)
    monkeypatch.setattr(grade.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(AssertionError("copied")))
    grade.main()
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["passed"] is False
    assert verdict["problems"] == ["bloat:500MB"]


def test_small_test_files_are_not_bloat(tmp_path: Path) -> None:
    (tmp_path / "t.log").write_bytes(b"x" * 1024 * 1024)
    assert grade.size(tmp_path) == 1024 * 1024 < grade.BLOAT
