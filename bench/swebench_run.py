"""SWE-bench in the mini-swe-agent harness, graded by the official harness.

For an external, comparable number: each instance (SWE-bench/SWE-bench_Verified
rows, which name their image, eval script and log parser) runs in its own
official image with no network, the model reached
through the bench relay, bench/mini_runner.py as the agent (/testbed is the
repository, as in the images and the recordings). The submitted patch (or,
without a submission, the diff of tracked files) is a prediction;
swebench.harness.run_evaluation decides what is resolved.

    python bench/swebench_run.py --config e4b-mini --tag swe60-e4b \\
        --instances .runtime/swebench/verified-60.jsonl --parallel 2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
import run  # noqa: E402

SWE = BENCH.parent / ".runtime/swebench"
CONDA_PATH = ("/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:"
              "/usr/sbin:/usr/bin:/sbin:/bin")


def run_instance(item: dict, config: dict, run_dir: Path, sock_dir: Path, timeout: int) -> dict:
    instance = item["instance_id"]
    out = run_dir / "out" / instance
    out.mkdir(parents=True, exist_ok=True)
    spec = {"prompt": item["problem_statement"], "timeout_s": timeout, "mini": config.get("mini"),
            "effort": config.get("effort", "off")}
    name = "eab-swe-" + instance.replace("__", "-").replace(".", "-")[:80]
    # The image's own python runs the agent (the testbed env may be old);
    # commands see the testbed env first on PATH, as in the official harness.
    script = ("PY=$(command -v /opt/miniconda3/bin/python3 python3 | head -1); "
              "$PY /opt/bench/mini_runner.py > /out/result.json 2> /out/stderr.txt; "
              "cd /testbed && git -c core.fileMode=false diff > /out/tracked.diff")
    # The image's root (its files are root's); of the capabilities only
    # DAC_OVERRIDE, to write /out and reach the relay's socket, both the host
    # user's. No network.
    command = ["docker", "run", "--rm", "--name", name, "--network", "none", "--cap-drop", "ALL",
               "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "8g",
               "--cpus", "4", "-w", "/testbed",
               "-v", f"{sock_dir}:/sock:ro", "-v", f"{out}:/out:rw",
               "-v", f"{BENCH / 'relay.py'}:/opt/bench/relay.py:ro",
               "-v", f"{BENCH / 'mini_runner.py'}:/opt/bench/mini_runner.py:ro",
               "-v", f"{BENCH / 'mini_templates.json'}:/opt/bench/mini_templates.json:ro",
               "-e", f"BENCH_SPEC={json.dumps(spec, ensure_ascii=False)}", "-e", "MINI_SUBMISSION=/out/submission.txt",
               "-e", f"PATH={CONDA_PATH}", item["image"], "bash", "-c", script]
    started = time.monotonic()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 300)
        (out / "docker.txt").write_text(f"exit {done.returncode}\n{done.stderr[-4000:]}", encoding="utf-8")
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
    result = run.last_json((out / "result.json").read_text(encoding="utf-8", errors="replace")) \
        if (out / "result.json").is_file() else {"outcome": "crash"}
    submission = (out / "submission.txt").read_text(encoding="utf-8", errors="replace") \
        if (out / "submission.txt").is_file() else ""
    tracked = (out / "tracked.diff").read_text(encoding="utf-8", errors="replace") \
        if (out / "tracked.diff").is_file() else ""
    patch = submission if submission.lstrip().startswith("diff") else tracked
    trajectory = result.pop("trajectory", None)
    if trajectory is not None:
        (out / "trajectory.json").write_text(json.dumps(trajectory, ensure_ascii=False), encoding="utf-8")
    return {"instance_id": instance, "outcome": result.get("outcome"), "turns": result.get("turns"),
            "usage": result.get("usage"), "wall": round(time.monotonic() - started, 1),
            "submitted": bool(submission), "patch_lines": patch.count("\n"), "model_patch": patch}


def evaluate(run_dir: Path, rows: list[dict], tag: str, instances: Path) -> dict:
    predictions = run_dir / "predictions.jsonl"
    with predictions.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"instance_id": row["instance_id"], "model_name_or_path": tag,
                                     "model_patch": row["model_patch"]}) + "\n")
    subprocess.run([str(SWE / "venv/bin/python"), "-m", "swebench.harness.run_evaluation",
                    "--dataset_name", str(instances.resolve()), "--predictions_path", str(predictions),
                    "--run_id", tag, "--max_workers", "4",
                    "--instance_ids", *[row["instance_id"] for row in rows]],
                   cwd=run_dir, check=False)
    reports = sorted(run_dir.glob(f"{tag}.{tag}.json")) or sorted(run_dir.glob("*.json"))
    report = json.loads(reports[-1].read_text()) if reports else {}
    return {"resolved": len(report.get("resolved_ids", [])), "submitted": report.get("submitted_instances"),
            "total": len(rows), "resolved_ids": report.get("resolved_ids", [])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--port", type=int, default=18193)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    config = json.loads((BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    run_dir = run.STATE / args.tag
    run_dir.mkdir(parents=True, exist_ok=True)
    results = run_dir / "results.jsonl"
    done = {json.loads(line)["instance_id"] for line in results.read_text().splitlines()} \
        if results.is_file() else set()
    items = [json.loads(line) for line in args.instances.read_text().splitlines()]
    todo = [item for item in items if item["instance_id"] not in done]
    print(f"{len(todo)} instances, {len(done)} already done", flush=True)
    if todo:
        server = run.Server(config, run_dir, args.parallel, args.port)
        lock = threading.Lock()
        try:
            def work(item: dict) -> None:
                row = run_instance(item, config, run_dir, server.sock_dir, args.timeout)
                with lock, results.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{row['instance_id']}: {row['outcome']} submitted={row['submitted']} "
                      f"lines={row['patch_lines']} {row['wall']}s", flush=True)

            with ThreadPoolExecutor(args.parallel) as pool:
                list(pool.map(work, todo))
        finally:
            server.close()
    rows = [json.loads(line) for line in results.read_text().splitlines()]
    summary = evaluate(run_dir, rows, args.tag, args.instances)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"resolved {summary['resolved']}/{summary['total']}", flush=True)


if __name__ == "__main__":
    main()
