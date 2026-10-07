"""Opt-in real Windows integration; task text is supplied in RAM via environment."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from edge_agent.permissions import validate_permissions
from edge_agent.runtime import (
    CORE_ROOT,
    DEFAULT_MODEL,
    NODE_VERSION,
    QWEN_MODEL,
)
from edge_agent.service import CONTROL_PORT, EdgeAgent, ExclusiveHTTPServer, Handler


def vram() -> int:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    )
    return int(result.stdout.strip())


def launch_edge(profile: Path) -> subprocess.Popen:
    """A hidden Edge with its own throwaway profile and CDP on 9223; the caller
    closes it by this PID and the profile goes with the temporary directory."""
    from edge_agent.service import _listening_pid

    if _listening_pid(9223) is not None:
        raise RuntimeError("CDP port 9223 is occupied")
    candidates = [
        Path(os.environ.get(name, "")) / "Microsoft/Edge/Application/msedge.exe"
        for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
        if os.environ.get(name)
    ]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise RuntimeError("Microsoft Edge (msedge.exe) not found")
    edge = subprocess.Popen(
        [
            str(executable),
            "--headless=new",
            "--remote-debugging-port=9223",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9223/json/version", timeout=1
            ):
                return edge
        except OSError:
            if edge.poll() is not None:
                break
            time.sleep(0.2)
    subprocess.run(
        ["taskkill", "/PID", str(edge.pid), "/T", "/F"],
        capture_output=True,
        check=False,
        timeout=15,
    )
    raise RuntimeError("dedicated Edge CDP did not become ready")


def native_probe(core: EdgeAgent, workspace: Path, key: str) -> dict[str, object]:
    text = os.environ[key]
    interview = key == "EDGE_AGENT_INTERVIEW_PROMPT"
    tools, errors = [], 0
    started = time.monotonic()
    session_id = uuid.uuid4().hex
    timeout = 300 if interview else 180
    timer = threading.Timer(timeout, core.chat.cancel, args=(session_id,))
    timer.start()
    terminal = ""
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{CONTROL_PORT}/chat",
            data=json.dumps(
                {
                    "session_id": session_id,
                    "text": text,
                    "agent": True,
                    "client": "ui" if interview else "discord",
                    "mode": "interview" if interview else "work",
                    "working_dir": str(workspace),
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout + 10) as response:
            events = []
            event = ""
            for raw_line in response:
                line = raw_line.decode().strip()
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    events.append((event, json.loads(line[6:])))
                if not line and event in {"done", "error", "cancelled"}:
                    break
        answer = ""
        forbidden_calls = 0
        for event, data in events:
            if event == "delta":
                answer += str(data.get("text", ""))
            if event == "tool" and data["phase"] == "running":
                forbidden_calls += data["tool"] in {
                    "edit",
                    "write",
                    "powershell",
                    "bash",
                }
            if event == "tool" and data["phase"] == "finished":
                result = json.dumps(data["result"])
                tools.append(
                    {
                        "tool": data["tool"],
                        "characters": len(result),
                        "http_error": any(
                            code in result
                            for code in (
                                "HTTP 403",
                                "HTTP 429",
                                "HTTP 503",
                                "timed out",
                            )
                        ),
                    }
                )
            if event == "error":
                errors += 1
            if event in {"error", "done", "cancelled"}:
                terminal = event
        # Any one of these sets of tools counts (web: the research tool, or search and read).
        expected = {
            "EDGE_AGENT_WEB_PROMPT": ({"web_research"}, {"web_search", "web_fetch"}),
            "EDGE_AGENT_SEARCH_PROMPT": ({"grep", "find"},),
            "EDGE_AGENT_READ_PROMPT": ({"read"},),
            "EDGE_AGENT_INTERVIEW_PROMPT": (set(),),
        }[key]
        if interview:
            from bench.grill_auto import round_counts

            counts = round_counts(answer)
            result = {
                "event": "interview_probe",
                "model": core.active_model,
                "terminal": terminal,
                "errors": errors,
                **counts,
                "write_or_command_calls": forbidden_calls,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
            # Even an incomplete or empty model answer leaves numeric evidence.
            print(json.dumps(result), flush=True)
            if (
                terminal != "done"
                or errors
                or forbidden_calls
                or not counts["questions"]
            ):
                raise AssertionError(
                    "interview did not finish with questions and zero writes/commands"
                )
            return result
        if (
            terminal != "done"
            or errors
            or not any(
                option.issubset({item["tool"] for item in tools}) for option in expected
            )
            or any(item["http_error"] for item in tools)
        ):
            raise AssertionError(f"{key}: task or required tools failed")
        return {
            "event": key,
            "terminal": terminal,
            "tools": tools,
            "errors": errors,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    finally:
        timer.cancel()
        if interview:
            core.chat.reset(session_id)


def concurrent_control_probe(core: EdgeAgent) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while not core.chat_in_progress and time.monotonic() < deadline:
        time.sleep(0.01)
    if not core.chat_in_progress:
        raise RuntimeError("Agent did not enter the HTTP stream")
    started = time.monotonic()
    request = urllib.request.Request(
        f"http://127.0.0.1:{CONTROL_PORT}/base",
        data=json.dumps({"owner_pid": os.getpid()}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=3).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        return {
            "status": exc.code,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    raise AssertionError("switch must reject promptly during an Agent stream")


def role_probe(core: EdgeAgent) -> dict[str, object]:
    started = time.monotonic()
    result = core._model_request(
        "/v1/chat/completions",
        {
            "model": "local",
            "messages": [
                {"role": "user", "content": os.environ["EDGE_AGENT_ROLE_PROMPT"]}
            ],
            "id_slot": 0,
            "max_tokens": 32,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=180,
    )
    return {
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "output_tokens": result.get("usage", {}).get("completion_tokens"),
        "timings": result.get("timings", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--lease-sharing", action="store_true")
    parser.add_argument("--interview-models", action="store_true")
    parser.add_argument("--inference-port", type=int, default=18089)
    args = parser.parse_args()
    from edge_agent import service

    service.GENERAL_PORT = args.inference_port
    for port in (18087, args.inference_port):
        from edge_agent.service import _listening_pid

        if _listening_pid(port) is not None:
            raise RuntimeError(f"integration port {port} is occupied")
    temporary = tempfile.TemporaryDirectory(prefix="edge-integration-")
    with temporary as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "probe.py").write_text("fixture_marker = 1\n")
        core = EdgeAgent(root)
        core.model_dir = CORE_ROOT / "models"
        core.chat.bridge.core_dir = CORE_ROOT
        core.config = {
            **core.config,
            "model": QWEN_MODEL,
            "context_size": 16384,
            "max_output_tokens": 512,
            "working_context_size": 8192,
            "permissions": validate_permissions(
                {
                    "discord": {
                        "enabled": True,
                        "edit": "deny",
                        "shell": "deny",
                        "web": "allow",
                        "workspaces": [str(workspace)],
                    }
                }
            ),
        }
        Handler.core = core
        server = ExclusiveHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        edge = None
        try:
            core.switch("base")
            if args.browser:
                edge = launch_edge(root / "integration-edge-profile")
                subprocess.run(
                    [
                        str(
                            CORE_ROOT
                            / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
                        ),
                        "tests/browser_check.mjs",
                    ],
                    cwd=CORE_ROOT,
                    check=True,
                    timeout=240,
                )
            if args.interview_models:
                original = dict(core.config)
                os.environ["EDGE_AGENT_INTERVIEW_PROMPT"] = "做一個記帳小工具"
                failures = 0
                try:
                    for model in (DEFAULT_MODEL, QWEN_MODEL):
                        try:
                            core.select_model(
                                {
                                    "model": model,
                                    "lora": "",
                                    "mmproj": "",
                                    "max_output_tokens": 8192,
                                }
                            )
                            native_probe(core, workspace, "EDGE_AGENT_INTERVIEW_PROMPT")
                        except (AssertionError, RuntimeError, OSError):
                            failures += 1
                            print(
                                json.dumps(
                                    {"event": "interview_failed", "model": model}
                                ),
                                flush=True,
                            )
                        finally:
                            core.chat.close()
                finally:
                    core.select_model(original)
                    print(
                        json.dumps(
                            {
                                "event": "interview_config_restored",
                                "equal": core.config == original,
                                "model": core.active_model,
                            }
                        ),
                        flush=True,
                    )
                if failures:
                    raise AssertionError(f"{failures} interview model checks failed")
            if args.lease_sharing:
                original = dict(core.config)
                saved = (
                    core.config_path.read_bytes() if core.config_path.exists() else None
                )
                prompts = {
                    "EDGE_AGENT_ROLE_PROMPT": "Reply with only OK.",
                    "EDGE_AGENT_READ_PROMPT": "Read probe.py and report the value of fixture_marker.",
                }
                previous = {key: os.environ.get(key) for key in prompts}
                os.environ.update(prompts)
                try:
                    status = core.switch(
                        "character",
                        os.getpid(),
                        {
                            "model": DEFAULT_MODEL,
                            "lora": "",
                            "context_size": 8192,
                            "gpu_layers": 99,
                            "low_vram": True,
                            "port": 18088,
                        },
                    )
                    assert DEFAULT_MODEL != original["model"]
                    assert status["ready"] and status["agent_slot"] == 1
                    assert status["model"] == DEFAULT_MODEL and status["lora"] == ""
                    assert core.config == original
                    if args.browser:
                        subprocess.run(
                            [
                                str(
                                    CORE_ROOT
                                    / f".runtime/node/node-v{NODE_VERSION}-win-x64/node.exe"
                                ),
                                "tests/browser_check.mjs",
                                "--lease-status",
                            ],
                            cwd=CORE_ROOT,
                            check=True,
                            timeout=60,
                        )
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        agent = pool.submit(
                            native_probe, core, workspace, "EDGE_AGENT_READ_PROMPT"
                        )
                        role = pool.submit(role_probe, core)
                        result = {
                            "event": "automatic_lease_sharing",
                            "leased_model": core.active_model,
                            "agent_slot": core.agent_slot,
                            "agent": agent.result(),
                            "role": role.result(),
                        }
                finally:
                    core.chat.close()
                    restored = core.switch("base", os.getpid())
                    for key, value in previous.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
                assert restored["ready"] and restored["model"] == original["model"]
                assert core.config == original
                assert (
                    core.config_path.read_bytes() if core.config_path.exists() else None
                ) == saved
                result.update(
                    {"restored_model": restored["model"], "preferences_unchanged": True}
                )
                print(json.dumps(result), flush=True)
            for key in ("EDGE_AGENT_WEB_PROMPT", "EDGE_AGENT_SEARCH_PROMPT"):
                if key in os.environ:
                    print(json.dumps(native_probe(core, workspace, key)), flush=True)
            if "EDGE_AGENT_ROLE_PROMPT" in os.environ:
                for context in (8192, 16384, 32768):
                    core.config = {**core.config, "context_size": context}
                    core.switch(
                        "character",
                        os.getpid(),
                        {
                            "model": QWEN_MODEL,
                            "lora": "",
                            "context_size": context,
                            "gpu_layers": 99,
                            "low_vram": True,
                            "port": 18088,
                        },
                    )
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        agent = pool.submit(
                            native_probe, core, workspace, "EDGE_AGENT_READ_PROMPT"
                        )
                        control = concurrent_control_probe(core)
                        role = pool.submit(role_probe, core)
                        print(
                            json.dumps(
                                {
                                    "event": "shared_slots",
                                    "context": context,
                                    "vram_mib": vram(),
                                    "control": control,
                                    "agent": agent.result(),
                                    "role": role.result(),
                                }
                            ),
                            flush=True,
                        )
                core.chat.close()
                core.switch("base", os.getpid())
                print(
                    json.dumps(
                        {"event": "lease_returned", "ready": core.status()["ready"]}
                    ),
                    flush=True,
                )
        finally:
            core.chat.close()
            core.terminal.close_all()
            core._stop_child()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            if edge is not None:
                subprocess.run(
                    ["taskkill", "/PID", str(edge.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                edge.wait(timeout=15)
            # Windows may retain a departing child's cwd briefly after wait().
            for attempt in range(30):
                try:
                    temporary.cleanup()
                    break
                except PermissionError:
                    if attempt == 29:
                        raise
                    time.sleep(0.1)
    print(
        json.dumps(
            {
                "event": "integration_cleanup",
                "listeners": [
                    port
                    for port in (18087, args.inference_port, 9223)
                    if _listening_pid(port) is not None
                ],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
