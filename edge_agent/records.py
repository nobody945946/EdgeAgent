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
import re
from pathlib import Path

MARKER = ".agent-records"
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def save_session(folder: str | None, session_id: str, entries: list[dict[str, object]]) -> bool:
    """Write the session's entries; False when nothing was written."""
    if not folder or not (Path(folder) / MARKER).is_file():
        return False
    name = session_id if _SAFE.match(session_id) else hashlib.sha1(session_id.encode()).hexdigest()
    target = Path(folder) / "edgeagent" / f"{name}.jsonl"
    try:
        target.parent.mkdir(exist_ok=True)
        partial = target.with_suffix(".jsonl.part")
        partial.write_text(
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
            encoding="utf-8",
        )
        partial.replace(target)
    except OSError:
        return False
    return True
