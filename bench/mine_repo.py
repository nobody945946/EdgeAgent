"""Turn a repository's history into bench tasks: "make these tests pass".

For each commit that changes both code and tests (small code diff), the task
workspace is the parent commit plus the commit's test files; the reference
solution is the commit's code files. `run.py --selftest` keeps only tasks
that fail untouched and pass solved, so mined tasks need no manual review.

    python bench/mine_repo.py --repo silenttalk --last 120
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

BENCH = Path(__file__).resolve().parent
# Bench (evaluation) repositories, plus the open-source repositories training
# tasks are cut from (bench/make_tasks.py). The two sets never overlap.
REPOS = {"silenttalk": Path("/home/clawd/SilentTalk")}
TRAINING_REPOS = BENCH.parent / ".runtime/agentdata/repos"
if TRAINING_REPOS.is_dir():
    REPOS.update({p.name: p for p in sorted(TRAINING_REPOS.iterdir()) if (p / ".git").is_dir()})


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


def show(repo: Path, commit: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                          capture_output=True, check=True).stdout


def candidates(repo: Path, last: int, max_lines: int) -> list[tuple[str, str]]:
    found = []
    for line in git(repo, "log", "--no-merges", f"-{last}", "--format=%h %s").splitlines():
        sha, _, subject = line.partition(" ")
        stat = git(repo, "show", "--numstat", "--format=", sha).splitlines()
        code = tests = lines = 0
        for row in stat:
            added, removed, path = row.split("\t")
            if added == "-":
                continue
            if path.startswith("tests/test_") and path.endswith(".py"):
                tests += 1
            elif path.endswith(".py"):
                code += 1
                lines += int(added) + int(removed)
        if code and tests and lines <= max_lines:
            found.append((sha, subject))
    return found


def write_task(name: str, repo: Path, sha: str, subject: str, image: str) -> Path:
    parent = git(repo, "rev-parse", f"{sha}^").strip()
    changed = [row.split("\t") for row in
               git(repo, "show", "--name-status", "--format=", sha).splitlines()]
    tests = [p[-1] for p in changed if p[-1].startswith("tests/test_") and p[0] != "D"]
    code = [p for p in changed if p[-1].endswith(".py") and not p[-1].startswith("tests/")]
    task_dir = BENCH / "tasks" / f"repo-{name}-{sha}"
    if task_dir.exists():
        shutil.rmtree(task_dir)
    for path in tests:
        for base in ("workspace", "hidden"):
            target = task_dir / base / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(show(repo, sha, path))
    deleted = []
    for status, *paths in code:
        path = paths[-1]
        if status == "D":
            deleted.append(path)
            continue
        target = task_dir / "solution" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(show(repo, sha, path))
    listing = "、".join(tests)
    task = {
        "kind": "repo", "repo": name, "base": parent, "git": True, "image": image,
        "prompt": (f"這個專案要完成這項修改：「{subject}」。對應的測試已經寫好在 {listing}，"
                   "目前會失敗。請修改程式讓這些測試通過，不要修改測試檔，也不要動其他不相關的東西。"),
        "keep": tests, "check": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        "timeout_s": 1500,
    }
    if deleted:
        (task_dir / "solution_delete.txt").write_text("\n".join(deleted) + "\n")
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
    return task_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, choices=sorted(REPOS))
    parser.add_argument("--last", type=int, default=120)
    parser.add_argument("--max-lines", type=int, default=150)
    args = parser.parse_args()
    repo = REPOS[args.repo]
    for sha, subject in candidates(repo, args.last, args.max_lines):
        print(write_task(args.repo, repo, sha, subject, f"edgeagent-bench-{args.repo}:1").name)


if __name__ == "__main__":
    main()
