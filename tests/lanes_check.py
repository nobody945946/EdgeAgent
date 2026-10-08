"""The two inference lanes on the real laptop (run by the laptop's agent with
EdgeAgent's service stopped; it reads the owner's config and changes nothing).

Loads the model the way a SilentTalk lease does (two slots in one pool, the
owner's context size and GPU layers), then measures:
  memory   - dedicated and shared GPU memory of llama-server, free VRAM, free RAM
  lines    - a character line alone, while the Agent generates, and while the
             Agent reads a long prompt (time to the first token and in all)
  window   - the Agent reading a prompt that fills its whole window while the
             character holds a cache: it must succeed
once with the character's own adapter (two adapters, as shipped now) and once
with one adapter for both (as a joint adapter would run). Prints one JSON
object per measurement and a summary line.

    .venv\\Scripts\\python.exe tests\\lanes_check.py --install <the deployed EdgeAgent folder>

--install is the folder EdgeAgent is deployed in (its config.json, models\\
and llama-server); the check itself may run from another checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from edge_agent.config import load_config
from edge_agent.control import Handler
from edge_agent.core import EdgeAgent
from edge_agent.runtime import CHARACTER_ADAPTER, CONTROL_PORT, CORE_ROOT, DEFAULT_MODEL
from edge_agent.service import ExclusiveHTTPServer

URL = f"http://127.0.0.1:{CONTROL_PORT}"
HISTORY = [m for i in range(40) for m in (
    {"role": "user", "content": f"今天第 {i} 局打得怎樣？"},
    {"role": "assistant", "content": f"第 {i} 局還行，補刀差了一點。"})]
CODE_LINE = "def f{i}(x):\n    return x * {i} + {j}  # 計算第 {i} 項\n"


def gpu() -> dict[str, int]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True, timeout=10).stdout.split(",")
    return {"vram_used_mib": int(out[0]), "vram_free_mib": int(out[1])}


def process_memory() -> dict[str, float]:
    """Windows' own counters for the llama-server processes: dedicated and
    shared GPU memory (shared means it spilled out of the GPU), and free RAM."""
    script = ("$ids = (Get-Process llama-server -ErrorAction SilentlyContinue).Id; "
              "$g = (Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage','\\GPU Process Memory(*)\\Shared Usage')"
              ".CounterSamples | ? { $i = $_.InstanceName; $ids | ? { $i -like ('pid_' + $_ + '_*') } }; "
              "$d = ($g | ? Path -like '*dedicated usage' | Measure-Object CookedValue -Sum).Sum; "
              "$s = ($g | ? Path -like '*shared usage' | Measure-Object CookedValue -Sum).Sum; "
              "$m = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory; "
              "'{0} {1} {2}' -f [double]$d, [double]$s, $m")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=120, check=False).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if len(out) != 3:
        return {}
    return {"dedicated_mib": round(float(out[0]) / 2**20, 1), "shared_mib": round(float(out[1]) / 2**20, 1),
            "ram_free_mib": round(float(out[2]) / 1024, 1)}


def stream(body: dict, headers: dict) -> dict[str, object]:
    request = urllib.request.Request(URL + "/v1/chat/completions", json.dumps({**body, "stream": True}).encode(),
                                     {"Content-Type": "application/json", **headers})
    started, first, chunks = time.monotonic(), None, 0
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            for line in response:
                if line.startswith(b"data: {") and b'"choices"' in line:
                    delta = json.loads(line[6:])["choices"][0]["delta"]
                    if delta.get("content") or delta.get("reasoning_content"):
                        chunks += 1
                        first = first or round(time.monotonic() - started, 2)
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "seconds": round(time.monotonic() - started, 2)}
    return {"status": 200, "first_s": first, "seconds": round(time.monotonic() - started, 2), "chunks": chunks}


def code(lines: int) -> str:
    return "".join(CODE_LINE.format(i=i, j=i % 7) for i in range(lines))


def run(core: EdgeAgent, lease_lora: str, config: dict) -> dict[str, object]:
    status = core.switch("character", os.getpid(), {"model": DEFAULT_MODEL, "lora": lease_lora,
                                                    "context_size": 32768, "port": 18088})
    assert status["ready"] and status["agent_slot"] == 1, status
    role = {"X-Character-Owner": str(os.getpid())}
    agent = {"Authorization": f"Bearer {core.inference.access.agent_key}"}
    pool = int(status["context_size"])
    result: dict[str, object] = {"lease_lora": lease_lora, "agent_lora": status["lora"], "pool": pool,
                                 "gpu_layers": config["gpu_layers"]}

    def line(turn: int) -> dict[str, object]:
        return stream({"messages": [{"role": "system", "content": "你是機魂，在 Discord 語音裡陪主人聊天，回答簡短自然。"},
                                    *HISTORY, {"role": "user", "content": f"那第 {turn} 局呢？說兩句就好。"}],
                       "chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 60,
                       "lora": [{"id": 0, "scale": 1.0}]}, role)

    def task(text: str, thinking: bool, max_tokens: int) -> dict[str, object]:
        with core.inference.access.agent_turn():
            return stream({"messages": [{"role": "user", "content": text}],
                           "chat_template_kwargs": {"enable_thinking": thinking}, "max_tokens": max_tokens}, agent)

    def beside(job, delay: float, turn: int) -> dict[str, object]:
        out: dict[str, object] = {}
        thread = threading.Thread(target=lambda: out.update(agent=job()))
        thread.start()
        time.sleep(delay)
        out["character"] = line(turn)
        thread.join()
        return out

    decode = lambda: task("請詳細解釋快速排序的每一步，越長越好。", True, 1500)
    line(0)
    result["memory"] = {**gpu(), **process_memory()}
    result["alone"] = [line(i) for i in (1, 2, 3)]
    result["during_decode"] = beside(decode, 5, 4)
    result["during_prompt"] = beside(lambda: task(f"# a\n{code(1800)}\n用一句話總結這個檔案。", False, 30), 3, 5)
    # The whole window: a prompt the size of the pool less its output, while the character holds a cache.
    line(6)
    # The pool less the output, the margin and the template, counted by the
    # server's own tokenizer on the whole prompt (later lines have longer
    # numbers, so a count of the first lines falls short: the dev box's dry
    # run sent more than the pool and got 400).
    budget = pool - 30 - 256 - 200

    def count(text: str) -> int:
        return len(core.router.request("/tokenize", {"content": text, "model": "local"})["tokens"])

    per_line = count(code(100)) / 100
    lines = int(budget / per_line)
    while (tokens := count(prompt := f"# b\n{code(lines)}\n用一句話總結這個檔案。")) > budget:
        lines -= int((tokens - budget) / per_line) + 1
    result["full_window"] = {"prompt_tokens": tokens, **task(prompt, False, 30)}
    result["after_full_window"] = line(7)
    result["memory_after"] = {**gpu(), **process_memory()}
    return result


def main() -> int:
    from edge_agent.processes import listening_pid

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install", type=Path, default=CORE_ROOT, help="the deployed EdgeAgent folder")
    parser.add_argument("--memory-only", action="store_true",
                        help="only the memory of the llama-server already running (the baseline), then exit")
    options = parser.parse_args()
    if options.memory_only:
        print(json.dumps({"event": "memory_baseline", **gpu(), **process_memory()}), flush=True)
        return 0
    install = options.install.resolve()
    for port in (CONTROL_PORT, 18089):
        if listening_pid(port) is not None:
            raise RuntimeError(f"port {port} is in use: stop EdgeAgent's service first")
    owner = load_config(install / "config.json")  # read only; the check runs in a temporary core
    from edge_agent import load_plan, router

    deployed = install / ".runtime/deployment.json"
    if deployed.is_file():  # the install's own llama-server, for the router and its model
        server = str((install / json.loads(deployed.read_text(encoding="utf-8"))["server"]).resolve())
        load_plan.find_llama_server = router.find_llama_server = lambda: server
    with tempfile.TemporaryDirectory(prefix="edge-lanes-") as directory:
        core = EdgeAgent(Path(directory))
        core.model_dir = (install / "models").resolve()
        core.chat.bridge.core_dir = CORE_ROOT
        keep = ("model", "lora", "context_size", "gpu_layers", "low_vram", "max_output_tokens", "working_context_size")
        core.config = {**core.config, **{k: owner[k] for k in keep if k in owner}}
        Handler.core = core
        server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        results = []
        try:
            for lease_lora in (CHARACTER_ADAPTER, str(core.config["lora"])):
                results.append(run(core, lease_lora, core.config))
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                core.switch("base", os.getpid())
        finally:
            core.chat.close()
            core._stop_child()
            server.shutdown()
    print(json.dumps({"event": "lanes_check_done", "runs": len(results)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
