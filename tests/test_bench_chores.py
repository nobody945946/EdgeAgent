"""bench/tool_tasks_chores.py: the training chores ask for what their checks grade."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(BENCH))

import tool_tasks_chores


def _grade(task, root: Path, folder: str, layout: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the task's check on a workspace where each file of `folder` sits in the subfolder `layout` names."""
    task.write(root, "task")
    orig, work = root / "task" / "workspace", root / "work"
    for path in (orig / folder).iterdir():
        (work / folder / layout[path.name]).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, work / folder / layout[path.name] / path.name)
    check = (root / "task" / "check.py").read_text(encoding="utf-8")
    check = check.replace('W, O = Path("/work"), Path("/orig")', f"W, O = Path({str(work)!r}), Path({str(orig)!r})")
    (root / "check.py").write_text(check, encoding="utf-8")
    (root / "answer.json").write_text(json.dumps({"answer": "", "calls": []}), encoding="utf-8")
    return subprocess.run([sys.executable, str(root / "check.py")], capture_output=True, text=True, check=False,
                          env={**os.environ, "BENCH_ANSWER": str(root / "answer.json")})


def test_a_pdf_or_a_spreadsheet_may_sit_with_the_documents_but_pictures_not_with_videos(tmp_path: Path) -> None:
    kinds = {ext: kind for kind, exts in tool_tasks_chores.TYPE_POOLS.items() for ext in exts}

    def kinds_of(task) -> dict[str, str]:
        return {rel.split("/", 1)[1]: kinds[rel.rsplit(".", 1)[1]] for rel in task.files}

    task = next(t for t in (tool_tasks_chores.gen_organize(random.Random(s)) for s in range(200))
                if "子資料夾：" not in t.situation  # the owner names no folders
                and len(set(kinds_of(t).values()) & {"文件", "試算表", "PDF"}) > 1
                and len(set(kinds_of(t).values()) & {"圖片", "影片", "音樂"}) > 1)
    folder = next(iter(task.files)).split("/")[0]
    by_kind = kinds_of(task)
    merged = {name: "文件" if kind in ("文件", "試算表", "PDF") else kind for name, kind in by_kind.items()}
    media = {name: "媒體" if kind in ("圖片", "影片", "音樂") else kind for name, kind in by_kind.items()}
    assert _grade(task, tmp_path / "merged", folder, merged).returncode == 0
    assert _grade(task, tmp_path / "apart", folder, by_kind).returncode == 0
    assert _grade(task, tmp_path / "media", folder, media).returncode == 1


def test_the_photo_task_asks_for_every_photo_and_has_every_prefix() -> None:
    task = tool_tasks_chores.gen_rename_photos(random.Random(0))
    assert "每一張照片" in task.situation and all(p in task.situation for p in ("IMG_", "DSC_", "PXL_"))
    assert {rel.split("/")[1][:4] for rel in task.files} == {"IMG_", "DSC_", "PXL_"}
