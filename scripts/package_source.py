"""Build the EdgeAgent source archive that SilentTalk's Windows download
bundles and its bootstrap installs, from committed files only.

    python scripts/package_source.py --output edge-agent-windows-source-v8.zip

What a player gets: the package, its tests and the Windows installer and
check scripts. Not the bench (evaluation data), AGENTS.md (instructions for
the developers' agents) or the developer's own machine maintenance scripts.
The archive is reproducible: the same commit gives the same bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {"LICENSE", "README.md", "VALIDATION.md", "pyproject.toml", ".gitignore", ".gitattributes", "開啟 EdgeAgent.cmd"}
TREES = ("edge_agent/", "tests/")
# Fixed time for every entry so a commit always packs to the same bytes.
STAMP = (2020, 1, 1, 0, 0, 0)


def _git(project: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-c", "core.quotepath=false", *arguments], cwd=project,
                          capture_output=True, check=True, timeout=60).stdout


def files(project: Path = ROOT, revision: str = "HEAD") -> list[str]:
    tracked = _git(project, "ls-tree", "-r", "--name-only", revision).decode().splitlines()
    return sorted(
        name for name in tracked
        if name in TOP_FILES
        or name.startswith(TREES)
        # scripts/ itself, not its subfolders (windows-ai-maintenance is the developer's laptop)
        or (name.startswith("scripts/") and name.count("/") == 1)
    )


def build(output: Path, project: Path = ROOT, revision: str = "HEAD") -> dict[str, object]:
    commit = _git(project, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    names = files(project, commit)
    if "scripts/bootstrap.ps1" not in names or "pyproject.toml" not in names:
        raise ValueError("the installer or the package definition is missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            entry = zipfile.ZipInfo(name, date_time=STAMP)
            entry.external_attr = 0o644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, _git(project, "show", f"{commit}:{name}"))
    temporary.replace(output)
    data = output.read_bytes()
    return {"commit": commit, "files": len(names), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    print(json.dumps(build(args.output, revision=args.revision)))


if __name__ == "__main__":
    main()
