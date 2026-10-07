"""Conversation modes and thinking profiles: which skill opens a mode's stretch."""

from __future__ import annotations

from pathlib import Path

THINKING_PROFILES = {"off": "off", "low": "low", "medium": "medium", "high": "high"}
# Modes besides plain work open their stretch of conversation with a skill:
# - interview (求知儀式): the grill-me interview (edge_agent/skills/grill-me,
#   MIT) with read-only tools, until the user confirms a spec;
# - discussion (推演儀式): rules for weighing data and ideas without misleading
#   (edge_agent/skills/discussion; it cut misleading answers from 35% to 20%,
#   and from 43% to 18% on items it was not written against, bench/logic_eval.py).
MODES = ("work", "interview", "discussion")
SKILLS = Path(__file__).with_name("skills")
MODE_SKILLS = {"interview": SKILLS / "grill-me" / "SKILL.md", "discussion": SKILLS / "discussion" / "SKILL.md"}


def mode_opening(mode: str, text: str) -> str:
    """The first message of a mode's stretch: the skill's instructions, then
    the user's words. Sent as text rather than Pi's /skill: command, whose
    block names the skill file's path: small models then try to read that
    file, outside the workspace, until the repeat guard stops the turn."""
    _, front, body = MODE_SKILLS[mode].read_text(encoding="utf-8").split("---", 2)
    name = next(line.split(":", 1)[1].strip() for line in front.splitlines() if line.startswith("name:"))
    return f'<skill name="{name}">\n{body.strip()}\n</skill>\n\n{text}'
