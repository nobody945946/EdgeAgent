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
    from edge_agent import load_plan, processes, router

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
    monkeypatch.setattr(processes, "listening_pid", lambda port: None)
    monkeypatch.setattr(lanes_check, "ExclusiveHTTPServer", lambda *args: type(
        "Server", (), {"serve_forever": lambda self: None, "shutdown": lambda self: None})())
    monkeypatch.setattr(router, "find_llama_server", router.find_llama_server)
    monkeypatch.setattr(load_plan, "find_llama_server", load_plan.find_llama_server)
    monkeypatch.setattr(sys, "argv", ["lanes_check.py", "--install", str(tmp_path)])
    with pytest.raises(RuntimeError, match="stop before"):
        lanes_check.main()
    expected = str((tmp_path / "llama/llama-server.exe").resolve())
    assert seen == {"router": expected, "load_plan": expected}


@pytest.mark.parametrize("agent_lora", ["", "agent.gguf"])
def test_shared_measurement_uses_one_real_adapter_for_both_lanes(tmp_path: Path, monkeypatch,
                                                               agent_lora: str) -> None:
    from edge_agent import processes

    measured = []

    class Core:
        def __init__(self, directory: Path) -> None:
            self.config = {"lora": agent_lora}
            self.chat = type("Chat", (), {"bridge": type("Bridge", (), {})(), "close": lambda self: None})()

        def switch(self, *args, **kwargs):
            return {}

        def _stop_child(self) -> None:
            pass

    def measure(core, lease_lora, config):
        measured.append((lease_lora, config["lora"]))
        return {"lease_lora": lease_lora, "agent_lora": config["lora"]}

    monkeypatch.setattr(lanes_check, "EdgeAgent", Core)
    monkeypatch.setattr(lanes_check, "load_config", lambda path: {})
    monkeypatch.setattr(processes, "listening_pid", lambda port: None)
    monkeypatch.setattr(lanes_check, "ExclusiveHTTPServer", lambda *args: type(
        "Server", (), {"serve_forever": lambda self: None, "shutdown": lambda self: None})())
    monkeypatch.setattr(lanes_check, "run", measure)
    monkeypatch.setattr(sys, "argv", ["lanes_check.py", "--install", str(tmp_path)])

    assert lanes_check.main() == 0
    assert measured[0] == (lanes_check.CHARACTER_ADAPTER, agent_lora)
    character, agent = measured[1]
    assert character == agent == lanes_check.CHARACTER_ADAPTER
    assert character


@pytest.mark.parametrize("weight", [1, 3])
def test_window_prompt_fills_the_budget_without_overflow(weight: int) -> None:
    # A deterministic counter with growing costs for later line numbers.
    # Verify maximal filling, rather than repeating the search algorithm.
    def count(text):
        return len(text.encode("utf-8")) + weight * sum(c.isdigit() for c in text)

    pool = 65536
    tokens, prompt = lanes_check.window_prompt(pool, count)
    budget = pool - 30 - 256 - 200
    assert tokens == count(prompt) <= budget
    lines = prompt.count("def f")
    next_prompt = prompt.replace(lanes_check.code(lines), lanes_check.code(lines + 1))
    assert count(next_prompt) > budget
    assert tokens > pool - 1024
