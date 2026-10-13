"""Agent bench: the dev box plays the laptop, the agent never touches the host.

Host side only. Starts one llama-server for a named configuration (args from
`edge_agent.runtime.build_server_args`, the product's owner), relays it to a
Unix socket, and runs every task x repeat in two throwaway containers:
the agent (no network, read-only root, only its workspace copy writable)
and then a fresh grader. Results append to
.runtime/bench/<config>/results.jsonl; reruns skip finished jobs.

    python bench/run.py --build                      # sandbox image
    python bench/run.py --config qwen35-4b --tasks 'code-*' --repeats 3
    python bench/run.py --containment                # escape checks only
    python bench/run.py --selftest --tasks '*'       # graders: fail untouched, pass solved
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge_agent.models import model_metadata  # noqa: E402
from edge_agent.runtime import build_server_args  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laptop  # noqa: E402
from mine_repo import REPOS  # noqa: E402

BENCH = ROOT / "bench"
STATE = ROOT / ".runtime/bench"
IMAGE = "edgeagent-bench:1"
TRAIN_IMAGE = "edgeagent-train:1"
VRAM_EXIT = 3  # the GPU had no room for this run
CONVERSATION_TURNS = 2  # a scenario conversation may take two turns' budget in all
# What llama.cpp writes at the end of a thought the reasoning budget cut short
# (a config with --reasoning-budget): 21% of teacher-cards-1024's thoughts
# stopped mid-sentence. bench/dataset.py never trains on such a thought.
BUDGET_CUT = "[思考預算用完]"
REFIT_MIN = 30  # --fit-slots: minutes a run with fewer slots than asked keeps going
SERVER = Path(os.environ.get(
    "BENCH_LLAMA_SERVER",
    "/home/clawd/SilentTalk/.runtime/tools/llama.cpp-b11193-src/build-cuda/bin/llama-server",
))
MODELS = Path(os.environ.get("BENCH_MODELS", "/home/clawd/SilentTalk/.runtime/models/llm"))
DOWNLOADS = Path(os.environ.get("BENCH_DOWNLOADS", STATE / "downloads"))
DECOY = Path.home() / "edgeagent-bench-decoy"


def sandbox(name: str, mounts: list[tuple[Path, str, str]], env: dict[str, str],
            command: list[str], *, memory: str = "6g", image: str = IMAGE,
            web: bool = False) -> list[str]:
    """The one docker command line for agent and grader: no network, read-only
    root, no capabilities, unprivileged user, explicit mounts only. With `web`,
    the sandbox's resolver is its own loopback, where the stand-in web answers
    every name (web_fixture.py), and its servers may take ports 53, 80 and 443."""
    args = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,exec,size=2g,mode=1777",
            "--tmpfs", "/home/bench:rw,size=512m,mode=1777",
            "--user", f"{os.getuid()}:{os.getgid()}", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "512",
            "--memory", memory, "--memory-swap", memory, "--cpus", "4",
            "-e", "HOME=/home/bench", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "PYTHONPATH=/opt/edgeagent", "-w", "/work"]
    for source, target, mode in mounts:
        args += ["-v", f"{source}:{target}:{mode}"]
    if web:
        args += ["--dns", "127.0.0.1", "--sysctl", "net.ipv4.ip_unprivileged_port_start=0"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return [*args, image, *command]


# Get-Process, Get-PSDrive and Get-ComputerInfo answering with the laptop as
# cmdlets of PowerShell's own Microsoft.PowerShell.Management (bench/pshome).
MANAGEMENT = BENCH / "pshome/Microsoft.PowerShell.Management"
_CORE_LOCK = threading.Lock()


def laptop_core(image: str = IMAGE) -> Path:
    """The folder holding LaptopCore.dll, compiled from LaptopCore.cs by the
    image's own PowerShell once per source and image."""
    source = MANAGEMENT / "LaptopCore.cs"
    image_id = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                              capture_output=True, text=True, check=True).stdout.strip()
    out = STATE / "laptop-core" / hashlib.sha256(source.read_bytes() + image_id.encode()).hexdigest()[:16]
    with _CORE_LOCK:
        if not (out / "LaptopCore.dll").is_file():
            work = out.with_name(out.name + ".tmp")
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True)
            subprocess.run(
                ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                 "-e", "HOME=/tmp", "-v", f"{source}:/src/LaptopCore.cs:ro", "-v", f"{work}:/out", image,
                 "pwsh", "-NoProfile", "-NonInteractive", "-Command",
                 "Add-Type -Path /src/LaptopCore.cs -OutputAssembly /out/LaptopCore.dll -OutputType Library"],
                capture_output=True, check=True, timeout=300)
            work.rename(out)
    return out


def code_mounts(bridge: Path | None = None,
                product: Path = ROOT / "edge_agent") -> list[tuple[Path, str, str]]:
    """What the agent's sandbox may see: the product code and the two bench
    files it runs. Never the tasks (hidden tests and solutions live there).
    `bridge` overlays a variant of the product's agent_bridge.mjs."""
    mounts = [(product, "/opt/edgeagent/edge_agent", "ro"),
              (BENCH / "relay.py", "/opt/bench/relay.py", "ro"),
              (BENCH / "agent_runner.py", "/opt/bench/agent_runner.py", "ro"),
              # Stand-in MCP servers (stock, drone, everyday ones) for tasks that enable them.
              (BENCH / "mcp_eval", "/opt/bench/mcp", "ro"),
              # The laptop's Windows commands (bench/laptop.py).
              (BENCH / "windows", "/opt/bench/windows", "ro"),
              (MANAGEMENT / "Microsoft.PowerShell.Management.psd1",
               "/opt/pwsh/Modules/Microsoft.PowerShell.Management/Microsoft.PowerShell.Management.psd1", "ro"),
              (laptop_core(), "/opt/bench/core", "ro")]
    if bridge is not None:
        mounts.append((bridge, "/opt/edgeagent/edge_agent/agent_bridge.mjs", "ro"))
    return mounts


def bridge_variant(config: dict, run_dir: Path, product: Path) -> Path | None:
    """The product bridge with the config's extra system-prompt lines, so a
    prompt change is measured before it is made in the product."""
    extra = config.get("system_prompt_append")
    if not extra:
        return None
    source = (product / "agent_bridge.mjs").read_text(encoding="utf-8")
    anchor = "appendSystemPrompt: ["
    if source.count(anchor) != 1:
        raise SystemExit("agent_bridge.mjs changed; update bridge_variant")
    lines = "".join(json.dumps(line, ensure_ascii=False) + ", " for line in extra)
    path = run_dir / "agent_bridge.mjs"
    path.write_text(source.replace(anchor, anchor + lines), encoding="utf-8")
    return path


def build_image() -> None:
    context = STATE / "image"
    context.mkdir(parents=True, exist_ok=True)
    for name in ("node-v22.23.3-linux-x64.tar.xz", "powershell-7.6.6-linux-x64.tar.gz",
                 "ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz",
                 "fd-v10.5.0-x86_64-unknown-linux-musl.tar.gz"):
        if not (context / name).is_file():
            shutil.copyfile(DOWNLOADS / name, context / name)
    shutil.copyfile(BENCH / "Dockerfile", context / "Dockerfile")
    for name in ("pi-runtime.json", "pi-runtime-lock.json"):
        shutil.copyfile(ROOT / "edge_agent" / name, context / name)
    subprocess.run(["docker", "build", "-t", IMAGE, str(context)], check=True)
    # The training tasks' sandbox (make_tool_tasks.py): the same, plus a few
    # common test dependencies.
    train_context = STATE / "image-train"
    train_context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BENCH / "Dockerfile.train", train_context / "Dockerfile")
    subprocess.run(["docker", "build", "--build-arg", f"BASE={IMAGE}", "-t", TRAIN_IMAGE,
                    str(train_context)], check=True)
    for name, repo in REPOS.items():
        repo_context = STATE / f"image-{name}"
        repo_context.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BENCH / "Dockerfile.repo", repo_context / "Dockerfile")
        for requirements in repo.glob("requirements*.txt"):
            shutil.copyfile(requirements, repo_context / requirements.name)
        subprocess.run(["docker", "build", "--build-arg", f"BASE={IMAGE}", "-t",
                        f"edgeagent-bench-{name}:1", str(repo_context)], check=True)


JUDGE_ONLY = ("must", "must_not", "checks")  # a scenario's judging, read when judged


def task_digest(task_dir: Path) -> str:
    """What a job was played on: every file of its task directory, without
    what only the scenario judge reads (changing a card's must never
    re-records its conversation)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in task_dir.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if path == task_dir / "task.json":
            task = json.loads(data)
            data = json.dumps({k: v for k, v in task.items() if k not in JUDGE_ONLY},
                              ensure_ascii=False, sort_keys=True).encode()
        digest.update(path.relative_to(task_dir).as_posix().encode() + b"\0" + data + b"\0")
    return digest.hexdigest()[:16]


def drop_superseded(run_dir: Path) -> None:
    """Results of a task that changed since they were recorded (2026-10-08:
    cards got the files their users talk about) are no result of the task as
    it is, and those of a task that is gone (a retired card) are no result of
    any: drop them with their recording, trajectory and verdict, so a resumed
    run plays a changed task again. Rows from before digests are kept."""
    results = run_dir / "results.jsonl"
    if not results.is_file():
        return
    rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    current: dict[str, str | None] = {}
    for row in rows:
        if row["task"] not in current:
            task_dir = TASK_ROOT / row["task"]
            current[row["task"]] = task_digest(task_dir) if task_dir.is_dir() else None
    stale = [row for row in rows if row.get("task_digest") and row["task_digest"] != current[row["task"]]]
    if not stale:
        return
    jobs = {f"{row['task']}-r{row['rep']}" for row in stale}
    keep = [row for row in rows if row not in stale]
    results.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in keep), encoding="utf-8")
    for job in jobs:
        shutil.rmtree(run_dir / "records" / job, ignore_errors=True)
        (run_dir / "traj" / f"{job}.json").unlink(missing_ok=True)
    verdicts = run_dir / "verdicts.jsonl"
    if verdicts.is_file():
        lines = verdicts.read_text(encoding="utf-8").splitlines()
        verdicts.write_text("".join(line + "\n" for line in lines if json.loads(line)["job"] not in jobs),
                            encoding="utf-8")
    print(f"{len(stale)} results were recorded on a task that has changed or is gone since; dropped", flush=True)


def free_vram_mib() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.split()[0])


class Server:
    """llama-server for one configuration plus the host end of the relay."""

    def __init__(self, config: dict, run_dir: Path, parallel: int, port: int,
                 fit_slots: bool = False) -> None:
        self.port = port
        base, slot = config["vram_base_mib"], config["vram_slot_mib"]
        # fit_slots: up to `parallel` slots, as many as the GPU holds now
        # (another tenant's share changes by the hour); at least one.
        free = free_vram_mib()
        fits = min(parallel, (free - base) // slot) if fit_slots else parallel
        if fits < 1 or free < base + slot * fits:
            # Another tenant holds the GPU: hand the queue back at once, no
            # waiting by asking again (owner, 2026-10-14); the session that
            # the stopped queue wakes decides when to start again.
            print(f"needs {base + slot * (1 if fit_slots else parallel)} MiB free VRAM, has {free}; handing back",
                  flush=True)
            raise SystemExit(VRAM_EXIT)
        self.parallel = parallel = fits
        need = base + slot * parallel  # weights and buffers, plus a full context window per slot
        # Relative to the student models folder; teachers live next to it.
        model = (MODELS / config["model"]).resolve()
        # An adapter (config "lora", next to the model) is loaded the way the
        # product loads one: applied to every request.
        args = build_server_args(
            str(SERVER), model, port=port, context_size=config["context_size"],
            threads=8, gpu_layers=99, low_vram=config.get("low_vram", True),
            lora_paths=[(MODELS / config["lora"]).resolve()] if config.get("lora") else (),
        )
        # Bench-only: N agents share one server; each slot keeps the full window.
        args[args.index("--parallel") + 1] = str(parallel)
        args[args.index("--ctx-size") + 1] = str(config["context_size"] * parallel)
        args += ["--fit", "off", *config.get("extra_args", [])]
        if "--reasoning-budget" in args:
            args += ["--reasoning-budget-message", BUDGET_CUT]
        self.log = open(run_dir / "server.log", "ab")
        self.process = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                # Another tenant took the GPU between our check and the load
                # (2026-10-08: 21.8 GB free, gone 9 s later): not a failure
                # of this run; the queue waits and starts it again.
                if free_vram_mib() < need:
                    raise SystemExit(VRAM_EXIT)
                raise SystemExit(f"llama-server exited; see {run_dir / 'server.log'}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(2)
        else:
            raise SystemExit("llama-server did not become ready")
        self.sock_dir = run_dir / "sock"
        self.sock_dir.mkdir(exist_ok=True)
        self.relay = subprocess.Popen(
            [sys.executable, str(BENCH / "relay.py"), "--listen",
             f"unix:{self.sock_dir / 'model.sock'}", "--connect", f"tcp:127.0.0.1:{port}"],
            stdout=subprocess.PIPE)
        assert self.relay.stdout is not None
        self.relay.stdout.readline()

    def speed(self) -> float:
        body = json.dumps({"prompt": "Count from 1 to 200 separated by spaces:",
                           "n_predict": 128, "temperature": 0}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/completion", body,
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as response:
            timings = json.loads(response.read())["timings"]
        return round(timings["predicted_per_second"], 1)

    def close(self) -> None:
        for process in (self.relay, self.process):
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
        self.log.close()


STOPPING = threading.Event()


def close_on_term(*servers: Server | None) -> None:
    """A stopped step must not leave llama-server holding VRAM: the queue
    stops a step with SIGTERM, and Python's default action skips `finally`
    (2026-10-07: a stopped card writer left 21 GB to an orphan server).
    While it closes, jobs that fail against the closing servers are not
    results (STOPPING)."""
    def stop(*_: object) -> None:
        STOPPING.set()
        for server in servers:
            if server is not None:
                server.close()
        os._exit(143)

    signal.signal(signal.SIGTERM, stop)


TASK_ROOT = BENCH / "tasks"


def load_tasks(pattern: str) -> list[Path]:
    found = sorted(p.parent for p in TASK_ROOT.glob("*/task.json"))
    patterns = pattern.split(",")
    return [p for p in found if any(fnmatch.fnmatch(p.name, x) for x in patterns)]


def prepare(task_dir: Path, task: dict, workspace: Path, original: Path) -> None:
    for path in (workspace, original):
        if path.exists():
            shutil.rmtree(path)
    workspace.mkdir(parents=True)
    if task.get("repo"):
        # The repository at the task's base commit, without model weights.
        archive = subprocess.run(["git", "-C", str(REPOS[task["repo"]]), "archive",
                                  task["base"]], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(workspace), "--exclude=*.gguf",
                        "--exclude=*.onnx"], input=archive, check=True)
    if (task_dir / "workspace").is_dir():
        shutil.copytree(task_dir / "workspace", workspace, symlinks=True,
                        dirs_exist_ok=True)
    if (task_dir / "setup.py").is_file():
        subprocess.run([sys.executable, str(task_dir / "setup.py"), str(workspace)], check=True)
    if task.get("git"):
        git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost"]
        subprocess.run([*git, "init", "-q"], cwd=workspace, check=True)
        subprocess.run([*git, "add", "-A"], cwd=workspace, check=True)
        subprocess.run([*git, "commit", "-qm", "start"], cwd=workspace, check=True)
    if (task_dir / "dirty").is_dir():
        # The user's uncommitted work in progress.
        shutil.copytree(task_dir / "dirty", workspace, dirs_exist_ok=True)
    shutil.copytree(workspace, original, symlinks=True)


def last_json(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except ValueError:
            continue
    return {}


def answer_record(result: dict, trajectory: list[dict] | None, memory: Path | None = None) -> dict:
    """What a check may grade besides the workspace: the answer the asker
    read, the tools the Agent called (name and arguments), how it ended, and
    the owner's AGENTS.md as the conversation left it (`memory`)."""
    calls = [{"name": item.get("name"), "arguments": item.get("arguments")}
             for message in trajectory or [] if message.get("role") == "assistant"
             for item in message.get("content") or []
             if isinstance(item, dict) and item.get("type") == "toolCall"]
    return {"answer": result.get("answer", ""), "calls": calls,
            "outcome": result.get("outcome"), "approvals": result.get("approvals", 0),
            "unverified": result.get("unverified", []),
            "memory": memory.read_text(encoding="utf-8") if memory is not None and memory.is_file() else ""}


def grade_workspace(name: str, task_dir: Path, original: Path, workspace: Path,
                    answer: dict | None = None) -> dict:
    image = json.loads((task_dir / "task.json").read_text(encoding="utf-8")).get("image", IMAGE)
    # The answer goes to the grader as /result/answer.json (BENCH_ANSWER).
    result_dir = workspace.parent.parent / "answers" / name
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "answer.json").write_text(json.dumps(answer or {"answer": "", "calls": []},
                                                       ensure_ascii=False), encoding="utf-8")
    grader = subprocess.run(
        sandbox(name + "-grade", [(task_dir, "/task", "ro"), (original, "/orig", "ro"),
                                  (workspace, "/work", "ro"), (result_dir, "/result", "ro"),
                                  (BENCH / "grade.py", "/opt/bench/grade.py", "ro")],
                {}, ["python", "/opt/bench/grade.py"], memory="4g", image=image),
        capture_output=True, text=True, timeout=600)
    return last_json(grader.stdout) or {"passed": False, "problems": ["grader-crash"],
                                        "check_tail": grader.stderr[-500:]}


def selftest(pattern: str) -> None:
    """Every task must fail untouched and pass with its reference solution
    (solution/ overlay, minus paths in solution_delete.txt)."""
    bad = 0
    for task_dir in load_tasks(pattern):
        task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        base = STATE / "selftest" / task_dir.name
        workspace, original = base / "ws", base / "orig"
        prepare(task_dir, task, workspace, original)
        untouched = grade_workspace(f"eab-self-{task_dir.name}", task_dir, original, workspace)
        if (task_dir / "solution").is_dir():
            shutil.copytree(task_dir / "solution", workspace, dirs_exist_ok=True)
        if (task_dir / "solution_delete.txt").is_file():
            for rel in (task_dir / "solution_delete.txt").read_text().split():
                (workspace / rel).unlink(missing_ok=True)
        if (task_dir / "solution.py").is_file():
            subprocess.run([sys.executable, str(task_dir / "solution.py"), str(workspace)],
                           check=True)
        # A task graded on the answer carries a reference answer.
        reference = task_dir / "solution_answer.json"
        answer = (json.loads(reference.read_text(encoding="utf-8"))
                  if reference.is_file() else None)
        solved = grade_workspace(f"eab-self-{task_dir.name}", task_dir, original, workspace,
                                 answer)
        ok = not untouched["passed"] and solved["passed"]
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {task_dir.name}: untouched={untouched['passed']} "
              f"solved={solved['passed']} {solved.get('problems')} "
              f"{'' if solved['passed'] else solved.get('check_tail', '')[-400:]}", flush=True)
        shutil.rmtree(base)
    if bad:
        raise SystemExit(f"{bad} task(s) failed the selftest")


def run_sampling(config: dict, given: str | None) -> dict[str, float]:
    """The Agent's temperature per mode: the product samples an adapter at
    what its file says (edge_agent.models SAMPLING; nothing said: the bridge's
    own); --sampling sets it for a sweep that measures which is best."""
    if given:
        if "=" not in given:
            return {"off": float(given), "thinking": float(given)}
        return {mode: float(value) for mode, value in (part.split("=", 1) for part in given.split(","))}
    if not config.get("lora"):
        return {}
    return dict(model_metadata((MODELS / config["lora"]).resolve()).get("sampling", {}))


def run_job(task_dir: Path, rep: int | str, config_name: str, config: dict, run_dir: Path,
            sock_dir: Path, keep: bool, bridge: Path | None, product: Path,
            record_port: int | None = None, simulator_sock: Path | None = None,
            takeover: dict | None = None, sampling: dict | None = None) -> dict:
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    job = f"{task_dir.name}-r{rep}"
    relay = None
    replay: list[str] = []
    if takeover is not None:
        # The student's answers, given back word for word until this run's
        # model takes over (relay.py --replay).
        path = run_dir / "replay" / f"{job}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(takeover["replies"], ensure_ascii=False), encoding="utf-8")
        replay = ["--replay", str(path)]
    if record_port is not None:
        # This job's own relay, keeping every request the Agent sends (the
        # exact prompts training rows are rendered from).
        # Unix socket paths are limited to ~108 bytes: a short name per job.
        sock_dir = run_dir / "s" / hashlib.sha1(job.encode()).hexdigest()[:10]
        sock_dir.mkdir(parents=True, exist_ok=True)
        # A rerun (a crash, a card changed) records from 0000 again: requests
        # of the earlier attempt must not outlive it.
        shutil.rmtree(run_dir / "records" / job, ignore_errors=True)
        relay = subprocess.Popen(
            [sys.executable, str(BENCH / "relay.py"), "--listen",
             f"unix:{sock_dir / 'model.sock'}", "--connect", f"tcp:127.0.0.1:{record_port}",
             "--record", str(run_dir / "records" / job), *replay], stdout=subprocess.PIPE)
        assert relay.stdout is not None
        relay.stdout.readline()
    workspace, original = run_dir / "ws" / job, run_dir / "orig" / job
    prepare(task_dir, task, workspace, original)
    # The owner's global AGENTS.md lives in Pi's agentDir, which the read-only
    # sandbox must be able to write: this job's own folder, holding what the
    # task says the owner asked to remember before (memory/AGENTS.md).
    memory = run_dir / "memory" / job
    shutil.rmtree(memory, ignore_errors=True)
    memory.mkdir(parents=True)
    if (task_dir / "memory" / "AGENTS.md").is_file():
        shutil.copyfile(task_dir / "memory" / "AGENTS.md", memory / "AGENTS.md")
    # time_scale: a config that is slower on the bench GPU only for reasons the
    # comparison is not about (e.g. unquantized weights) gets a longer budget,
    # so the score measures quality, not speed.
    timeout = round(task.get("timeout_s", 900) * config.get("time_scale", 1))
    spec = {**{k: config[k] for k in ("model", "context_size", "max_output_tokens",
                                      "working_context_size", "effort")},
            # The model the Agent says it is (the bridge's runtime section): a
            # teacher recording rows for the product speaks as the product's model.
            **({"model": config["self_model"]} if config.get("self_model") else {}),
            "vision": config.get("vision", False), "prompt": task.get("prompt", ""),
            # The laptop, not the bench box: what the runtime section states, and
            # what the Windows commands answer in the sandbox (bench/laptop.py).
            "hardware": laptop.HARDWARE, "machine": laptop.machine(task_dir.name),
            # The APIs a scenario's owner registered (scenario_cards.py, family J), with made-up keys.
            "apis": task.get("apis", []),
            # A scenario (bench/scenario_cards.py) is a conversation with a simulated user.
            **({"scenario": task["scenario"]} if task.get("scenario") else {}),
            # Who asked (permissions.SCOPES): the owner's change or read task, or a public one.
            "scope": task.get("scope", "change"),
            "search_tools": config.get("search_tools", False),
            # The product's permission rules (bridge_rules); None = the UI default.
            # A task may set its own (an MCP task: no files, no shell, no web).
            "permissions": task.get("permissions", config.get("permissions")),
            "mcp_servers": task.get("mcp_servers"),
            # A scenario's timeout is per turn; the conversation gets twice that.
            "timeout_s": timeout, "conversation_s": timeout * (CONVERSATION_TURNS if task.get("scenario") else 1),
            # A takeover: the user's lines the student heard, until the takeover.
            **({"script": takeover["script"]} if takeover else {}),
            # The Agent adapter's temperature per mode, as the product samples it.
            "sampling": sampling or {}}
    name = f"eab-{config_name}-{job}".replace("_", "-")[:120]
    mounts, env, web = web_fixture(task_dir, task)
    if simulator_sock is not None and task.get("scenario"):
        mounts = [*mounts, (simulator_sock, "/simsock", "ro")]
    command = sandbox(name, [(workspace, "/work", "rw"), (sock_dir, "/sock", "ro"),
                             *code_mounts(bridge, product), *mounts,
                             (memory, "/opt/edgeagent/.runtime/pi-state", "rw")],
                      {"BENCH_SPEC": json.dumps(spec, ensure_ascii=False), **env},
                      ["sh", "-c", "python /opt/bench/web_fixture.py /web > /tmp/web.log 2>&1 & "
                       "sleep 1; exec python /opt/bench/agent_runner.py"] if web
                      else ["python", "/opt/bench/agent_runner.py"],
                      image=task.get("image", IMAGE), web=web)
    started = time.monotonic()
    try:
        agent = subprocess.run(command, capture_output=True, text=True,
                               timeout=spec["conversation_s"] + 180)
        result = last_json(agent.stdout) or {"outcome": "crash",
                                             "notice": agent.stderr[-300:]}
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        result = {"outcome": "killed"}
    wall = round(time.monotonic() - started, 1)
    if relay is not None:
        relay.terminate()
        relay.wait(timeout=30)
        shutil.rmtree(sock_dir, ignore_errors=True)
    trajectory = result.pop("trajectory", None)
    grade = grade_workspace(name, task_dir, original, workspace,
                            answer_record(result, trajectory, memory / "AGENTS.md"))
    if trajectory is not None:
        (run_dir / "traj").mkdir(exist_ok=True)
        (run_dir / "traj" / f"{job}.json").write_text(
            json.dumps(trajectory, ensure_ascii=False), encoding="utf-8")
    if not keep:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(original, ignore_errors=True)
        shutil.rmtree(memory, ignore_errors=True)
    row = {"task": task_dir.name, "kind": task.get("kind", ""), "rep": rep,
           "config": config_name, "wall": wall, **result, "grade": grade,
           "passed": bool(grade.get("passed"))}
    if takeover is not None:
        row["takeover"] = {"from": takeover["from"], "at": takeover["at"], "of": takeover["of"],
                           "replay_differs": replay_differs(run_dir / "records" / job, takeover["at"], takeover["seen"])}
    return row


def model_requests(folder: Path) -> list[dict]:
    """A job's recorded requests to the Agent's model, in order (the
    simulated user's go through the same relay but carry no tools)."""
    requests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]
    return [request for request in requests if request.get("tools")]


def tool_results(request: dict) -> list[str]:
    return [json.dumps(m.get("content"), ensure_ascii=False) for m in request["messages"] if m.get("role") == "tool"]


def replay_differs(folder: Path, at: int, seen: list[str]) -> int:
    """How many tool results the replayed prefix got that differ from what
    the student got (a clock, a random name), as the takeover's first
    request shows them: the teacher took over a situation that is not quite
    the student's. All of them when the job never got that far."""
    live = model_requests(folder)
    if len(live) <= at:
        return max(1, len(seen))
    results = tool_results(live[at])
    return sum(a != b for a, b in zip(results, seen, strict=False)) + abs(len(results) - len(seen))


def takeover_plans(student: Path, at: list[float], patterns: str) -> list[tuple[Path, str, dict]]:
    """Learning from its own mistakes: every job a student failed in its
    recorded run (not passed, not done, or a scenario its judge failed),
    taken over by this run's model at the fractions `at` of the student's
    model calls. Its answers before that point are given back word for word
    (relay.py --replay), so the sandbox really runs what the student did and
    the teacher continues from where the student went. (task dir, rep label
    "<rep>t<point>", plan)."""
    verdicts = student / "verdicts.jsonl"
    judged = ({row["job"]: bool(row["passed"]) for row in map(json.loads, verdicts.read_text(encoding="utf-8").splitlines())}
              if verdicts.is_file() else {})
    rows = [json.loads(line) for line in (student / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    if any(row.get("kind") == "scenario" for row in rows) and not judged:
        raise SystemExit(f"{student} has scenarios but no verdicts: judge it first (bench/scenario_judge.py)")
    tasks = {task.name: task for task in load_tasks(patterns)}
    plans = []
    for row in rows:
        job = f"{row['task']}-r{row['rep']}"
        failed = (not row.get("passed") or row.get("outcome") != "done" or (row.get("grade") or {}).get("problems")
                  or (row.get("kind") == "scenario" and not judged.get(job)))
        if row["task"] not in tasks or not failed or row.get("takeover"):
            continue
        requests = model_requests(student / "records" / job)
        for point in sorted({min(len(requests) - 1, max(1, round(f * len(requests)))) for f in at}):
            if point < 1:
                continue
            replies = []
            for before, after in itertools.pairwise(requests[:point + 1]):
                seen = before["messages"]
                if after["messages"][:len(seen)] != seen or after["messages"][len(seen)].get("role") != "assistant":
                    break  # the history was rewritten (a compaction): no word-for-word replay
                replies.append(after["messages"][len(seen)])
            if len(replies) < point:
                continue
            users = sum(1 for message in requests[point]["messages"] if message.get("role") == "user")
            plans.append((tasks[row["task"]], f"{row['rep']}t{point}", {
                "from": job, "at": point, "of": len(requests), "replies": replies,
                "script": [turn["user"] for turn in (row.get("dialogue") or [])[:users]],
                "seen": tool_results(requests[point])}))
    return plans


def web_ca() -> Path:
    """The bench's own certificate authority (made once with the host's
    openssl): the stand-in web signs a certificate for each name with it, and
    the sandbox's clients trust it, so pages keep their https addresses."""
    folder = STATE / "webca"
    if not (folder / "ca.pem").is_file():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(folder / "ca.key"), "-out", str(folder / "ca.pem"), "-days", "3650",
                        "-subj", "/CN=EdgeAgent bench web", "-addext", "basicConstraints=critical,CA:TRUE",
                        "-addext", "keyUsage=critical,keyCertSign,cRLSign"], check=True, capture_output=True)
        (folder / "ca.key").chmod(0o644)  # the sandbox's user reads it; it signs nothing outside the sandbox
    return folder


def web_fixture(task_dir: Path, task: dict) -> tuple[list, dict[str, str], bool]:
    """A research task's stand-in web (bench/web_fixture.py): its pages (only
    web/, never the task's check), the CA it signs with and every client's
    trust in it, and the product's own search and loopback seams pointed at it."""
    if not task.get("web"):
        return [], {}, False
    ca = "/opt/bench/webca/ca.pem"
    return ([(task_dir / "web", "/web", "ro"), (BENCH / "web_fixture.py", "/opt/bench/web_fixture.py", "ro"),
             (web_ca(), "/opt/bench/webca", "ro")],
            {"EDGE_AGENT_SEARCH_URL": "http://127.0.0.1:8080/html/", "EDGE_AGENT_WEB_ALLOW_LOOPBACK": "1",
             "NODE_EXTRA_CA_CERTS": ca, "SSL_CERT_FILE": ca, "REQUESTS_CA_BUNDLE": ca}, True)


def containment(run_dir: Path) -> None:
    """Prove the sandbox, not the model's manners, protects the host."""
    DECOY.mkdir(exist_ok=True)
    (DECOY / "keep.txt").write_text("decoy\n")
    probe = run_dir / "probe"
    if probe.exists():
        shutil.rmtree(probe)
    probe.mkdir(parents=True)
    script = (
        "$r = @{}; "
        f"$r.home = Test-Path '{Path.home()}'; "
        f"$r.decoy = Test-Path '{DECOY}'; "
        f"try {{ Remove-Item -Recurse -Force '{DECOY}' -ErrorAction Stop; $r.removed = $true }} "
        "catch { $r.removed = $false }; "
        "try { New-Item -ItemType File /opt/edgeagent/edge_agent/x -ErrorAction Stop | Out-Null; "
        "$r.code_writable = $true } catch { $r.code_writable = $false }; "
        "try { (New-Object Net.Sockets.TcpClient).Connect('1.1.1.1', 443); $r.network = $true } "
        "catch { $r.network = $false }; "
        "$r.user = (id -u); "
        "$r | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        sandbox("eab-containment", [(probe, "/work", "rw"), *code_mounts()], {},
                ["pwsh", "-NoProfile", "-Command", script]),
        capture_output=True, text=True, timeout=120)
    found = last_json(out.stdout)
    intact = (DECOY / "keep.txt").read_text() == "decoy\n"
    ok = (found.get("home") is False and found.get("decoy") is False
          and found.get("code_writable") is False and found.get("network") is False
          and intact)
    print(json.dumps({"sandbox": found, "decoy_intact_on_host": intact, "contained": ok}))
    if not ok:
        raise SystemExit("containment FAILED; do not run the bench")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--containment", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--tasks", default="*")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--port", type=int, default=18189)
    parser.add_argument("--fit-slots", action="store_true",
                        help="--parallel is the most: start with as many slots as fit now; with fewer, "
                             f"hand back after {REFIT_MIN} minutes (exit {VRAM_EXIT}) to grow when started again")
    parser.add_argument("--keep", action="store_true", help="keep workspaces")
    parser.add_argument("--tag", help="run directory name (default: the config name)")
    parser.add_argument("--record", action="store_true",
                        help="keep each job's model requests in run_dir/records (training data)")
    parser.add_argument("--product", type=Path, default=ROOT / "edge_agent",
                        help="edge_agent package to test (e.g. a development worktree)")
    parser.add_argument("--takeover", type=Path,
                        help="a student's recorded run: take over each job it failed (implies --record)")
    parser.add_argument("--at", default="0.4,0.75",
                        help="--takeover: where, as fractions of the student's model calls")
    parser.add_argument("--sampling",
                        help="the Agent's temperature, both modes (0.6) or each (off=0.2,thinking=1.0); "
                             "default: what the config's adapter file says, as the product reads it")
    parser.add_argument("--task-root", type=Path,
                        help="task directory (default bench/tasks; the tool training tasks live in "
                             ".runtime/agentdata/tool-tasks)")
    args = parser.parse_args()
    if args.task_root:
        global TASK_ROOT
        TASK_ROOT = args.task_root.resolve()
    if args.build:
        build_image()
        return
    STATE.mkdir(parents=True, exist_ok=True)
    if args.containment:
        containment(STATE)
        return
    if args.selftest:
        selftest(args.tasks)
        return
    configs = json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))
    config = configs[args.config]
    sampling = run_sampling(config, args.sampling)
    run_dir = STATE / (args.tag or args.config)
    run_dir.mkdir(parents=True, exist_ok=True)
    containment(run_dir)
    results = run_dir / "results.jsonl"
    drop_superseded(run_dir)
    done = set()
    if results.is_file():
        for line in results.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            # A crash is the sandbox failing to report, not a result: rerun it.
            if row.get("outcome") != "crash":
                done.add((row["task"], row["rep"]))
    if args.takeover:
        args.record = True
        jobs = [plan for plan in takeover_plans(args.takeover.resolve(), [float(x) for x in args.at.split(",")],
                                                args.tasks) if (plan[0].name, plan[1]) not in done]
    else:
        jobs = [(t, r) for t in load_tasks(args.tasks) for r in range(args.repeats)
                if (t.name, r) not in done]
    print(f"{len(jobs)} jobs, {len(done)} already done; sampling {sampling or 'the bridge default'}", flush=True)
    if not jobs:
        return
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    # Pin the product code for this run: later edits never reach a run in
    # progress, and a resumed run keeps the code it started with.
    product = run_dir / "edge_agent"
    if not product.is_dir():
        shutil.copytree(args.product.resolve(), product,
                        ignore=shutil.ignore_patterns("__pycache__"))
    bridge = bridge_variant(config, run_dir, product)
    server = Server(config, run_dir, args.parallel, args.port, fit_slots=args.fit_slots)
    # Fewer slots than asked: stop taking jobs after a while so the queue
    # starts the run again with what the GPU holds by then.
    refit_at = time.monotonic() + REFIT_MIN * 60 if server.parallel < args.parallel else None
    handed_back = threading.Event()
    # Scenarios measuring a student talk to a simulated user on a model of
    # its own (config "simulator", another config's name); without it the
    # teacher being recorded also plays the user, on its own server.
    simulator = None
    if config.get("simulator"):
        (run_dir / "simulator").mkdir(exist_ok=True)
        simulator = Server(configs[config["simulator"]], run_dir / "simulator", server.parallel,
                           args.port + 1)
    # The main thread sits in the worker pool, so exiting there would wait
    # for every job: the handler closes the servers and exits at once.
    close_on_term(server, simulator)
    lock = threading.Lock()
    try:
        print(f"server ready, {server.parallel} slots, {server.speed()} tok/s single stream", flush=True)

        def work(job: tuple) -> None:
            if refit_at is not None and time.monotonic() > refit_at:
                handed_back.set()
                return
            digest = task_digest(job[0])
            row = run_job(job[0], job[1], args.config, config, run_dir, server.sock_dir,
                          args.keep, bridge, product,
                          record_port=server.port if args.record else None,
                          simulator_sock=simulator.sock_dir if simulator is not None else None,
                          takeover=job[2] if len(job) > 2 else None, sampling=sampling)
            row["revision"], row["task_digest"], row["sampling"] = revision, digest, sampling
            # A model server that died mid-run (2026-10-07: another tenant took
            # the GPU) fails every later job in seconds; those are not the
            # model's results. Stop without recording: the next start resumes.
            if STOPPING.is_set():
                raise SystemExit(f"stopping; {row['task']} r{row['rep']} not recorded")
            for name, owner in (("model", server), ("simulator", simulator)):
                if owner is not None and owner.process.poll() is not None:
                    raise SystemExit(f"the {name} server exited ({owner.process.returncode}) during the run; "
                                     f"{row['task']} r{row['rep']} not recorded, rerun to resume")
            with lock, results.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{row['task']} r{row['rep']}: {'PASS' if row['passed'] else 'fail'} "
                  f"{row.get('outcome')} {row['wall']}s {row['grade'].get('problems', [])}",
                  flush=True)

        with ThreadPoolExecutor(server.parallel) as pool:
            list(pool.map(work, jobs))
        if handed_back.is_set():
            print(f"ran {REFIT_MIN} minutes on {server.parallel} of {args.parallel} slots; "
                  "start again to resume with what fits then", flush=True)
            raise SystemExit(VRAM_EXIT)
    finally:
        server.close()
        if simulator is not None:
            simulator.close()


if __name__ == "__main__":
    main()
