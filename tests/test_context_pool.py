import pytest

from edge_agent.context_pool import allocate_context


@pytest.mark.parametrize("total, requested", [
    (65536, 32768), (98304, 32768), (131072, 32768),
    (16384, 32768), (65536, 8192), (65536, 0), (8192, 32768),
])
def test_a_lease_adds_a_slot_in_the_same_pool_and_never_reduces_the_agent_window(total, requested):
    # The GPU holds the 64K pool and nothing more (owner, 2026-10-08): the
    # character's slot draws from it; the Agent's window stays the whole pool.
    result = allocate_context(total, requested)
    assert result.agent == result.total == total
    assert result.character == min(requested, total)
    assert result.slots == (2 if requested else 1)


@pytest.mark.parametrize("total, character", [(4096, 32768), (65536, -1)])
def test_an_invalid_window_is_rejected_before_loading(total, character):
    with pytest.raises(ValueError):
        allocate_context(total, character)
