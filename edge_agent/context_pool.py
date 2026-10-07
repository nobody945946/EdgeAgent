"""Describe one model window lent to one inference client at a time."""

from dataclasses import dataclass

from edge_agent.runtime import MIN_AGENT_CONTEXT_SIZE


@dataclass(frozen=True, slots=True)
class ContextAllocation:
    total: int
    character: int
    agent: int

    @property
    def slots(self) -> int:
        return 1


def allocate_context(total: int, character: int) -> ContextAllocation:
    """The Agent keeps the full window; an idle borrower reserves no KV.

    The character value is its requested prompt limit, not a permanent slice.
    The inference scheduler serializes both clients on the same physical slot.
    """
    if total < MIN_AGENT_CONTEXT_SIZE:
        raise ValueError("此模型的 context 少於 8192，無法支援 EdgeAgent")
    if character < 0:
        raise ValueError("角色視窗不能為負數")
    return ContextAllocation(total, min(character, total), total)
