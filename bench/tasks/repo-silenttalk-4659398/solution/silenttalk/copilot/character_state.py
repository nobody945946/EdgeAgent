"""The character's own life between sessions: character_state.json.

A person changes: what they wanted gets done and something else comes up,
moods pass, they form views and impressions of the people around them. The
user-written persona (personas.json) is the core and is never rewritten; this
file is the layer that moves. The user allowed storing it on one condition:
only the character's own state, in its own words. No transcripts, no quotes,
no details about anyone that came from the chat (AGENTS.md rule 1), which is
why every text goes through `_clean` before it is kept.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

STATE_FILE = "character_state.json"

# open: on its mind; told: already shared, bring up only news about it;
# progress: moving; done: satisfied; dropped: let go.
STATUSES = ("open", "told", "progress", "done", "dropped")
ACTIVE = ("open", "told", "progress")
MAX_CONCERNS = 8
MAX_FINISHED = 3
MAX_VIEWS = 6
MAX_FEELINGS = 12
MAX_TEXT = 40

# Nothing that looks like contact details or an account survives.
_PRIVATE = re.compile(
    r"(\d[\d\- ]{5,}\d|https?://|www\.|@\w|[\w.+-]+@[\w-]+\.\w+|帳號|密碼|身分證|地址)"
)


@dataclass(frozen=True, slots=True)
class Concern:
    text: str
    status: str = "open"
    # How it is going, in the character's words ("約成了，週六大家都在").
    note: str = ""

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


@dataclass(frozen=True, slots=True)
class CharacterState:
    concerns: tuple[Concern, ...] = ()
    recent: str = ""
    mood: str = ""
    # Views it has stated ("恐怖片我真的不行"), so it stays consistent.
    views: tuple[str, ...] = ()
    # (name, how it feels about them).
    feelings: tuple[tuple[str, str], ...] = ()
    updated: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.concerns or self.recent or self.mood or self.views or self.feelings)

    def active_concerns(self) -> list[Concern]:
        return [concern for concern in self.concerns if concern.active]

    def finished_concerns(self) -> list[Concern]:
        return [concern for concern in self.concerns if not concern.active]

    def days_since_update(self, now: datetime | None = None) -> int | None:
        if not self.updated:
            return None
        try:
            then = datetime.fromisoformat(self.updated)
        except ValueError:
            return None
        return max(0, ((now or datetime.now()) - then).days)

    def normalized(self) -> CharacterState:
        """Cleaned, capped and deduplicated: what may be kept on disk."""
        concerns: list[Concern] = []
        for concern in self.concerns:
            text = _clean(concern.text)
            if not text or concern.status not in STATUSES:
                continue
            # A small model writes the same worry twice in other words.
            twin = next((i for i, c in enumerate(concerns) if _alike(c.text, text)), None)
            if twin is not None:
                if concern.status != "open":  # the later word on it wins
                    concerns[twin] = Concern(concerns[twin].text, concern.status, _clean(concern.note))
                continue
            concerns.append(Concern(text, concern.status, _clean(concern.note)))
        active = [c for c in concerns if c.active][:MAX_CONCERNS]
        finished = [c for c in concerns if not c.active][-MAX_FINISHED:]
        views = [v for v in dict.fromkeys(_clean(view) for view in self.views) if v]
        feelings: dict[str, str] = {}
        for name, feeling in self.feelings:
            name, feeling = _clean(name, 20), _clean(feeling)
            if name and feeling:
                feelings[name] = feeling
        return replace(
            self,
            concerns=tuple(active + finished),
            recent=_clean(self.recent, 60),
            mood=_clean(self.mood, 20),
            views=tuple(views[-MAX_VIEWS:]),
            feelings=tuple(list(feelings.items())[-MAX_FEELINGS:]),
        )

    def prompt_block(self, now: datetime | None = None, *, full: bool = True) -> str:
        """What the model reads about its own life, after the persona sheet.
        `full=False` (character_memory "light", a 2B model on a 3050 Ti)
        leaves out views and feelings: fewer lines to attend to and copy."""
        lines: list[str] = []
        days = self.days_since_update(now)
        if days is not None and days >= 2:
            lines.append(f"上次跟大家聊天是 {days} 天前。")
        if self.recent:
            lines.append(f"你最近：{self.recent}")
        # A mood passes: after a couple of days it is not carried over.
        if self.mood and (days is None or days < 2):
            lines.append(f"你這陣子的心情：{self.mood}")
        active = self.active_concerns()
        if active:
            lines.append(
                "你心裡掛著的事（聊天時接得上就自然帶到，冷場時可以拿來開話題，不要硬塞；"
                "標了「說過了」的只講新進展）："
            )
            for concern in active:
                mark = {"told": "（說過了）", "progress": "（有進展）"}.get(concern.status, "")
                note = f"：{concern.note}" if concern.note else ""
                lines.append(f"- {concern.text}{mark}{note}")
        finished = self.finished_concerns()
        if finished:
            lines.append(
                "最近已經了結的事：" + "；".join(
                    f"{c.text}（{'達成了' if c.status == 'done' else '放下了'}）" for c in finished
                )
            )
        if self.views and full:
            lines.append("你說過的看法（要前後一致）：" + "；".join(self.views))
        if self.feelings and full:
            lines.append("你對身邊的人：" + "；".join(f"{n}：{f}" for n, f in self.feelings))
        return "\n".join(lines)


class CharacterStateStore:
    """character_state.json: {persona_id: state}. Belongs to the user, like
    personas.json; the persona page shows it and can clear it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, persona_id: str) -> CharacterState:
        raw = self._read().get(persona_id)
        if not isinstance(raw, dict):
            return CharacterState()
        try:
            state = CharacterState(
                concerns=tuple(
                    Concern(str(c.get("text", "")), str(c.get("status", "open")), str(c.get("note", "")))
                    for c in raw.get("concerns", [])
                    if isinstance(c, dict)
                ),
                recent=str(raw.get("recent", "")),
                mood=str(raw.get("mood", "")),
                views=tuple(str(v) for v in raw.get("views", [])),
                feelings=tuple(
                    (str(pair[0]), str(pair[1]))
                    for pair in raw.get("feelings", [])
                    if isinstance(pair, list | tuple) and len(pair) == 2
                ),
                updated=str(raw.get("updated", "")),
            )
        except (AttributeError, TypeError):
            return CharacterState()
        return state.normalized()

    def save(self, persona_id: str, state: CharacterState) -> None:
        data = self._read()
        state = state.normalized()
        data[persona_id] = {
            "concerns": [
                {"text": c.text, "status": c.status, "note": c.note} for c in state.concerns
            ],
            "recent": state.recent,
            "mood": state.mood,
            "views": list(state.views),
            "feelings": [list(pair) for pair in state.feelings],
            "updated": state.updated or datetime.now().isoformat(timespec="seconds"),
        }
        self._write(data)

    def clear(self, persona_id: str) -> None:
        data = self._read()
        if data.pop(persona_id, None) is not None:
            self._write(data)

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(self.path)


def _alike(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


def _clean(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(text).split()).strip("「」\"' ")
    if not text or _PRIVATE.search(text):
        return ""
    return text[:limit]


def merge_state(
    old: CharacterState, new: CharacterState, *, now: datetime | None = None
) -> CharacterState:
    """The update the model wrote, on top of what was there: empty fields
    keep the old value; the timestamp moves."""
    return CharacterState(
        concerns=new.concerns or old.concerns,
        recent=new.recent or old.recent,
        mood=new.mood or old.mood,
        views=tuple(dict.fromkeys(old.views + new.views)),
        feelings=tuple({**dict(old.feelings), **dict(new.feelings)}.items()),
        updated=(now or datetime.now()).isoformat(timespec="seconds"),
    ).normalized()


__all__ = [
    "ACTIVE",
    "STATE_FILE",
    "STATUSES",
    "CharacterState",
    "CharacterStateStore",
    "Concern",
    "merge_state",
]
