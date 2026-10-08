"""Every test keeps EdgeAgent's own data folders to itself: the undo journal
and the API keys go to a temporary folder, never the install's .runtime."""

from __future__ import annotations

import pytest

from edge_agent import apis, undo


@pytest.fixture(autouse=True)
def private_runtime(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("runtime")
    monkeypatch.setattr(undo, "ROOT", folder / "undo")
    monkeypatch.setattr(apis, "KEYS", folder / "api-keys.json")
