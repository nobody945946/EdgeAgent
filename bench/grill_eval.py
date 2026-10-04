"""Does a small model ask good questions in the 求知儀式 (grill-me) interview?

Each scenario (bench/grill/scenarios.jsonl, eval only) is an idea the user
types plus the user's hidden intent: the decisions that matter. A model
interviews a simulated user over a few rounds; a blind judge then labels every
question (a key decision, useful, already answered, filler, a fact it should
have looked up, ...). The user simulator and the judge are separate steps
whose JSON files this script reads; nothing here trains anything.

The model sees exactly what the product sends: the request Pi builds in
interview mode (system prompt, tools, the skill block) is captured once from
the real bridge with a fake model server that never calls a tool. Tool calls
during the eval run against an in-memory fake workspace, never the disk.

    python bench/grill_eval.py capture --out RUN
    python bench/grill_eval.py ask RUN --model PATH.gguf --name e4b
    (answers-r1.json from the user simulator)  python bench/grill_eval.py ask ... again
    python bench/grill_eval.py report RUN
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "bench/grill/scenarios.jsonl"
SERVER = Path("/home/clawd/SilentTalk/.runtime/tools/llama.cpp-b11193-src/build-cuda/bin/llama-server")
PLACEHOLDER = "IDEA_PLACEHOLDER_7f3a"
CWD_PLACEHOLDER = "WORKSPACE_PLACEHOLDER_7f3a"
# Where the product is installed on the laptop: outside every workspace.
SKILL_HOME = "C:\\Users\\me\\EdgeAgent"
PORT = 18312
MAX_HOPS = 20


def scenarios() -> list[dict]:
    rows = [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if "id" in row]


# ---------------------------------------------------------------- capture


def capture(out: Path) -> None:
    """Pi's first request in interview mode, from the real bridge."""
    sys.path.insert(0, str(ROOT))
    from edge_agent.chat import NativeChat

    seen: list[dict] = []

    class Model(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, stop in (({"role": "assistant", "content": "ok"}, None), ({}, "stop")):
                chunk = {"id": "x", "model": "local", "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": stop}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chat = NativeChat(model_port=server.server_port)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for _ in chat.stream_reply("capture", PLACEHOLDER, agent=True,
                                       working_dir=Path(tmp), interview=True):
                pass
            text = json.dumps(seen[0], ensure_ascii=False)
            text = text.replace(str(Path(tmp).resolve()), CWD_PLACEHOLDER)
            text = text.replace(str(ROOT), SKILL_HOME.replace("\\", "\\\\"))
    finally:
        chat.close()
        server.shutdown()
    out.mkdir(parents=True, exist_ok=True)
    (out / "pi-request.json").write_text(text, encoding="utf-8")
    request = json.loads(text)
    print("tools:", [t["function"]["name"] for t in request.get("tools", [])])
    print("sampling:", {k: v for k, v in request.items() if k not in ("messages", "tools")})


# ---------------------------------------------------------------- fake workspace


OUTSIDE = "File access must stay inside the opened workspace."  # the bridge's own words


def web(name: str, args: dict) -> str:
    """The product's own web tools (edge_agent/web_tools.mjs): public pages only."""
    call = ("webSearch(a.query, a.max_results ?? 5)" if name == "web_search"
            else "webFetch(a.url, a.max_chars ?? 12000)")
    script = (f"const m = await import({json.dumps((ROOT / 'edge_agent/web_tools.mjs').as_uri())});"
              f"const a = JSON.parse(process.argv[1]);"
              f"try {{ process.stdout.write(await m.{call}); }} catch (e) {{ process.stdout.write('Error: ' + e.message); }}")
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(args)],
                            capture_output=True, text=True, timeout=60, check=False)
    return result.stdout or f"Error: {result.stderr.strip()[:200]}"


class Workspace:
    """The scenario's files, relative to the folder the user opened."""

    def __init__(self, root: str, files: dict[str, str] | None) -> None:
        self.root = root.replace("\\", "/").rstrip("/")
        self.files = {k.replace("\\", "/"): v for k, v in (files or {}).items()}

    def _rel(self, path: str | None) -> str | None:
        path = str(path or ".").replace("\\", "/")
        if path.lower().startswith(self.root.lower()):
            path = path[len(self.root):]
        elif re.match(r"^([A-Za-z]:|/|~)", path) or path.startswith(".."):
            return None
        path = path.strip("/")
        return "" if path in ("", ".") else path.removeprefix("./")

    def _under(self, base: str) -> list[str]:
        return sorted(p for p in self.files if not base or p == base or p.startswith(base + "/"))

    def ls(self, args: dict) -> str:
        base = self._rel(args.get("path"))
        names = set()
        for path in self._under(base):
            rest = path[len(base):].lstrip("/") if base else path
            if rest:
                head = rest.split("/")[0]
                names.add(head + ("/" if "/" in rest.rstrip("/") or path.endswith("/") else ""))
        return "\n".join(sorted(names)) or "(empty directory)"

    def find(self, args: dict) -> str:
        base, pattern = self._rel(args.get("path")), str(args.get("pattern", "*"))
        hits = [p for p in self._under(base) if fnmatch.fnmatch(p.split("/")[-1] or p, pattern) or fnmatch.fnmatch(p, pattern)]
        return "\n".join(hits) or "No files found"

    def grep(self, args: dict) -> str:
        base = self._rel(args.get("path"))
        try:
            regex = re.compile(str(args.get("pattern", "")), re.IGNORECASE if args.get("ignoreCase") else 0)
        except re.error as exc:
            return f"Invalid pattern: {exc}"
        lines = [f"{p}:{n}: {line}" for p in self._under(base) if not self.files[p].startswith("(")
                 for n, line in enumerate(self.files[p].splitlines(), 1) if regex.search(line)]
        return "\n".join(lines[:100]) or "No matches found"

    def read(self, args: dict) -> str:
        path = self._rel(args.get("path"))
        if path in self.files:
            content = self.files[path]
            if content.startswith("("):  # a binary or private file, described
                return f"Error: cannot display {args.get('path')}: not a text file {content}"
            return content
        if any(p.startswith(path + "/") for p in self.files):
            return f"Error: {args.get('path')} is a directory"
        return f"Error: file not found: {args.get('path')}"

    def call(self, name: str, args: dict) -> str:
        if name in ("ls", "find", "grep", "read"):
            if self._rel(args.get("path")) is None:
                return OUTSIDE
            return getattr(self, name)(args)
        if name in ("web_search", "web_fetch"):
            return web(name, args)
        if name == "search_history":
            return "No matching text in this RAM session."
        return f"Error: tool {name} is not available in this mode"


# ---------------------------------------------------------------- interview


def start_server(model: Path, ctx: int, ngl: int) -> subprocess.Popen:
    log = open(f"/tmp/grill-eval-{PORT}.log", "w")  # noqa: SIM115 - the server's own stdout
    process = subprocess.Popen(
        [str(SERVER), "-m", str(model), "--host", "127.0.0.1", "--port", str(PORT), "-ngl", str(ngl),
         "--fit", "off", "-c", str(ctx), "--parallel", "1", "-fa", "on", "--jinja",
         "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"],
        stdout=log, stderr=subprocess.STDOUT)
    for _ in range(240):
        try:
            if json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).read()).get("status") == "ok":
                return process
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            raise SystemExit(f"llama-server exited; see /tmp/grill-eval-{PORT}.log")
        time.sleep(1)
    process.kill()
    raise SystemExit("llama-server did not become ready")


def complete(request: dict, messages: list[dict]) -> dict:
    body = {k: v for k, v in request.items() if k not in ("messages", "stream", "stream_options", "model")}
    body |= {"messages": messages, "stream": False}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(body, ensure_ascii=False).encode())
    return json.loads(urllib.request.urlopen(req, timeout=1800).read())["choices"][0]["message"]


def one_round(request: dict, messages: list[dict], workspace: Workspace) -> tuple[str, list[dict], float]:
    """Run the model until it answers in text; tool calls hit the fake workspace.
    Like the bridge, the same call a third time stops the turn."""
    calls, started, repeats = [], time.monotonic(), {}
    for _ in range(MAX_HOPS):
        reply = complete(request, messages)
        tool_calls = reply.get("tool_calls") or []
        message = {"role": "assistant", "content": reply.get("content") or ""}
        if tool_calls:
            message["tool_calls"] = tool_calls
        messages.append(message)
        if not tool_calls:
            return message["content"], calls, time.monotonic() - started
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"tool": name, "args": args})
            signature = json.dumps([name, args], sort_keys=True)
            repeats[signature] = repeats.get(signature, 0) + 1
            if repeats[signature] >= 3:
                return "", calls, time.monotonic() - started
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": workspace.call(name, args)})
    return "", calls, time.monotonic() - started


def with_skill(request: dict, skill: Path) -> dict:
    """The captured request with another candidate skill text in the opening."""
    body = skill.read_text(encoding="utf-8").split("---", 2)[2].strip()
    text = json.dumps(request, ensure_ascii=False)
    start = text.index("<skill name=\\\"grill-me\\\">\\n") + len("<skill name=\\\"grill-me\\\">\\n")
    end = text.index("\\n</skill>")
    return json.loads(text[:start] + json.dumps(body, ensure_ascii=False)[1:-1] + text[end:])


def ask(run: Path, model: Path, name: str, ctx: int, ngl: int, skill: Path | None) -> None:
    request = json.loads((run / "pi-request.json").read_text(encoding="utf-8"))
    if skill is not None:
        request = with_skill(request, skill)
    out = run / name
    out.mkdir(parents=True, exist_ok=True)
    server = start_server(model, ctx, ngl)
    try:
        for scenario in scenarios():
            path = out / f"{scenario['id']}.json"
            state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if state is None:
                text = json.dumps(request["messages"], ensure_ascii=False)
                text = text.replace(PLACEHOLDER, scenario["idea"])
                text = text.replace(CWD_PLACEHOLDER, scenario["workspace"].replace("\\", "\\\\"))
                messages = json.loads(text)
                state = {"scenario": scenario["id"], "model": name, "messages": messages, "rounds": []}
            else:
                done = len(state["rounds"])
                answers_file = run / f"answers-r{done}.json"
                answer = json.loads(answers_file.read_text(encoding="utf-8")).get(f"{name}/{scenario['id']}") if answers_file.is_file() else None
                if answer is None:
                    print(f"{name}/{scenario['id']}: no answer for round {done} yet", flush=True)
                    continue
                state["messages"].append({"role": "user", "content": answer})
                state["rounds"][-1]["user_answer"] = answer
            workspace = Workspace(scenario["workspace"], scenario.get("files"))
            text, calls, seconds = one_round(request, state["messages"], workspace)
            state["rounds"].append({"text": text, "tool_calls": calls, "seconds": round(seconds, 1)})
            path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{name}/{scenario['id']}: round {len(state['rounds'])} {len(text)} chars, "
                  f"{len(calls)} tool calls, {seconds:.0f} s", flush=True)
    finally:
        server.terminate()
        server.wait(timeout=60)


# ---------------------------------------------------------------- blind hand-offs
# The user simulator and the judge see transcripts under random keys, never
# which model or skill text wrote them; keys.json maps them back.


def transcripts(run: Path, configs: list[str]) -> dict[str, dict]:
    found = {}
    for config in configs:
        for path in sorted((run / config).glob("*.json")):
            found[f"{config}/{path.stem}"] = json.loads(path.read_text(encoding="utf-8"))
    return found


def blind_keys(run: Path, names: list[str]) -> dict[str, str]:
    import random

    path = run / "keys.json"
    keys = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    used = set(keys.values())
    rng = random.Random(len(keys) + 7)
    for name in names:
        if name not in keys:
            while (key := f"t{rng.randrange(1000, 9999)}") in used:
                pass
            keys[name] = key
            used.add(key)
    path.write_text(json.dumps(keys, indent=1), encoding="utf-8")
    return keys


def shown_rounds(state: dict) -> list[dict]:
    return [{"interviewer": r["text"], "looked_at": [f"{c['tool']} {json.dumps(c['args'], ensure_ascii=False)}" for c in r["tool_calls"]],
             **({"user": r["user_answer"]} if "user_answer" in r else {})} for r in state["rounds"]]


def sim_input(run: Path, configs: list[str], round_no: int) -> None:
    """For the user simulator: the hidden intent and the rounds so far."""
    by_id = {s["id"]: s for s in scenarios()}
    found = {k: v for k, v in transcripts(run, configs).items() if len(v["rounds"]) == round_no}
    keys = blind_keys(run, list(found))
    items = {keys[name]: {"idea": by_id[state["scenario"]]["idea"],
                          "hidden_intent": by_id[state["scenario"]]["decisions"],
                          "rounds": shown_rounds(state)} for name, state in found.items()}
    (run / f"sim-input-r{round_no}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} transcripts -> sim-input-r{round_no}.json")


def sim_apply(run: Path, round_no: int) -> None:
    """The simulator's answers (by blind key) -> answers-rN.json for `ask`."""
    keys = {v: k for k, v in json.loads((run / "keys.json").read_text(encoding="utf-8")).items()}
    blind = json.loads((run / f"sim-answers-r{round_no}.json").read_text(encoding="utf-8"))
    path = run / f"answers-r{round_no}.json"
    answers = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    answers |= {keys[key]: answer for key, answer in blind.items()}
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(blind)} answers -> {path.name}")


def judge_input(run: Path, configs: list[str], out: str) -> None:
    by_id = {s["id"]: s for s in scenarios()}
    found = transcripts(run, configs)
    keys = blind_keys(run, list(found))
    items = {}
    for name, state in found.items():
        scenario = by_id[state["scenario"]]
        items[keys[name]] = {"idea": scenario["idea"], "already_stated": scenario["stated"],
                             "hidden_intent": scenario["decisions"],
                             "workspace_files": scenario.get("files") or {},
                             "rounds": shown_rounds(state)}
    keys_order = sorted(items)
    (run / out).write_text(json.dumps({k: items[k] for k in keys_order}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} transcripts -> {out}")


def ref_apply(run: Path, round_no: int, name: str = "ref-s1") -> None:
    """The reference interviewer's round N (ref-rN.json: {scenario: text})
    into the same state files the models write. It was shown the files."""
    rounds = json.loads((run / f"ref-r{round_no}.json").read_text(encoding="utf-8"))
    answers_path = run / f"answers-r{round_no - 1}.json"
    answers = json.loads(answers_path.read_text(encoding="utf-8")) if answers_path.is_file() else {}
    out = run / name
    out.mkdir(exist_ok=True)
    for scenario in scenarios():
        path = out / f"{scenario['id']}.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "scenario": scenario["id"], "model": name, "messages": [], "rounds": []}
        if len(state["rounds"]) != round_no - 1:
            continue
        if round_no > 1:
            state["rounds"][-1]["user_answer"] = answers[f"{name}/{scenario['id']}"]
        looked = [{"tool": "read", "args": {"path": "(all files)"}}] if scenario.get("files") and round_no == 1 else []
        state["rounds"].append({"text": rounds[scenario["id"]], "tool_calls": looked, "seconds": 0})
        path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: round {round_no} applied")


def ref_input(run: Path, round_no: int, name: str = "ref-s1") -> None:
    """For the reference interviewer's next round: its rounds and the answers."""
    by_id = {s["id"]: s for s in scenarios()}
    items = {}
    for path in sorted((run / name).glob("*.json")):
        state = json.loads(path.read_text(encoding="utf-8"))
        scenario = by_id[state["scenario"]]
        answers = json.loads((run / f"answers-r{round_no - 1}.json").read_text(encoding="utf-8"))
        rounds = [{"interviewer": r["text"], "user": r.get("user_answer") or answers[f"{name}/{state['scenario']}"]}
                  for r in state["rounds"]]
        items[state["scenario"]] = {"idea": scenario["idea"], "workspace": scenario["workspace"],
                                    "files": scenario.get("files"), "rounds": rounds}
    (run / f"ref-input-r{round_no}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} scenarios -> ref-input-r{round_no}.json")


# ---------------------------------------------------------------- report

WASTE = ("already_answered", "filler", "should_look_up", "premature")


def report(run: Path, judged_file: str) -> None:
    """Per config, over its transcripts: how many of the user's real
    decisions the questions reached, and how many questions were waste."""
    keys = {v: k for k, v in json.loads((run / "keys.json").read_text(encoding="utf-8")).items()}
    judged = json.loads((run / judged_file).read_text(encoding="utf-8"))
    by_model: dict[str, list[dict]] = {}
    decisions = {s["id"]: {d["id"] for d in s["decisions"]} for s in scenarios()}
    for key, verdict in judged.items():
        model, scenario = keys[key].split("/")
        model = re.sub(r"-s\d+$", "", model)
        by_model.setdefault(model, []).append(verdict | {"scenario": scenario})
    print("config | 逐字稿 | 題/稿 | 命中真正決定 | 第1輪命中 | 廢話率 | 有用率 | 好建議 | 替使用者決定/稿 | 停住輪 | 全繁中")
    for model, items in sorted(by_model.items()):
        questions = [q for item in items for q in item["questions"]]
        labels: dict[str, int] = {}
        for q in questions:
            labels[q["label"]] = labels.get(q["label"], 0) + 1

        def coverage(rounds: int, items: list[dict] = items) -> float:
            shares = [len({h for q in item["questions"] if q.get("round", 1) <= rounds for h in q.get("hits", [])}
                          & decisions[item["scenario"]]) / len(decisions[item["scenario"]]) for item in items]
            return sum(shares) / len(shares)

        n = max(1, len(questions))
        recs = [q for q in questions if q.get("recommendation") in ("good", "weak", "missing")]
        print(f"{model:9} | {len(items):2} | {len(questions) / len(items):4.1f} | {coverage(99):4.0%} | "
              f"{coverage(1):4.0%} | {sum(labels.get(w, 0) for w in WASTE) / n:4.0%} | "
              f"{(labels.get('key', 0) + labels.get('useful', 0)) / n:4.0%} | "
              f"{sum(q['recommendation'] == 'good' for q in recs) / max(1, len(recs)):4.0%} | "
              f"{sum(item.get('self_decided', 0) for item in items) / len(items):3.1f} | "
              f"{sum(item.get('stopped_rounds', 0) for item in items):2} | "
              f"{sum(bool(item.get('language_ok')) for item in items) / len(items):4.0%} | {labels}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("capture")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("ask")
    p.add_argument("run", type=Path)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--ctx", type=int, default=16384)
    p.add_argument("--ngl", type=int, default=99)
    p.add_argument("--skill", type=Path, help="a candidate skill text instead of the product's")
    p = sub.add_parser("report")
    p.add_argument("run", type=Path)
    p.add_argument("--judged", default="judge.json")
    for name in ("sim-input", "judge-input"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
        p.add_argument("--configs", nargs="+", required=True)
        p.add_argument("--round", type=int, default=1)
        p.add_argument("--out", default="judge-input.json")
    for name in ("sim-apply", "ref-apply", "ref-input"):
        p = sub.add_parser(name)
        p.add_argument("run", type=Path)
        p.add_argument("--round", type=int, required=True)
    args = parser.parse_args()
    if args.cmd == "capture":
        capture(args.out)
    elif args.cmd == "ask":
        ask(args.run, args.model, args.name, args.ctx, args.ngl, args.skill)
    elif args.cmd == "sim-input":
        sim_input(args.run, args.configs, args.round)
    elif args.cmd == "sim-apply":
        sim_apply(args.run, args.round)
    elif args.cmd == "ref-apply":
        ref_apply(args.run, args.round)
    elif args.cmd == "ref-input":
        ref_input(args.run, args.round)
    elif args.cmd == "judge-input":
        judge_input(args.run, args.configs, args.out)
    else:
        report(args.run, args.judged)


if __name__ == "__main__":
    main()
