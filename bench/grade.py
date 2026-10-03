"""Grade one finished workspace in a fresh sandbox. Never runs on the host.

Mounts: /task (task definition), /orig (workspace before the agent), /work
(workspace after the agent), all read-only. A task passes when its check
command exits 0, every `keep` path is unchanged, nothing outside `may_change`
was modified or deleted, and no test infrastructure was tampered with.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

TASK, ORIG, FINAL = Path("/task"), Path("/orig"), Path("/work")
INFRA = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "sitecustomize.py",
         "usercustomize.py"}


def tree(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            path = Path(base, name)
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                files[rel] = "link:" + os.readlink(path)
            elif path.is_file():
                files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def matches(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def main() -> None:
    task = json.loads((TASK / "task.json").read_text(encoding="utf-8"))
    before, after = tree(ORIG), tree(FINAL)
    problems: list[str] = []
    for rel, digest in before.items():
        if matches(rel, task.get("keep", [])) and after.get(rel) != digest:
            problems.append("keep:" + rel)
    may_change = task.get("may_change")
    collateral = [
        rel for rel, digest in before.items()
        if after.get(rel) != digest and may_change is not None
        and not matches(rel, may_change)
    ]
    problems += ["collateral:" + rel for rel in collateral[:20]]
    for rel in sorted(set(before) | set(after)):
        name = rel.rsplit("/", 1)[-1]
        if (name in INFRA or name.endswith(".pth")) and before.get(rel) != after.get(rel):
            problems.append("tampered:" + rel)
    work = Path("/tmp/grade")
    shutil.copytree(FINAL, work, symlinks=True)
    if (TASK / "hidden").is_dir():
        shutil.copytree(TASK / "hidden", work, dirs_exist_ok=True)
    env = {**os.environ, "BENCH_ORIG": str(ORIG), "BENCH_TASK": str(TASK)}
    check = list(task["check"])
    pytest = check[1:3] == ["-m", "pytest"]
    if pytest:
        check += ["--junitxml", "/tmp/junit.xml"]
    try:
        result = subprocess.run(check, cwd=work, env=env, capture_output=True,
                                text=True, timeout=300)
        code, output = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        code, output = -1, "check timed out"
    if pytest and code == 0:
        # Exit 0 alone is not proof: code under test could exit the process.
        try:
            suite = ElementTree.parse("/tmp/junit.xml").getroot()
            suite = suite if suite.tag == "testsuite" else suite[0]
            ran = int(suite.get("tests", 0)) - int(suite.get("skipped", 0))
            broken = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
            if ran <= 0 or broken:
                problems.append("junit:no-passing-tests")
        except (OSError, ElementTree.ParseError, IndexError):
            problems.append("junit:missing")
    changed = sum(1 for rel, d in before.items() if rel in after and after[rel] != d)
    print(json.dumps({
        "passed": code == 0 and not problems, "check_exit": code, "problems": problems,
        "changed": changed, "deleted": sum(1 for rel in before if rel not in after),
        "created": sum(1 for rel in after if rel not in before),
        "check_tail": output[-1200:],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
