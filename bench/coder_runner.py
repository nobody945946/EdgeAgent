"""Run one bench task through the host-driven coder (edge_agent.coder) instead
of the Agent's loop. Sandbox side only; same contract as agent_runner.py:
the result is the last stdout line as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from edge_agent import coder


def main() -> None:
    spec = json.loads(os.environ["BENCH_SPEC"])
    relay = subprocess.Popen(
        [sys.executable, "/opt/bench/relay.py", "--listen", "tcp:127.0.0.1:18089",
         "--connect", "unix:/sock/model.sock"],
        stdout=subprocess.PIPE,
    )
    assert relay.stdout is not None
    relay.stdout.readline()
    options = spec.get("coder") or {}
    model = coder.Model("http://127.0.0.1:18089", thinking=spec.get("effort", "off") != "off")
    started = time.monotonic()
    # Leave the last minute for restoring files and reporting.
    deadline = started + max(60, spec["timeout_s"] - 60)
    try:
        result = coder.solve(Path("/work"), spec["prompt"], model, deadline=deadline,
                             samples=int(options.get("samples", 4)), rounds=int(options.get("rounds", 3)),
                             temperature=float(options.get("temperature", 0.8)))
        outcome, answer, extra = "done", result.answer, {
            "coder": {"outcome": result.outcome, "rounds": result.rounds, "samples": result.samples,
                      "changed": result.changed, "trace": result.trace}}
    except Exception as exc:  # noqa: BLE001 - a crash is a result here
        outcome, answer, extra = "error", "", {"notice": f"{type(exc).__name__}: {exc}"[:300]}
    relay.terminate()
    print(json.dumps({
        "outcome": outcome, "reason": "", "seconds": round(time.monotonic() - started, 1),
        "approvals": 0, "turns": model.calls, "tools": {}, "tool_errors": {},
        "usage": {"input": model.tokens["input"], "output": model.tokens["output"], "cacheRead": 0},
        "answer": answer, "unverified": [], **extra,
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
