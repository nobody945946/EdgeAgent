"""How one model window is shared by the Agent and a leased character."""

from dataclasses import dataclass

from edge_agent.runtime import MIN_AGENT_CONTEXT_SIZE


@dataclass(frozen=True, slots=True)
class ContextAllocation:
    total: int
    character: int
    agent: int

    @property
    def slots(self) -> int:
        """A leased character gets a slot of its own in the same pool."""
        return 2 if self.character else 1


def allocate_context(total: int, character: int) -> ContextAllocation:
    """The Agent keeps the full window (owner, 2026-10-08: 64K is the floor,
    and the GPU holds nothing more); a leased character draws its cells from
    the same pool (kv-unified), never a slice of its own. `character` is its
    prompt limit; edge_agent.inference counts what each slot holds, and the
    Agent's need always wins."""
    if total < MIN_AGENT_CONTEXT_SIZE:
        raise ValueError("此模型的 context 少於 8192，無法支援 EdgeAgent")
    if character < 0:
        raise ValueError("角色視窗不能為負數")
    return ContextAllocation(total, min(character, total), total)
