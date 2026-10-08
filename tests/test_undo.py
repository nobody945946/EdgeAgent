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
    ("Get-Content -Path app.py", []),
    ('Get-ChildItem -Path . -Filter "*.tmp" -File | Measure-Object', []),
    ("Set-Content -Path notes.txt -Value hi", ["notes.txt"]),
    ("Get-ChildItem | Out-File -FilePath list.txt", ["list.txt"]),
    ("Rename-Item -Path app.py -NewName main.py", ["app.py", "main.py"]),
    ("Move-Item -Path app.py -Destination sub", ["app.py", "sub/app.py"]),
    ("Copy-Item -Path app.py -Destination copy.py", ["copy.py"]),
    ("New-Item -ItemType Directory -Path build", []),
])
def test_a_command_proven_to_touch_only_named_files_runs_after_they_are_copied(journal: Path, command: str, copies) -> None:
    from edge_agent.permissions import reversible_shell

    (journal / "app.py").write_text("print(1)\n")
    (journal / "sub").mkdir()
    found = reversible_shell(command, journal)
    assert found is not None, command
    assert sorted(p.relative_to(journal.resolve()).as_posix() for p in found) == copies


@pytest.mark.parametrize("command", [
    # Programs: what they do cannot be put back (the owner can allow a turn).
    "python app.py", "python -c \"import shutil; shutil.rmtree('build')\"", "python -m pip install requests",
    "python -m pytest -q", "pytest --basetemp=C:/Users/x/Documents", "git status", "git diff --output=app.py",
    "git commit -m x", "git checkout -- app.py",
    "code --install-extension x", ".\\pytest.cmd", "curl.exe https://example.com",
    # The network, processes, the system.
    "Invoke-RestMethod -Uri https://api.example.com/x", "Invoke-WebRequest -Uri https://x/a.exe -OutFile a.exe",
    "Start-Process code", "Start-Process pwsh -ArgumentList '-Command','Remove-Item -Recurse x'",
    "Stop-Process -Name code", "Remove-Item app.py", "Clear-Content app.py",
    # Anything not a plain pipeline of literal arguments.
    "if ($true) { Set-Content -Path /etc/zzz -Value x }", "(Get-Item app.py).Delete()",
    "[IO.File]::WriteAllText('x.txt','')", "Get-ChildItem | ForEach-Object Delete", "Get-Process | ForEach-Object Kill",
    "Write-Output (Stop-Process -Name explorer -Force)", "Write-Output $(Stop-Computer -Force)",
    "$p = 'x'; Set-Content -Path $p -Value 1", "Set-Content -Path notes.txt -Value $env:EDGE_API_X",
    "Get-Date && Set-Content -Path a.txt -Value 1", "'x' > out.txt",
    # Leaving the workspace, or naming files that cannot be told.
    "cd ..; Set-Content -Path notes.txt -Value x", "Set-Location ..", "Set-Content -Path ../out.txt -Value x",
    "Set-Content -Path *.txt -Value x", "Set-Content -Path . -Value x",
    # Links, Windows path forms and names Windows rewrites.
    "New-Item -ItemType SymbolicLink -Path l2 -Value /etc", "New-Item -ItemType HardLink -Path h.txt -Value app.py",
    "New-Item -ItemType Junction -Path j -Value /tmp", "Set-Content -Path \\\\server\\share\\x.txt -Value x",
    "Set-Content -Path //server/share/x.txt -Value x", "Set-Content -Path C:foo.txt -Value x",
    "Set-Content -Path 'app.py.' -Value x", "Set-Content -Path 'app.py ' -Value x", "Set-Content -Path 'a.txt:evil' -Value x",
    "Set-Content -Path a.txt -Value x -Stream evil", "Set-Content -Path env:EVIL -Value x", "Copy-Item -Path sub -Destination elsewhere -Recurse",
    "Expand-Archive -Path a.zip -DestinationPath .",
    # Redirections and archive functions are not proven by the parser: Set-Content and Out-File are.
    "Get-ChildItem > notes.txt", "Get-ChildItem > 'notes.txt'", "Compress-Archive -Path app.py -DestinationPath out",
])
def test_anything_not_proven_reversible_waits_for_the_owner(journal: Path, command: str) -> None:
    from edge_agent.permissions import reversible_shell

    (journal / "app.py").write_text("print(1)\n")
    (journal / "sub").mkdir()
    assert reversible_shell(command, journal) is None, command


@pytest.fixture
def repo(journal: Path) -> Path:
    import subprocess

    (journal / "app.py").write_text("print(1)\n")
    subprocess.run(["git", "init", "-q"], cwd=journal, check=True)
    return journal


@pytest.mark.parametrize("command", [
    "git status", "git status --short", "git diff", "git diff --cached --stat", "git log -n 5 --oneline",
    "git show HEAD --name-only", "git blame app.py", "git ls-files", "git branch -a", "git branch --show-current",
    "git diff HEAD~1 -- app.py | Select-Object -First 40",
])
def test_git_reading_the_workspaces_own_repository_runs(repo: Path, command: str) -> None:
    from edge_agent.permissions import reversible_shell, routine_shell

    assert routine_shell(command, repo), command
    assert reversible_shell(command, repo) == [], command


@pytest.mark.parametrize("command", [
    # Writes and programs: git's own and the ones its options run.
    "git commit -m x", "git checkout -- app.py", "git branch topic", "git stash", "git reset --hard",
    "git diff --output=app.py", "git diff --outp=app.py", "git log -p --ext-diff", "git diff --textconv",
    "git diff --no-index /etc/passwd app.py", "git blame --contents /etc/passwd app.py",
    # Global options (config, another repository or folder, a pager).
    "git -c core.pager=evil log", "git -C /etc status", "git --git-dir=/tmp/x status", "git -p log",
    "git --exec-path=. status", "git diff -- .git/config",
])
def test_git_that_writes_or_runs_programs_waits_for_the_owner(repo: Path, command: str) -> None:
    from edge_agent.permissions import reversible_shell, routine_shell

    assert not routine_shell(command, repo), command
    assert reversible_shell(command, repo) is None, command


def test_git_outside_a_repository_root_and_writes_under_git_wait_for_the_owner(repo: Path) -> None:
    from edge_agent.permissions import reversible_shell, routine_shell

    (repo / "sub").mkdir()
    # A folder inside a repository: git would read the repository above the workspace.
    assert not routine_shell("git status", repo / "sub")
    # .git's config and hooks decide what later git commands run: never changed unasked.
    assert reversible_shell("Set-Content -Path .git/config -Value x", repo) is None
    assert reversible_shell("Set-Content -Path .GIT/hooks/pre-commit -Value x", repo) is None
    rules = {"edit": "reversible", "shell": "reversible", "web": "allow"}
    hook = {"tool": "write", "arguments": {"path": ".git/hooks/pre-commit"},
            "resolved_path": str(repo / ".git" / "hooks" / "pre-commit")}
    assert approvals.host_answer(hook, rules, repo) is None
    assert approvals.host_answer({**hook, "turn_approved": True}, rules, repo) is True


def test_the_owners_turn_approval_covers_what_cannot_be_put_back_but_never_deleting(journal: Path) -> None:
    rules = {"edit": "reversible", "shell": "reversible", "web": "allow"}
    run = {"tool": "powershell", "arguments": {"command": "python app.py"}}
    assert approvals.host_answer(run, rules, journal) is None
    assert approvals.host_answer({**run, "turn_approved": True}, rules, journal) is True
    delete = {"tool": "powershell", "arguments": {"command": "Remove-Item app.py"}, "explicit": "destructive",
              "turn_approved": True}
    assert approvals.host_answer(delete, rules, journal) is None


def test_an_edit_is_copied_where_pi_writes_it(journal: Path) -> None:
    (journal / "app.py").write_text("v1")
    rules = {"edit": "reversible", "shell": "reversible", "web": "allow"}
    edit = {"tool": "edit", "arguments": {"path": "@app.py"}, "resolved_path": str(journal / "app.py")}
    assert approvals.host_answer(edit, rules, journal) is True
    assert undo.history(journal)[0]["files"] == ["app.py"]
    outside = {"tool": "write", "arguments": {"path": "~/x.txt"}, "resolved_path": str(Path.home() / "x.txt")}
    assert approvals.host_answer(outside, rules, journal) is None


def test_a_checkpoint_that_fails_leaves_undo_working(journal: Path, monkeypatch) -> None:
    import shutil

    (journal / "a.txt").write_text("v1")
    good = undo.checkpoint(journal, [Path("a.txt")], "first")

    def broken(*args, **kwargs):
        raise PermissionError("locked")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copy2", broken)
        with pytest.raises(OSError):
            undo.checkpoint(journal, [Path("a.txt")], "second")
        rules = {"edit": "reversible", "shell": "reversible", "web": "allow"}
        assert approvals.host_answer({"tool": "edit", "arguments": {"path": "a.txt"}}, rules, journal) is None
    assert [h["id"] for h in undo.history(journal)] == [good]
    (journal / "a.txt").write_text("v2")
    assert undo.restore(journal, good) == ["a.txt"] and (journal / "a.txt").read_text() == "v1"
