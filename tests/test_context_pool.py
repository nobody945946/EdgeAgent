import pytest

from edge_agent.context_pool import allocate_context


@pytest.mark.parametrize(
    "total, requested, roles, character, agent",
    [(65536, 32768, 2, 16384, 32768),
     (98304, 32768, 2, 32768, 32768),
     (131072, 32768, 2, 32768, 65536),
     (16384, 32768, 1, 8192, 8192),
     (65536, 8192, 2, 8192, 49152),
     (65536, 0, 0, 0, 65536)],
)
def test_clients_share_a_bounded_pool(total, requested, roles, character, agent):
    result = allocate_context(total, requested, roles)
    assert result.total == total
    assert result.character == character
    assert result.agent == agent
    assert result.agent + result.character * roles == total
    assert result.slots == roles + 1


def test_an_unsupported_pool_is_rejected_before_loading():
    with pytest.raises(ValueError):
        allocate_context(8192, 32768, 2)
