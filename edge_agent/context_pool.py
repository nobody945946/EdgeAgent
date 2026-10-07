"""Allocate one configured model window between independent clients."""

from dataclasses import dataclass

from edge_agent.runtime import MIN_AGENT_CONTEXT_SIZE


@dataclass(frozen=True, slots=True)
class ContextAllocation:
    total: int
    character: int
    character_slots: int
    agent: int

    @property
    def slots(self) -> int:
        return self.character_slots + 1


def allocate_context(total: int, character: int, character_slots: int) -> ContextAllocation:
    """Keep an Agent window without expanding the user's configured pool.

    At 64K, two character caches receive at most 16K each and the Agent 32K.
    Larger pools preserve the requested character size; smaller pools retain
    at least the supported Agent minimum. A lease is a bounded request.
    """
    if character_slots == 0:
        if total < MIN_AGENT_CONTEXT_SIZE:
            raise ValueError("此模型的 context 少於 8192，無法支援 EdgeAgent")
        return ContextAllocation(total, 0, 0, total)
    agent_reserve = min(32768, max(MIN_AGENT_CONTEXT_SIZE, total // 2))
    character = min(character, (total - agent_reserve) // character_slots)
    if character < 1024:
        raise ValueError("模型視窗不足以同時提供角色租約與 Agent 工作空間")
    return ContextAllocation(
        total, character, character_slots, total - character * character_slots
    )
