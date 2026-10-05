"""Agent sessions on the user's encrypted records drive.

The user keeps conversations so the Agent can grow from them (SilentTalk's
AGENTS.md rule 1). A session is written whole after each turn to
<folder>/edgeagent/<session>.jsonl (Pi's own entries, one per line), only
when sessions are recorded and the folder holds the setup marker: an
unplugged or locked drive gets nothing, and nothing is kept elsewhere to be
written later. The same drive and marker as SilentTalk's transcripts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

MARKER = ".agent-records"


def _system_drive() -> str:
    return (
        os.environ.get("SystemDrive", "C:").casefold()
        if sys.platform == "win32"
        else ""
    )


def validate_record_folder(folder: str) -> Path:
    """Resolve before checking the drive, including junctions and symlinks."""
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("請先選擇加密磁碟上的資料夾")
    path = Path(folder).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("紀錄資料夾不存在")
    system = _system_drive()
    if system and (path.drive.casefold() == system or len(path.drive) != 2):
        raise ValueError("請選擇系統磁碟以外、具有獨立磁碟代號的加密磁碟")
    return path


def prepare_record_folder(folder: str) -> Path:
    """Mark an existing folder selected on the user's unlocked encrypted disk."""
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
    return "暫停：磁碟沒接上、容器沒掛上或沒解鎖；請確認紀錄資料夾已設定"


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
