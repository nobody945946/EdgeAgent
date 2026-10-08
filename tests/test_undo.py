"""edge_agent.undo and the "reversible" permission: what runs without asking can be put back."""

from __future__ import annotations

from pathlib import Path

import pytest

from edge_agent import approvals, undo


@pytest.fixture
def journal(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(undo, "ROOT", tmp_path / "journal")
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_restoring_a_checkpoint_undoes_it_and_every_later_one(journal: Path) -> None:
    (journal / "a.txt").write_text("v1")
    first = undo.checkpoint(journal, [Path("a.txt")], "edit: a.txt")
    (journal / "a.txt").write_text("v2")
    undo.checkpoint(journal, [Path("a.txt"), Path("new.txt")], "write: new.txt")
    (journal / "a.txt").write_text("v3")
    (journal / "new.txt").write_text("made")
    assert [h["label"] for h in undo.history(journal)] == ["write: new.txt", "edit: a.txt"]
    assert undo.restore(journal, first) == ["a.txt", "new.txt"]
    assert (journal / "a.txt").read_text() == "v1" and not (journal / "new.txt").exists()
    assert undo.history(journal) == []


def test_only_files_inside_the_workspace_and_not_too_big_are_covered(journal: Path, monkeypatch) -> None:
    assert undo.coverable(journal, [Path("sub/new.txt")])
    assert not undo.coverable(journal, [Path("../outside.txt")])
    (journal / "big.bin").write_bytes(b"x" * 10)
    monkeypatch.setattr(undo, "MAX_BYTES", 5)
    assert not undo.coverable(journal, [Path("big.bin")])


def test_a_reversible_edit_runs_after_its_file_is_copied(journal: Path) -> None:
    (journal / "app.py").write_text("print(1)\n")
    rules = {"edit": "reversible", "shell": "reversible", "web": "allow"}
    answer = approvals.host_answer({"tool": "edit", "arguments": {"path": "app.py", "edits": []}}, rules, journal)
    assert answer is True and undo.history(journal)[0]["files"] == ["app.py"]
    # An explicit approval (protected settings, deleting) is the owner's.
    assert approvals.host_answer({"tool": "write", "arguments": {"path": "x.txt"}, "explicit": "protected"},
                                 rules, journal) is None
    # Without "reversible" the host does not answer.
    assert approvals.host_answer({"tool": "edit", "arguments": {"path": "app.py"}},
                                 {**rules, "edit": "ask"}, journal) is None


@pytest.mark.parametrize(("command", "copies"), [
    ("Get-ChildItem", []),
    ("Set-Content -Path notes.txt -Value hi", ["notes.txt"]),
    ("python app.py", []),
    ("python app.py > log.txt", ["log.txt"]),
    ("python -m pytest -q", []),
    ("git status", []),
    ("Start-Process code", []),
    ("Invoke-RestMethod -Uri https://api.example.com/price", []),
    ("Rename-Item -Path app.py -NewName main.py", ["app.py", "main.py"]),
    ("Move-Item -Path app.py -Destination sub", ["app.py", "sub/app.py"]),
    ("Remove-Item app.py", None),
    ("pip install requests", None),
    ("git push", None),
    ("'x' > out.txt", None),
    ("Invoke-RestMethod -Uri https://api.example.com/order -Method Post -Body '{}'", None),
    ("Stop-Process -Name code", None),
    ("curl.exe https://example.com", None),
])
def test_a_command_runs_when_everything_it_does_can_be_put_back(journal: Path, command: str, copies) -> None:
    from edge_agent.permissions import reversible_shell

    (journal / "app.py").write_text("print(1)\n")
    (journal / "sub").mkdir()
    found = reversible_shell(command, journal, after_web=False)
    assert (None if found is None else sorted(p.relative_to(journal.resolve()).as_posix() for p in found)) == copies


@pytest.mark.parametrize("command", ["python app.py", "Start-Process code", "Invoke-RestMethod -Uri https://api.example.com/x"])
def test_after_web_content_programs_and_the_network_wait_for_the_owner(journal: Path, command: str) -> None:
    from edge_agent.permissions import reversible_shell

    (journal / "app.py").write_text("print(1)\n")
    assert reversible_shell(command, journal, after_web=True) is None
    assert reversible_shell("Set-Content -Path a.txt -Value 1", journal, after_web=True) is not None
