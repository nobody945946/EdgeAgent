"""Can a small model reason well enough not to mislead the user?

The user wants to discuss ideas and qualitative data with the Agent:「他必須邏輯好
才不會誤導我」. Three kinds of items (bench/logic/items.jsonl, eval only):

- themes: qualitative data in a file. Find the themes with counts and real
  quotes. Some prompts carry the user's (or a manager's) wrong reading.
- flaws: the user's idea with planted flaws, or a sound plan. Does the model
  flag the key flaws without inventing any, even when it is only asked to
  help carry the idea out?
- pushback: a question with a definite answer, then the user pushes back,
  either wrongly (hold) or with a real new fact (update).

The model sees exactly what the product sends in work mode, captured from the
real bridge with thinking off or on (grill_eval.capture). Tools run against
grill_eval's read-only fake workspace. A blind judge (bench/logic/judge-rubric.md)
labels each transcript; code checks that every quote is really in the data.

    python bench/logic_eval.py capture RUN
    python bench/logic_eval.py ask RUN --model PATH.gguf --name qwen4b-off [--thinking medium]
    python bench/logic_eval.py judge-input RUN --configs qwen4b-off qwen4b-med
    (judge.json from the blind judges)
    python bench/logic_eval.py report RUN
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import random
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grill_eval
from grill_eval import (
    CWD_PLACEHOLDER,
    PLACEHOLDER,
    Workspace,
    blind_keys,
    one_round,
    start_server,
    transcripts,
)

ITEMS = Path(__file__).resolve().parent / "logic" / "items.jsonl"
THINKING = ("off", "medium")
READ_ONLY = "Error: the user asked for analysis only; files in this folder are read-only for this task."


ITEM_FILES = {"main": ITEMS, "holdout": ITEMS.with_name("items-holdout.jsonl"),
              "research": ITEMS.with_name("items-research.jsonl"), "dev": ITEMS.with_name("items-dev.jsonl")}
_REPO_CACHE: dict[tuple, dict[str, str]] = {}


def item_files(item: dict) -> dict[str, str]:
    """The item's workspace: its own files, or text files of a git commit
    (dev items), with one line broken when the item says so."""
    repo = item.get("repo")
    if not repo:
        return item.get("files") or {}
    key = (repo["path"], repo["commit"], tuple(repo["include"]))
    if key not in _REPO_CACHE:
        names = subprocess.run(["git", "-C", repo["path"], "ls-tree", "-r", "--name-only", repo["commit"],
                                *repo["include"]], capture_output=True, text=True, check=True).stdout.split()
        files = {}
        for name in names:
            blob = subprocess.run(["git", "-C", repo["path"], "show", f"{repo['commit']}:{name}"],
                                  capture_output=True, check=True).stdout
            try:
                files[name] = blob.decode("utf-8")
            except UnicodeDecodeError:
                files[name] = "(binary file)"
        _REPO_CACHE[key] = files
    files = dict(_REPO_CACHE[key])
    mutate = repo.get("mutate")
    if mutate:
        files[mutate["file"]] = files[mutate["file"]].replace(mutate["old"], mutate["new"])
    return files


def items(which: str = "main") -> list[dict]:
    path = ITEM_FILES[which]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def all_items() -> list[dict]:
    return [item for which in ITEM_FILES for item in items(which)]


def with_skill(prompt: str, skill: Path | None) -> str:
    """A candidate skill opens the message the way the product opens 求知儀式
    (chat.mode_opening): the skill text, then the user's words."""
    if skill is None:
        return prompt
    body = skill.read_text(encoding="utf-8").split("---", 2)[2].strip()
    return f'<skill name="discussion">\n{body}\n</skill>\n\n{prompt}'


class AnalysisWorkspace(Workspace):
    """The fake workspace, plus PowerShell for working numbers out: it runs in
    the bench sandbox (no network, read-only, the item's files at /work), as
    the user would approve a calculation. Edits stay refused."""

    def call(self, name: str, args: dict) -> str:
        if name in ("edit", "write"):
            return READ_ONLY
        if name != "powershell":
            return super().call(name, args)
        from run import sandbox

        command = str(args.get("command", ""))
        for prefix in (self.root, self.root.replace("/", "\\")):
            command = command.replace(prefix, "/work")
        with tempfile.TemporaryDirectory() as tmp:
            for rel, content in self.files.items():
                path = Path(tmp) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            Path(tmp).chmod(0o755)
            try:
                done = subprocess.run(
                    sandbox(f"logic-{uuid.uuid4().hex[:8]}", [(Path(tmp), "/work", "ro")], {},
                            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command], memory="1g"),
                    capture_output=True, text=True, timeout=90, check=False)
            except subprocess.TimeoutExpired:
                return "Error: command timed out"
        return ((done.stdout + done.stderr).strip() or f"(exit {done.returncode}, no output)")[:4000]


def status(run: Path, name: str, item_set: str = "main") -> int:
    """0 when every item has an answer without error (queues loop until then)."""
    pending = [i["id"] for i in items(item_set) if not (run / name / f"{i['id']}.json").is_file()
               or "error" in json.loads((run / name / f"{i['id']}.json").read_text(encoding="utf-8"))]
    print(f"{name}: {len(items(item_set)) - len(pending)}/{len(items(item_set))} done")
    return 1 if pending else 0


def capture(run: Path) -> None:
    for thinking in THINKING:
        grill_eval.capture(run, mode="work", thinking=thinking, name=f"pi-request-{thinking}.json")


ROUND_BUDGET_S = 900  # one answer, tool calls included
VRAM_EXIT = 3


def wait_for_vram(model: Path, wait_min: float) -> None:
    """Start only when the GPU has room (the model plus ~2 GB for context and
    buffers); other tenants share it. Give up after wait_min (exit 3)."""
    from run import free_vram_mib

    need = model.stat().st_size // 2**20 + 2048
    give_up = time.monotonic() + wait_min * 60
    while free_vram_mib() < need:
        if time.monotonic() > give_up:
            raise SystemExit(VRAM_EXIT)
        print(f"waiting for {need} MiB free VRAM (now {free_vram_mib()})", flush=True)
        time.sleep(30)


def answer(request: dict, item: dict, skill: Path | None = None) -> list[dict]:
    text = json.dumps(request["messages"], ensure_ascii=False)
    text = text.replace(PLACEHOLDER, json.dumps(with_skill(item["prompt"], skill), ensure_ascii=False)[1:-1])
    text = text.replace(CWD_PLACEHOLDER, item["workspace"].replace("\\", "\\\\"))
    messages = json.loads(text)
    workspace = AnalysisWorkspace(item["workspace"], item_files(item))
    rounds = []
    for user_turn in [None, *([item["followup"]] if item.get("followup") else [])]:
        if user_turn is not None:
            messages.append({"role": "user", "content": user_turn})
        reply, calls, seconds = one_round(request, messages, workspace, ROUND_BUDGET_S)
        rounds.append({"text": reply, "tool_calls": calls, "seconds": round(seconds, 1)})
    return rounds


def ask(run: Path, model: Path, name: str, thinking: str, ctx: int, ngl: int, only: str = "*",
        vram_wait_min: float = 180, item_set: str = "main", skill: Path | None = None) -> None:
    """Unattended: every item ends (time budget per round); a dead server is
    restarted and the item retried once; an item that still fails is saved
    with its error and retried on the next ask."""
    request = json.loads((run / f"pi-request-{thinking}.json").read_text(encoding="utf-8"))
    out = run / name
    out.mkdir(parents=True, exist_ok=True)
    todo = []
    for item in items(item_set):
        path = out / f"{item['id']}.json"
        if fnmatch.fnmatch(item["id"], only) and (
                not path.is_file() or "error" in json.loads(path.read_text(encoding="utf-8"))):
            todo.append(item)
    if not todo:
        return
    wait_for_vram(model, vram_wait_min)
    server = start_server(model, ctx, ngl)
    try:
        for item in todo:
            state = {"item": item["id"], "model": name, "thinking": thinking,
                     "skill": skill.name if skill else ""}
            for attempt in (1, 2):
                try:
                    state["rounds"] = answer(request, item, skill)
                    state.pop("error", None)
                    break
                except (OSError, ValueError, KeyError) as exc:  # URLError and timeouts are OSError
                    state["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    print(f"{name}/{item['id']}: attempt {attempt} failed: {state['error']}", flush=True)
                    if server.poll() is not None or attempt == 1:
                        server.terminate()
                        server.wait(timeout=60)
                        server = start_server(model, ctx, ngl)
            (out / f"{item['id']}.json").write_text(json.dumps(state, ensure_ascii=False, indent=1),
                                                    encoding="utf-8")
            rounds = state.get("rounds", [])
            print(f"{name}/{item['id']}: {[len(r['text']) for r in rounds]} chars, "
                  f"{sum(len(r['tool_calls']) for r in rounds)} tool calls, "
                  f"{sum(r['seconds'] for r in rounds):.0f} s{' ERROR' if 'error' in state else ''}", flush=True)
    finally:
        server.terminate()
        server.wait(timeout=60)


# ---------------------------------------------------------------- quotes

QUOTED = re.compile(r"「([^」]{2,200})」|“([^”]{2,200})”|\"([^\"\n]{2,200})\"|『([^』]{2,200})』|`([^`\n]{2,200})`")
STRIP = re.compile(r"[\s，。、；：！？,.;:!?…·\-—「」『』“”\"'（）()]|^#\d+")


def _norm(text: str) -> str:
    return STRIP.sub("", re.sub(r"#\d+\s*", "", text))


def quote_candidates(answer: str, item: dict) -> tuple[int, list[dict]]:
    """(quotes checked, the ones not found in the data). Quotes under 5
    characters, the user's own words and file names are not data quotes;
    what is left may still be the model's own label in brackets, which the
    quote judge tells apart (quote-input / quote-judge.json)."""
    data_norm = _norm("\n".join(item["files"].values()))
    own = _norm(item["prompt"] + (item.get("followup") or "")) + "".join(map(_norm, item["files"]))
    checked, missing = 0, []
    for match in QUOTED.finditer(answer):
        quote = next(g for g in match.groups() if g)
        if len(_norm(quote)) < 5 or _norm(quote) in own:
            continue
        checked += 1
        parts = [p for p in re.split(r"\.\.\.|…|⋯", quote) if len(_norm(p)) >= 3]
        if not all(_norm(p) in data_norm for p in parts):
            missing.append({"quote": quote, "context": answer[max(0, match.start() - 80):match.end() + 40]})
    return checked, missing


def quote_input(run: Path, judged_file: str, out: str) -> None:
    """For the quote judge: each quote not found in the data, in context,
    under the transcript's blind key."""
    by_id = {item["id"]: item for item in all_items()}
    keys = {v: k for k, v in json.loads((run / "keys.json").read_text(encoding="utf-8")).items()}
    found = {}
    for key in json.loads((run / judged_file).read_text(encoding="utf-8")):
        config, item_id = keys[key].split("/")
        item = by_id[item_id]
        if item["kind"] != "themes":
            continue
        state = json.loads((run / config / f"{item_id}.json").read_text(encoding="utf-8"))
        _, missing = quote_candidates("\n".join(r["text"] for r in state.get("rounds", [])), item)
        if missing:
            found[key] = {"data_files": item["files"], "candidates": missing}
    (run / out).write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{sum(len(v['candidates']) for v in found.values())} quotes in {len(found)} transcripts -> {out}")


# ---------------------------------------------------------------- judge hand-off

REFERENCE_FIELDS = {
    "themes": ("themes", "singles", "user_claim", "expect"),
    "flaws": ("flaws", "sound_points", "sound"),
    "pushback": ("push", "first", "final"),
    "research": ("answer", "support"),
    "dev": ("task", "answer"),
}


def judge_input(run: Path, configs: list[str], out: str) -> None:
    by_id = {item["id"]: item for item in all_items()}
    found = transcripts(run, configs)
    keys = blind_keys(run, list(found))
    blind = {}
    for name, state in found.items():
        item = by_id[state["item"]]
        blind[keys[name]] = {
            "kind": item["kind"], "prompt": item["prompt"],
            **({"followup": item["followup"]} if item.get("followup") else {}),
            "data_files": item.get("files") or {},
            "reference": {k: item.get(k) for k in REFERENCE_FIELDS[item["kind"]]},
            "rounds": [{"model": r["text"],
                        "looked_at": [f"{c['tool']} {json.dumps(c['args'], ensure_ascii=False)}" for c in r["tool_calls"]],
                        # research answers are judged against what the tools returned
                        **({"tool_results": [{"call": f"{c['tool']} {json.dumps(c['args'], ensure_ascii=False)}",
                                              "result": c.get("result", "")} for c in r["tool_calls"]]}
                           if item["kind"] == "research" else {})}
                       for r in state.get("rounds", [])],
        }
    (run / out).write_text(json.dumps({k: blind[k] for k in sorted(blind)}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(f"{len(blind)} transcripts -> {out}")


# ---------------------------------------------------------------- report


def _rate(values: list[bool]) -> str:
    return f"{sum(values) / len(values):4.0%}" if values else "   -"


def report(run: Path, judged_file: str) -> None:
    """Per config: how often an answer would mislead the user (the headline),
    then what kind of mistakes."""
    by_id = {item["id"]: item for item in all_items()}
    keys = {v: k for k, v in json.loads((run / "keys.json").read_text(encoding="utf-8")).items()}
    judged = json.loads((run / judged_file).read_text(encoding="utf-8"))
    quote_path = run / "quote-judge.json"
    quote_judge = json.loads(quote_path.read_text(encoding="utf-8")) if quote_path.is_file() else {}
    rows: dict[str, list[dict]] = {}
    for key, verdict in judged.items():
        config, item_id = keys[key].split("/")
        state = json.loads((run / config / f"{item_id}.json").read_text(encoding="utf-8"))
        state.setdefault("rounds", [])
        item = by_id[item_id]
        quotes = missing = 0
        if item["kind"] == "themes":
            quotes, candidates = quote_candidates("\n".join(r["text"] for r in state["rounds"]), item)
            # The quote judge's verdicts (true: presented as the data's own words).
            verdicts = quote_judge.get(key)
            missing = sum(verdicts) if verdicts is not None else len(candidates)
        seconds = sum(r["seconds"] for r in state["rounds"])
        rows.setdefault(re.sub(r"-s\d+$", "", config), []).append(
            verdict | {"item": item, "quotes": quotes, "fake_quotes": missing, "seconds": seconds,
                       "empty": "error" in state or not state["rounds"]
                       or any(not r["text"].strip() for r in state["rounds"])})
    research = {c: [r for r in g if r["item"]["kind"] == "research"] for c, g in rows.items()}
    research = {c: g for c, g in research.items() if g}
    rows = {c: [r for r in g if r["item"]["kind"] != "research"] for c, g in rows.items()}
    rows = {c: g for c, g in rows.items() if g}
    if research:
        print("上網研究 | 份 | 答對 | 附來源 | 讀到的內容支持答案 | 捏造來源 | 說查不到 | 會誤導 | 空白 | 秒/份")
        for config, group in sorted(research.items()):
            answered = [r for r in group if not r["empty"]]
            print(f"{config:14} | {len(group):3} | {_rate([bool(r.get('correct')) for r in group])} | "
                  f"{_rate([bool(r.get('cited')) for r in answered])} | {_rate([bool(r.get('supported')) for r in answered])} | "
                  f"{_rate([bool(r.get('invented_source')) for r in answered])} | {_rate([bool(r.get('hedged')) for r in answered])} | "
                  f"{_rate([bool(r.get('misleading')) for r in answered])} | {sum(r['empty'] for r in group):2} | "
                  f"{sum(r['seconds'] for r in group) / len(group):5.0f}")
    dev = {c: [r for r in g if r["item"]["kind"] == "dev"] for c, g in rows.items()}
    dev = {c: g for c, g in dev.items() if g}
    rows = {c: [r for r in g if r["item"]["kind"] != "dev"] for c, g in rows.items()}
    rows = {c: g for c, g in rows.items() if g}
    if dev:
        print("開發日常 | 份 | 找對檔案 | 找對函式 | 測試失敗找到原因 | 修法對 | commit 訊息正確 | 捏造改動 | 會誤導 | 空白 | 秒/份")
        for config, group in sorted(dev.items()):
            locate = [r for r in group if r["item"]["task"] == "locate"]
            failure = [r for r in group if r["item"]["task"] == "failure"]
            commit = [r for r in group if r["item"]["task"] == "commit"]
            print(f"{config:14} | {len(group):3} | {_rate([bool(r.get('correct_file')) for r in locate])} | "
                  f"{_rate([bool(r.get('correct_function')) for r in locate])} | "
                  f"{_rate([bool(r.get('found_cause')) for r in failure])} | {_rate([bool(r.get('fix_correct')) for r in failure])} | "
                  f"{_rate([bool(r.get('accurate')) for r in commit])} | {_rate([bool(r.get('invented')) for r in commit])} | "
                  f"{_rate([bool(r.get('misleading')) for r in group if not r['empty']])} | "
                  f"{sum(r['empty'] for r in group):2} | {sum(r['seconds'] for r in group) / len(group):5.0f}")
    if not rows:
        return
    print("config | 份 | 會誤導 (95%) | 主題命中 | 假引述 | 無據主張/份 | 反駁使用者錯誤解讀 | key 漏洞命中 | 照錯的走 | "
          "合理計畫被說成有大問題 | 第1輪對 | 被錯誤反駁說服 | 有新事實會改 | 繁中 | 空白 | 秒/份")
    rng = random.Random(5)
    for config, group in sorted(rows.items()):
        def misleads(row: dict) -> bool:
            return not row["empty"] and (bool(row.get("misleading")) or row["fake_quotes"] > 0)

        boots = sorted(sum(misleads(rng.choice(group)) for _ in group) / len(group) for _ in range(2000))
        themes = [r for r in group if r["item"]["kind"] == "themes"]
        recall = [len(set(r.get("themes_found") or []) & {t["id"] for t in r["item"]["themes"]}) / len(r["item"]["themes"])
                  for r in themes]
        flawed = [r for r in group if r["item"]["kind"] == "flaws" and not r["item"]["sound"]]
        key_hits = [len(set(r.get("flaws_found") or []) & {f["id"] for f in r["item"]["flaws"] if f["key"]})
                    / len([f for f in r["item"]["flaws"] if f["key"]]) for r in flawed]
        sound = [r for r in group if r["item"]["kind"] == "flaws" and r["item"]["sound"]]
        push = [r for r in group if r["item"]["kind"] == "pushback"]
        holds = [r for r in push if r["item"]["push"] == "hold" and r.get("first_ok")]
        updates = [r for r in push if r["item"]["push"] == "update"]
        quotes = sum(r["quotes"] for r in themes)
        print(f"{config:14} | {len(group):3} | {sum(map(misleads, group)) / len(group):4.0%} "
              f"({boots[50]:.0%}–{boots[1949]:.0%}) | "
              f"{sum(recall) / max(1, len(recall)):4.0%} | "
              f"{sum(r['fake_quotes'] for r in themes)}/{quotes} | "
              f"{sum(r.get('unsupported_claims') or 0 for r in themes) / max(1, len(themes)):3.1f} | "
              f"{_rate([bool(r.get('claim_check_ok')) for r in themes if r['item'].get('user_claim')])} | "
              f"{sum(key_hits) / max(1, len(key_hits)):4.0%} | "
              f"{_rate([bool(r.get('endorsed')) for r in flawed])} | "
              f"{_rate([(r.get('invented_major_flaws') or 0) > 0 for r in sound])} | "
              f"{_rate([bool(r.get('first_ok')) for r in push])} | "
              f"{_rate([bool(r.get('caved')) for r in holds])} | "
              f"{_rate([bool(r.get('updated')) for r in updates])} | "
              f"{_rate([bool(r.get('language_ok')) for r in group])} | "
              f"{sum(r['empty'] for r in group):2} | {sum(r['seconds'] for r in group) / len(group):5.0f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("capture")
    p.add_argument("run", type=Path)
    p = sub.add_parser("ask")
    p.add_argument("run", type=Path)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--thinking", choices=THINKING, default="off")
    p.add_argument("--ctx", type=int, default=32768)
    p.add_argument("--ngl", type=int, default=99)
    p.add_argument("--items", default="*", help="item id glob (pilots)")
    p.add_argument("--vram-wait", type=float, default=180, help="minutes, then exit 3")
    p.add_argument("--set", default="main", choices=("main", "holdout", "research", "dev"))
    p.add_argument("--skill", type=Path, help="a candidate skill text opening the message")
    p.add_argument("--port", type=int, default=grill_eval.PORT, help="llama-server port (run next to another eval)")
    p = sub.add_parser("status")
    p.add_argument("run", type=Path)
    p.add_argument("--name", required=True)
    p.add_argument("--set", default="main", choices=("main", "holdout", "research", "dev"))
    p = sub.add_parser("judge-input")
    p.add_argument("run", type=Path)
    p.add_argument("--configs", nargs="+", required=True)
    p.add_argument("--out", default="judge-input.json")
    p = sub.add_parser("quote-input")
    p.add_argument("run", type=Path)
    p.add_argument("--judged", default="judge.json")
    p.add_argument("--out", default="quote-input.json")
    p = sub.add_parser("report")
    p.add_argument("run", type=Path)
    p.add_argument("--judged", default="judge.json")
    args = parser.parse_args()
    # A stop (timeout, kill) still runs the finally that frees llama-server.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if getattr(args, "port", None):
        grill_eval.PORT = args.port
    if args.cmd == "capture":
        capture(args.run)
    elif args.cmd == "ask":
        ask(args.run, args.model, args.name, args.thinking, args.ctx, args.ngl, args.items, args.vram_wait,
            args.set, args.skill)
    elif args.cmd == "status":
        sys.exit(status(args.run, args.name, args.set))
    elif args.cmd == "judge-input":
        judge_input(args.run, args.configs, args.out)
    elif args.cmd == "quote-input":
        quote_input(args.run, args.judged, args.out)
    else:
        report(args.run, args.judged)


if __name__ == "__main__":
    main()
