"""edge_agent.apis: the owner's API registry, its keys kept apart and never shown to the model."""

from __future__ import annotations

import json

import pytest

from edge_agent import apis


def test_the_recommended_registry_is_valid_and_keyed_apis_wait_for_their_key() -> None:
    registry = apis.validate_apis([dict(api) for api in apis.RECOMMENDED])
    shown, env = apis.for_session(registry)
    names = {api["name"] for api in shown}
    assert "證交所 OpenAPI" in names and "Steam 商店" in names
    assert "IsThereAnyDeal" not in names and env == {}
    apis.set_key("isthereanydeal", "secret-key-123")
    shown, env = apis.for_session(registry)
    listed = next(api for api in shown if api["name"] == "IsThereAnyDeal")
    assert listed["key_env"] == "EDGE_API_ISTHEREANYDEAL" and "secret-key-123" not in json.dumps(shown)
    assert env == {"EDGE_API_ISTHEREANYDEAL": "secret-key-123"}
    assert apis.has_key(registry)["isthereanydeal"] is True
    apis.set_key("isthereanydeal", "")
    assert apis.for_session(registry)[1] == {}


@pytest.mark.parametrize("change", [
    {"id": "Bad Id"}, {"base_url": "ftp://x"}, {"key_in": "header", "key_name": ""}, {"key_in": "cookie"},
])
def test_a_broken_entry_is_refused(change) -> None:
    entry = {**apis.RECOMMENDED[0], **change}
    with pytest.raises(ValueError):
        apis.validate_apis([entry])
