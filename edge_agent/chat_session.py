"""One conversation's state, held only in RAM."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass(slots=True)
class ChatSession:
    """Pi session checkpoints, approvals and review data, held only in RAM."""

    entries: list[dict[str, object]] = field(default_factory=list)
    messages: list[dict[str, object]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event = field(default_factory=threading.Event)
    cancelled_at: float | None = None
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)
    approvals: dict[str, dict[str, object]] = field(default_factory=dict)
    # In a 求知儀式 stretch: the grill-me skill is already in the history.
    opened_mode: str = "work"  # the mode whose skill opened the current stretch
    # Whose conversation it is, fixed by its first task: a public one (anyone
    # in a chat) never continues as the owner's work, or the other way round,
    # so neither side's history reaches the other.
    public: bool | None = None
