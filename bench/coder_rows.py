"""Training rows for the host-driven coder (edge_agent.coder) from the
training tasks' own answers, with no model in the loop.

Every training task (bench/make_tasks.py) is a repository function that was
broken or emptied on purpose, and the repository's original function is the
answer. For each task, inside the training sandbox, the coder's own code
reproduces the failure and writes its two requests exactly as the product
sends them: which functions to change (answer: the broken one's number) and
the new code (answer: the original function). A task becomes rows only if
the original function, spliced in by the coder, makes the failing tests pass
(hard gate); a choice row only if the broken function was among the
candidates. Eval tasks (bench/tasks, held-out task folders) are refused.

    python bench/coder_rows.py --task-root .runtime/agentdata/tasks --out .runtime/agentdata/coder-rows.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BENCH))
import run  # noqa: E402

from edge_agent import coder  # noqa: E402

TRAINING_ROOT = (ROOT / ".runtime/agentdata/tasks").resolve()


def original_function(task_dir: Path, task: dict) -> tuple[str, str] | None:
    """The broken function's qualified name and the repository's own version
    of it: the definition named in the spec whose text the task changed (a
    module may hold several methods of that name)."""
    spec = task["spec"]
    fixed = {d.name: d for d in coder.definitions(task_dir / "solution", [spec["file"]])}
    broken = {d.name: d for d in coder.definitions(task_dir / "workspace", [spec["file"]])}
    changed = [name for name, d in fixed.items()
               if d.short == spec["function"] and name in broken and broken[name].source != d.source]
    if len(changed) != 1:
        return None
    return changed[0], textwrap.dedent(fixed[changed[0]].source)


# Sandbox side: reproduce with the coder, write both requests, check the answer.
INSIDE = r'''
import json, os, sys
from pathlib import Path
from edge_agent import coder
job = json.loads(os.environ["BENCH_ROWS"])
root = Path("/work")
setup = coder.reproduce(root, job["prompt"])
out = {"goal": setup.goal, "listed": [f"{d.file}:{d.name}" for d, _ in setup.listed]}
target = [d for d in setup.defs if d.file == job["file"] and d.name == job["name"]]
if setup.goal and target:
    chosen = target[:1]
    out["choose"] = coder.choose_prompt(job["prompt"], setup.first.output, setup.tests, setup.listed) if setup.listed else ""
    out["repair"] = coder.repair_prompt(job["prompt"], setup.first.output, setup.tests, chosen,
                                        coder._helpers(setup.defs, chosen))
    texts = coder.splice(root, chosen, job["answer"])
    if texts:
        for file, text in texts.items():
            (root / file).write_text(text, encoding="utf-8")
        check = coder.run_tests(root, sorted({t.split("::")[0] for t in setup.goal} | set(setup.targets)))
        out["answer_passes"] = not check.failed and not check.errors and not check.timed_out
print(json.dumps(out, ensure_ascii=False))
'''


def rows_for(task_dir: Path, work: Path) -> tuple[list[dict], str]:
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    if not task.get("prompt"):
        return [], "no request yet"
    found = original_function(task_dir, task)
    if found is None:
        return [], "no original function"
    qualname, answer = found
    job = task_dir.name
    workspace, original = work / "ws" / job, work / "orig" / job
    run.prepare(task_dir, task, workspace, original)
    payload = {"prompt": task["prompt"], "file": task["spec"]["file"], "name": qualname,
               "answer": answer}
    name = "ecr-" + hashlib.sha1(job.encode()).hexdigest()[:12]
    command = run.sandbox(name, [(workspace, "/work", "rw"), *run.code_mounts()],
                          {"BENCH_ROWS": json.dumps(payload, ensure_ascii=False)},
                          ["python", "-c", INSIDE], image=task.get("image", run.IMAGE))
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=1500)
        out = run.last_json(done.stdout)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        out = {}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(original, ignore_errors=True)
    if not out.get("goal"):
        return [], "not reproduced"
    if "repair" not in out:
        return [], "broken function not found"
    if not out.get("answer_passes"):
        return [], "original function does not pass"
    source = {"job": job, "repo": task.get("repo", ""), "kind": task["spec"]["kind"]}
    rows = [{"messages": coder.messages(out["repair"]) + [
        {"role": "assistant", "content": f"```python\n{answer.rstrip()}\n```"}],
        "train": "last", "source": {**source, "step": "repair"}}]
    numbers = [n for n, item in enumerate(out["listed"], 1) if item == f"{task['spec']['file']}:{qualname}"]
    if out.get("choose") and numbers:
        rows.append({"messages": coder.messages(out["choose"]) + [
            {"role": "assistant", "content": json.dumps({"edit": numbers[:1]})}],
            "train": "last", "source": {**source, "step": "choose"}})
    return rows, "kept" if len(rows) == 2 else "kept (not among the candidates)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, default=TRAINING_ROOT)
    parser.add_argument("--tasks", default="*")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    root = args.task_root.resolve()
    if root != TRAINING_ROOT:
        print(f"REFUSED: rows come only from the training tasks ({TRAINING_ROOT})")
        return 3
    run.TASK_ROOT = root
    tasks = run.load_tasks(args.tasks)
    work = ROOT / ".runtime/bench/coder-rows"
    why: Counter = Counter()
    total: list[dict] = []

    def one(task_dir: Path) -> None:
        rows, reason = rows_for(task_dir, work)
        why[reason] += 1
        total.extend(rows)
        print(f"{task_dir.name}: {reason}", flush=True)

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, tasks))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in sorted(total, key=lambda r: (r["source"]["job"], r["source"]["step"])):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    coder_digest = hashlib.sha256((ROOT / "edge_agent/coder.py").read_bytes()).hexdigest()[:16]
    args.out.with_name(args.out.name + ".version").write_text(json.dumps(
        {"producer": "edgeagent-coder-rows", "version": coder_digest, "tasks": len(tasks)}), encoding="utf-8")
    print(f"{len(total)} rows -> {args.out}; {dict(why)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
