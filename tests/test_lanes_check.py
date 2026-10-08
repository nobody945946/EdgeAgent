"""tests/lanes_check.py on a deployed install: the router starts the install's own llama-server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lanes_check


def test_a_deployed_install_hands_the_router_its_llama_server_path(tmp_path: Path, monkeypatch) -> None:
    """2026-10-09, laptop round 8: the control server took the path's name, so
    the router was handed the server object and Popen failed."""
    from edge_agent import load_plan, router

    seen = {}

    class Core:
        def __init__(self, directory: Path) -> None:
            self.config = {"lora": "agent.gguf"}
            self.chat = type("Chat", (), {"bridge": type("Bridge", (), {})(), "close": lambda self: None})()

        def switch(self, *args, **kwargs):
            seen["router"], seen["load_plan"] = router.find_llama_server(), load_plan.find_llama_server()
            raise RuntimeError("stop before a model starts")

        def _stop_child(self) -> None:
            pass

    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/deployment.json").write_text(json.dumps({"server": "llama/llama-server.exe"}),
                                                       encoding="utf-8")
    monkeypatch.setattr(lanes_check, "EdgeAgent", Core)
    monkeypatch.setattr(lanes_check, "load_config", lambda path: {})
    monkeypatch.setattr(router, "find_llama_server", router.find_llama_server)
    monkeypatch.setattr(load_plan, "find_llama_server", load_plan.find_llama_server)
    monkeypatch.setattr(sys, "argv", ["lanes_check.py", "--install", str(tmp_path)])
    with pytest.raises(RuntimeError, match="stop before"):
        lanes_check.main()
    expected = str((tmp_path / "llama/llama-server.exe").resolve())
    assert seen == {"router": expected, "load_plan": expected}
