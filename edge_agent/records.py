"""Agent sessions in the user's selected records folder.

The user keeps conversations so the Agent can grow from them (SilentTalk's
AGENTS.md rule 1). A session is written whole after each turn to
<folder>/edgeagent/<session>.jsonl (Pi's own entries, one per line), only
when sessions are recorded and the folder holds the setup marker: an
unavailable or unprepared folder gets nothing, and nothing is kept elsewhere
to be written later. Local SSD folders are allowed. The same folder and
marker as SilentTalk's transcripts.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MARKER = ".agent-records"


def validate_record_folder(folder: str) -> Path:
    """Require an existing folder, resolving junctions and symlinks."""
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("請先選擇紀錄資料夾")
    path = Path(folder).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("紀錄資料夾不存在")
    return path


def prepare_record_folder(folder: str) -> Path:
    """Mark the existing folder selected by the user for records."""
    path = validate_record_folder(folder)
    (path / MARKER).touch(exist_ok=True)
    return path


def recording_status(enabled: bool, folder: str) -> str:
    if not enabled:
        return "關閉"
    try:
        path = validate_record_folder(folder)
        if (path / MARKER).is_file():
            return "記錄中"
    except (OSError, ValueError, RuntimeError):
        pass
    return "暫停：紀錄資料夾無法使用或尚未設定；請確認資料夾與標記檔"


_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def save_session(
    folder: str | None, session_id: str, entries: list[dict[str, object]]
) -> bool:
    """Write the session's entries; False when nothing was written."""
    if not folder or recording_status(True, folder) != "記錄中":
        return False
    name = (
        session_id
        if _SAFE.match(session_id)
        else hashlib.sha1(session_id.encode()).hexdigest()
    )
    try:
        target = validate_record_folder(folder) / "edgeagent" / f"{name}.jsonl"
        target.parent.mkdir(exist_ok=True)
        partial = target.with_suffix(".jsonl.part")
        partial.write_text(
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
            encoding="utf-8",
        )
        partial.replace(target)
    except (OSError, ValueError, RuntimeError):
        return False
    return True
