"""Character-mode turn-taking eval (handoff/dev-box.md §5.2): speak or stay silent, and what.

Usage (from the repo root; llama-server is found on PATH or in .runtime/tools):
    python scripts/dev/turn_eval.py --stats                  # test-set stats only
    python scripts/dev/turn_eval.py --policy old             # production path at a pinned commit
    python scripts/dev/turn_eval.py --policy new --judge     # character_turn(), then Claude judge
    python scripts/dev/turn_eval.py --judge-only old-gemma-4-E2B-it-qat-UD-Q4_K_XL

Every conversation in scripts/dev/turn_eval_data.jsonl is played line by line
with teacher forcing: the character's scripted lines always enter the history,
whatever the model said. At every other line (a decision point) we record
whether the character spoke, what it said and how long that took, then report:

  recall       share of `speak` lines where it spoke        (target >= 95%)
  false speak  share of `silent` lines where it spoke       (target <= 10%)
  ...per tag and per engagement mode; `either` lines count for neither.

Policies:
  old  CopilotRuntime._handle_turn/_speak as of --old-rev (default: the commit
       before the character-mode rewrite), loaded from git so the baseline
       stays fixed while silenttalk/ changes: _MIN_TRIGGER_CHARS, address_of,
       should_reply with CharacterState, simulated clock (--seconds-per-line),
       ConversationStore.render_for_prompt, LocalReplyModel.character_reply.
  new  LocalReplyModel.character_turn(turns=..., summary="", addressed=...,
       engagement=...) from the working tree. See `new_policy_turn`.

--judge rates every generated reply (blind to the policy) with the claude CLI,
exactly like persona_eval.py: natural / voice / fit 1-5 and breaks_character.
Claude is only an evaluator here (handoff/dev-box.md rule 2). The test set is synthetic;
results go to .runtime/dev/turn_eval/<tag>.json. Nothing but numbers and ids is
printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib
import types
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

DATA_PATH = Path(__file__).with_name("turn_eval_data.jsonl")
OUT_DIR = PROJECT_DIR / ".runtime" / "dev" / "turn_eval"
EVAL_PORT = 18411
# Last commit with the old character path (keyword gate + character_reply),
# before llm.py / runtime.py / character.py / context.py were rewritten.
OLD_REV = "5924fd083cea63c4e15fd48421eddd7b51ac1169"
EXPECTS = ("speak", "silent", "either")
REQUIRED_TAGS = (
    "called_by_name", "called_mid", "third_person", "you_to_other", "misheard_name",
    "reply_to_bot", "ai_probe", "assistant_request", "private_talk",
    "question_to_group", "plain",
)
# Mirrors silenttalk/copilot/runtime.py at OLD_REV.
_MIN_TRIGGER_CHARS = 2
OWNER_ID = 0

# Production defaults for [copilot] (CopilotConfig at OLD_REV); config.toml wins.
COPILOT_DEFAULTS: dict[str, Any] = {
    "llm_context_size": 4096,
    "recent_turns": 16,
    "max_session_turns": 100,
    "context_char_budget": 2400,
    "max_reply_chars": 72,
    "llm_model": "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
    "llm_gpu_layers": 99,
    "llm_low_vram": True,
    "compact_context": True,
    "compact_keep_turns": 20,
    "compact_batch_turns": 12,
    "character_ai_honesty": "in_character",
    "character_follow_up_seconds": 20.0,
    "character_cooldown_seconds": 8.0,
    "character_turn_gap": 3,
}


# --------------------------------------------------------------------------- data


@dataclass
class Conversation:
    id: str
    persona_id: str
    engagement: str
    scene: str
    lines: list[dict[str, Any]]


@dataclass
class TestSet:
    meta: dict[str, Any]
    conversations: list[Conversation] = field(default_factory=list)

    def decision_points(self) -> list[tuple[Conversation, int, dict[str, Any]]]:
        return [
            (conv, index, line)
            for conv in self.conversations
            for index, line in enumerate(conv.lines)
            if not line.get("self")
        ]


def load_test_set(path: Path = DATA_PATH) -> TestSet:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or "meta" not in rows[0]:
        raise SystemExit(f"{path}: first line must be the meta header")
    test_set = TestSet(rows[0]["meta"])
    persona_ids = {p["id"] for p in test_set.meta.get("personas", [])}
    for row in rows[1:]:
        conv = Conversation(
            row["id"], row["persona_id"], row["engagement"], row.get("scene", ""), row["lines"]
        )
        if conv.engagement not in ("called", "active"):
            raise SystemExit(f"{conv.id}: engagement must be called|active")
        if not (conv.persona_id.startswith("preset-") or conv.persona_id in persona_ids):
            raise SystemExit(f"{conv.id}: unknown persona {conv.persona_id}")
        for index, line in enumerate(conv.lines):
            where = f"{conv.id}#{index}"
            if not line.get("text", "").strip():
                raise SystemExit(f"{where}: empty text")
            if line.get("self"):
                continue
            if line.get("expect") not in EXPECTS:
                raise SystemExit(f"{where}: expect must be one of {EXPECTS}")
            if not line.get("tags"):
                raise SystemExit(f"{where}: tags missing")
            if line.get("via", "voice") not in ("voice", "text"):
                raise SystemExit(f"{where}: via must be voice|text")
            if line.get("mention") and line.get("via") != "text":
                raise SystemExit(f"{where}: mention only exists for typed lines")
        test_set.conversations.append(conv)
    return test_set


def print_stats(test_set: TestSet) -> None:
    points = test_set.decision_points()
    by_expect = defaultdict(int)
    by_tag: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_mode: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for conv, _, line in points:
        by_expect[line["expect"]] += 1
        by_mode[conv.engagement][line["expect"]] += 1
        for tag in line["tags"]:
            by_tag[tag][line["expect"]] += 1
    lengths = [len(conv.lines) for conv in test_set.conversations]
    humans = [len({l["speaker"] for l in conv.lines if not l.get("self")}) for conv in test_set.conversations]
    modes = defaultdict(int)
    for conv in test_set.conversations:
        modes[conv.engagement] += 1
    total = len(points)
    print(f"conversations {len(test_set.conversations)}  "
          f"(called {modes['called']}, active {modes['active']})  "
          f"lines {min(lengths)}-{max(lengths)}  humans {min(humans)}-{max(humans)}")
    speakish = by_expect["speak"] + by_expect["either"]
    print(f"decision points {total}: speak {by_expect['speak']}  either {by_expect['either']}  "
          f"silent {by_expect['silent']}  (speak+either {speakish / total:.0%})")
    for mode, counts in sorted(by_mode.items()):
        print(f"  {mode:<7} " + "  ".join(f"{k} {counts[k]}" for k in EXPECTS))
    print(f"{'tag':<18}{'n':>5}{'speak':>7}{'either':>8}{'silent':>8}")
    for tag in sorted(by_tag, key=lambda t: -sum(by_tag[t].values())):
        counts = by_tag[tag]
        print(f"{tag:<18}{sum(counts.values()):>5}{counts['speak']:>7}"
              f"{counts['either']:>8}{counts['silent']:>8}")
    missing = [t for t in REQUIRED_TAGS if t not in by_tag]
    if missing:
        print("missing required tags:", ", ".join(missing))


# ------------------------------------------------------------------ old snapshot


def load_old_modules(rev: str) -> types.SimpleNamespace:
    """The copilot modules as of `rev`, imported under a private package name so
    the working tree (being rewritten) cannot change the baseline. "worktree"
    imports silenttalk.copilot as it is on disk instead."""
    if rev == "worktree":
        from silenttalk.copilot import character, context, llm, persona
        from silenttalk.copilot import types as ctypes
        return types.SimpleNamespace(
            character=character, context=context, llm=llm, persona=persona, types=ctypes
        )
    package = "_turn_eval_old"
    if package in sys.modules:
        mod = sys.modules[package]
        return types.SimpleNamespace(**{n: getattr(mod, n) for n in
                                        ("character", "context", "llm", "persona", "types")})
    root = types.ModuleType(package)
    root.__path__ = []  # type: ignore[attr-defined]
    sys.modules[package] = root
    names = ("types", "script", "persona", "character", "context", "llm")
    for name in names:
        source = subprocess.run(
            ["git", "show", f"{rev}:silenttalk/copilot/{name}.py"],
            cwd=PROJECT_DIR, capture_output=True, text=True, check=True,
        ).stdout
        for other in names:
            source = source.replace(f"silenttalk.copilot.{other}", f"{package}.{other}")
        spec = importlib.util.spec_from_loader(f"{package}.{name}", loader=None)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        module.__file__ = f"<{rev[:10]}:silenttalk/copilot/{name}.py>"
        sys.modules[f"{package}.{name}"] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
        setattr(root, name, module)
    return types.SimpleNamespace(**{n: getattr(root, n) for n in
                                    ("character", "context", "llm", "persona", "types")})


def resolve_persona(persona_module: Any, test_set: TestSet, persona_id: str) -> Any:
    for preset in persona_module.PRESET_PERSONAS:
        if preset.id == persona_id:
            return preset
    for raw in test_set.meta.get("personas", []):
        if raw["id"] == persona_id:
            return persona_module.persona_from_dict(raw)
    raise SystemExit(f"unknown persona {persona_id}")


def persona_sheet(persona: Any) -> str:
    """The character sheet plus the relationship list, the way the character
    prompt shows it since d0bd062 (render_system_block leaves relationships out)."""
    sheet = persona.render_system_block()
    relations = [f"- {rel.name}：{rel.relation}" for rel in persona.relationships
                 if rel.name and rel.relation]
    if relations:
        sheet += "\n你和這些人的關係（他們說話時，對話裡會出現這個名字）：\n" + "\n".join(relations)
    return sheet


# Set by --cpu: no GPU layers, for when a training run holds the GPU.
# Decisions and lines compare fine; latencies do not.
CPU_ONLY = False
# Set by --lora-uses: which requests the adapter is applied to.
LORA_USES: tuple[str, ...] | None = None


def copilot_config() -> dict[str, Any]:
    values = dict(COPILOT_DEFAULTS)
    config_path = PROJECT_DIR / "config.toml"
    if config_path.exists():
        with config_path.open("rb") as handle:
            values.update(tomllib.load(handle).get("copilot", {}))
    if CPU_ONLY:
        values["llm_gpu_layers"] = 0
    return values


def ensure_llama_server_on_path() -> None:
    if shutil.which("llama-server"):
        return
    tools = sorted((PROJECT_DIR / ".runtime" / "tools").glob("llama-b*"))
    if tools:
        os.environ["PATH"] = f"{tools[-1]}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["LD_LIBRARY_PATH"] = f"{tools[-1]}{os.pathsep}{os.environ.get('LD_LIBRARY_PATH', '')}"


def make_model(
    llm_module: Any, model_path: Path, config: dict[str, Any], lora: Path | None = None
) -> Any:
    extra: dict[str, Any] = {"lora_path": lora} if lora is not None else {}
    if lora is not None and LORA_USES is not None:
        extra["lora_uses"] = LORA_USES
    return llm_module.LocalReplyModel(
        model_path,
        **extra,
        port=EVAL_PORT,
        context_size=int(config["llm_context_size"]),
        max_reply_chars=int(config["max_reply_chars"]),
        gpu_layers=int(config["llm_gpu_layers"]),
        low_vram=bool(config["llm_low_vram"]),
        ai_honesty=str(config["character_ai_honesty"]),
        # CPU-only prefill of a fresh persona prompt can pass the app's 15 s.
        request_timeout_seconds=180.0 if CPU_ONLY else 15.0,
    )


def _compact(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def _forget_generated(model: Any, line: str) -> None:
    """Teacher forcing: the generated line was never said, so it must not count
    as a recently used catchphrase; the scripted line is added instead."""
    recent = getattr(model, "_recent_character_lines", None)
    if line and recent and recent[-1] == line:
        recent.pop()


def _remember_said(model: Any, text: str) -> None:
    recent = getattr(model, "_recent_character_lines", None)
    if recent is not None:
        recent.append(text)


# ------------------------------------------------------------------------ policies


async def run_old(args: argparse.Namespace, test_set: TestSet) -> list[dict[str, Any]]:
    old = load_old_modules(args.old_rev)
    config = copilot_config()
    model = make_model(old.llm, args.model_path, config)
    await model.start()
    records: list[dict[str, Any]] = []
    try:
        for conv in test_set.conversations:
            persona = resolve_persona(old.persona, test_set, conv.persona_id)
            model.set_persona(persona)
            model._recent_character_lines.clear()
            store = old.context.ConversationStore(
                recent_turns=int(config["recent_turns"]),
                max_turns=int(config["max_session_turns"]),
                char_budget=int(config["context_char_budget"]),
                older_self_turns=4,
            )
            compact_keep = max(int(config["recent_turns"]), int(config["compact_keep_turns"]))
            compact_trigger = compact_keep + max(4, int(config["compact_batch_turns"]))
            policy = old.character.CharacterPolicy(
                mode=conv.engagement,
                follow_up_seconds=float(config["character_follow_up_seconds"]),
                cooldown_seconds=float(config["character_cooldown_seconds"]),
                turn_gap=int(config["character_turn_gap"]),
            )
            # set_character_mode(True) starts from a fresh state.
            state = old.character.CharacterState(turns_since_spoke=0)
            ids = speaker_ids(conv)
            now = 0.0
            for index, line in enumerate(conv.lines):
                now += args.seconds_per_line
                if line.get("self"):
                    # record_self(): what the bot actually said aloud.
                    store.append(old.types.ConversationTurn(
                        OWNER_ID, persona.name, line["text"], is_self=True, created_at=now
                    ))
                    state.spoke(now)
                    _remember_said(model, line["text"])
                    continue
                via = line.get("via", "voice")
                store.append(old.types.ConversationTurn(
                    ids[line["speaker"]], line["speaker"], line["text"], via=via, created_at=now
                ))
                if config["compact_context"] and len(store.snapshot()) >= compact_trigger:
                    # Production compacts in the background when idle; here it
                    # runs before the decision and is not counted as latency.
                    await compact(store, model, compact_keep)
                state.heard()
                record = base_record(conv, index, line)
                if len(_compact(line["text"])) < _MIN_TRIGGER_CHARS:
                    records.append(record | {"gate": "short"})
                    continue
                address = old.character.address_of(
                    line["text"], persona, mention=bool(line.get("mention"))
                )
                if not old.character.should_reply(
                    policy, state, line["text"], addressed=address.worth_reading, now=now
                ):
                    records.append(record | {"gate": "rule"})
                    continue
                if not address.worth_reading:
                    state.follow_ups += 1
                started = time.perf_counter()
                reply = await model.character_reply(
                    context=store.render_for_prompt(),
                    trigger_speaker=line["speaker"],
                    trigger_text=line["text"],
                    addressed=address.called,
                )
                latency = (time.perf_counter() - started) * 1000
                _forget_generated(model, reply)
                records.append(record | {
                    "gate": "model", "addressed": address.called, "spoke": bool(reply),
                    "reply": reply, "latency_ms": round(latency, 1),
                })
            print(f"  {conv.id}: done", flush=True)
    finally:
        await model.stop()
    return records


async def compact(store: Any, model: Any, keep: int) -> None:
    older = store.pending_compaction(keep)
    if not older:
        return
    transcript = "\n".join(
        f"{'你' if turn.is_self else turn.speaker_name}：{turn.text}" for turn in older
    )
    summary = await model.summarize(transcript, store.summary)
    if summary:
        store.compact(summary, len(older))


async def new_policy_turn(model: Any, turns: list[Any], addressed: bool, engagement: str) -> str:
    """The one call the new architecture makes per heard line. Adjust here if
    the signature of character_turn changes."""
    return await model.character_turn(
        turns=turns, summary="", addressed=addressed, engagement=engagement
    )


async def run_new(args: argparse.Namespace, test_set: TestSet) -> list[dict[str, Any]]:
    from silenttalk.copilot import llm as llm_module
    from silenttalk.copilot import persona as persona_module
    from silenttalk.copilot.types import ConversationTurn

    if not hasattr(llm_module.LocalReplyModel, "character_turn"):
        raise SystemExit(
            "--policy new needs LocalReplyModel.character_turn(turns=..., summary=..., "
            "addressed=..., engagement=...) in silenttalk/copilot/llm.py; it does not exist yet."
        )
    config = copilot_config()
    lora = PROJECT_DIR / ".runtime" / "models" / "llm" / args.lora if args.lora else None
    if lora is not None and not lora.is_file():
        raise SystemExit(f"LoRA adapter not found: {lora}")
    model = make_model(llm_module, args.model_path, config, lora)
    await model.start()
    records: list[dict[str, Any]] = []
    try:
        for conv in test_set.conversations:
            persona = resolve_persona(persona_module, test_set, conv.persona_id)
            model.set_persona(persona)
            if hasattr(model, "_recent_character_lines"):
                model._recent_character_lines.clear()
            ids = speaker_ids(conv)
            turns: list[Any] = []
            now = time.time()
            for index, line in enumerate(conv.lines):
                now += args.seconds_per_line
                if line.get("self"):
                    turns.append(ConversationTurn(
                        OWNER_ID, persona.name, line["text"], is_self=True, created_at=now
                    ))
                    _remember_said(model, line["text"])
                    continue
                turns.append(ConversationTurn(
                    ids[line["speaker"]], line["speaker"], line["text"],
                    via=line.get("via", "voice"), created_at=now,
                ))
                started = time.perf_counter()
                reply = await new_policy_turn(
                    model, list(turns), bool(line.get("mention")), conv.engagement
                )
                latency = (time.perf_counter() - started) * 1000
                _forget_generated(model, reply)
                records.append(base_record(conv, index, line) | {
                    "gate": "model", "addressed": bool(line.get("mention")),
                    "spoke": bool(reply), "reply": reply, "latency_ms": round(latency, 1),
                })
            print(f"  {conv.id}: done", flush=True)
    finally:
        await model.stop()
    return records


def speaker_ids(conv: Conversation) -> dict[str, int]:
    ids: dict[str, int] = {}
    for line in conv.lines:
        if not line.get("self"):
            ids.setdefault(line["speaker"], len(ids) + 1)
    return ids


def base_record(conv: Conversation, index: int, line: dict[str, Any]) -> dict[str, Any]:
    return {
        "conv": conv.id, "index": index, "persona": conv.persona_id,
        "engagement": conv.engagement, "speaker": line["speaker"], "text": line["text"],
        "via": line.get("via", "voice"), "mention": bool(line.get("mention")),
        "expect": line["expect"], "tags": line["tags"],
        "spoke": False, "reply": "", "latency_ms": 0.0,
    }


# ------------------------------------------------------------------------ metrics


def _rates(records: list[dict[str, Any]]) -> dict[str, Any]:
    speak = [r for r in records if r["expect"] == "speak"]
    silent = [r for r in records if r["expect"] == "silent"]
    either = [r for r in records if r["expect"] == "either"]

    def share(rows: list[dict[str, Any]]) -> float | None:
        return round(sum(r["spoke"] for r in rows) / len(rows), 4) if rows else None

    return {
        "n_speak": len(speak), "recall": share(speak),
        "n_silent": len(silent), "false_speak": share(silent),
        "n_either": len(either), "either_spoke": share(either),
    }


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    tags = sorted({t for r in records for t in r["tags"]})
    calls = [r["latency_ms"] for r in records if r.get("gate") == "model"]
    spoken = [r["latency_ms"] for r in records if r.get("gate") == "model" and r["spoke"]]
    silent_calls = [r["latency_ms"] for r in records if r.get("gate") == "model" and not r["spoke"]]
    return {
        "overall": _rates(records),
        "by_engagement": {
            mode: _rates([r for r in records if r["engagement"] == mode])
            for mode in ("called", "active")
        },
        "by_tag": {tag: _rates([r for r in records if tag in r["tags"]]) for tag in tags},
        "latency_ms": {
            "model_calls": len(calls),
            "median": _percentile(calls, 0.5), "p90": _percentile(calls, 0.9),
            "spoke_median": _percentile(spoken, 0.5), "spoke_p90": _percentile(spoken, 0.9),
            "silent_median": _percentile(silent_calls, 0.5),
            "silent_p90": _percentile(silent_calls, 0.9),
        },
        "replies": sum(r["spoke"] for r in records),
        "decision_points": len(records),
        "gates": {g: sum(r.get("gate") == g for r in records) for g in ("short", "rule", "model")},
    }


def _pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value * 100:5.1f}%"


def print_metrics(metrics: dict[str, Any], judge: dict[str, Any] | None = None) -> None:
    o = metrics["overall"]
    print(f"\ndecision points {metrics['decision_points']}  replies {metrics['replies']}  "
          f"gates {metrics['gates']}")
    print(f"recall {_pct(o['recall'])} (n={o['n_speak']})   false speak {_pct(o['false_speak'])} "
          f"(n={o['n_silent']})   either spoke {_pct(o['either_spoke'])} (n={o['n_either']})")
    print(f"{'':<18}{'recall':>8}{'n':>5}{'false':>9}{'n':>5}{'either':>9}{'n':>5}")
    rows = [(f"[{m}]", r) for m, r in metrics["by_engagement"].items()]
    rows += sorted(metrics["by_tag"].items())
    for name, r in rows:
        print(f"{name:<18}{_pct(r['recall']):>8}{r['n_speak']:>5}{_pct(r['false_speak']):>9}"
              f"{r['n_silent']:>5}{_pct(r['either_spoke']):>9}{r['n_either']:>5}")
    lat = metrics["latency_ms"]
    print(f"latency per model call: median {lat['median']} ms  p90 {lat['p90']} ms  "
          f"(calls {lat['model_calls']}; spoke median {lat['spoke_median']} / "
          f"silent median {lat['silent_median']})")
    if judge:
        print("judge: " + "  ".join(
            f"{k} {v:.2f}" if isinstance(v, float) else f"{k} {v}" for k, v in judge.items()
        ))


# -------------------------------------------------------------------------- judge

JUDGE_PROMPT = """你是嚴格的評審，評估小型 AI 在 Discord 語音頻道裡「自己扮演角色聊天」的表現。這個 Bot 是 AI 扮演的角色，設定為不出戲：被問是不是 AI 時用角色的方式帶過、反問或開玩笑是正確的。對話是語音辨識結果，可能有錯字、沒有標點。

【角色設定】
{sheet}

下面每組是對話的最後幾句（標「（角色）」的是角色自己之前說的話），以及角色接著說出口的一句話。

評分：
- natural 1-5：像台灣人在語音裡會直接說出口的話，而且真的在接最後一句
- voice 1-5：口吻、用詞、句長、口頭禪用法像不像這個角色（口頭禪硬塞也扣分）
- fit 1-5：在這個時機開口恰不恰當：最後一句真的在跟角色說話、或它插話很自然，給高分；別人在私下聊、在跟別人說話、或它根本不需要開口卻插嘴，給低分
- breaks_character 0/1：1＝自稱是 AI、程式、語言模型或助理而脫離角色，或答應做它做不到的事（寫程式、查資料、設鬧鐘、翻譯整段文字）{relation_rule}

{items}

只輸出 JSON 陣列，不要其他文字，長度 {k}，依序對應：[{{"id": 1, "natural": 4, "voice": 4, "fit": 4, "breaks_character": 0{relation_example}}}, ...]"""
RELATION_RULE = (
    "\n- relation 1-5：對設定裡有關係的人（例如老公、爸媽、孫子、兄弟），稱呼、口吻、親疏"
    "是否符合那個關係，像家人或另一半在講話；這句沒有跟這些人有關時給 3"
)


def run_judge(prompt: str, model: str) -> list[dict[str, Any]]:
    """The judge is `claude -p` (dev only; the test set is synthetic, never
    real conversations). Expects a JSON list back."""
    with tempfile.TemporaryDirectory() as cwd:
        result = subprocess.run(
            ["claude", "-p", "--model", model],
            input=prompt, capture_output=True, text=True, cwd=cwd, timeout=900,
        )
    match = re.search(r"\[.*\]", result.stdout, re.S)
    if result.returncode != 0 or not match:
        raise RuntimeError(f"judge failed ({result.returncode}): {result.stdout[-400:]}"
                           f"{result.stderr[-400:]}")
    return json.loads(match.group(0))


def judge_records(
    records: list[dict[str, Any]], test_set: TestSet, model: str, seed: int, batch: int
) -> dict[str, Any]:
    from silenttalk.copilot import persona as persona_module

    convs = {c.id: c for c in test_set.conversations}
    rng = random.Random(seed)
    spoken = [r for r in records if r["spoke"]]
    by_persona: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in spoken:
        by_persona[record["persona"]].append(record)
    for persona_id, items in sorted(by_persona.items()):
        persona = resolve_persona(persona_module, test_set, persona_id)
        rng.shuffle(items)
        for start in range(0, len(items), batch):
            chunk = items[start:start + batch]
            rendered = []
            for n, record in enumerate(chunk, 1):
                conv = convs[record["conv"]]
                window = conv.lines[max(0, record["index"] - 7):record["index"] + 1]
                chat = "\n".join(
                    f"{persona.name}（角色）：{l['text']}" if l.get("self")
                    else f"{l['speaker']}{'（打字）' if l.get('via') == 'text' else ''}：{l['text']}"
                    for l in window
                )
                rendered.append(f"=== 第 {n} 組 ===\n{chat}\n角色接著說：{record['reply']}")
            related = bool(persona.relationships)
            prompt = JUDGE_PROMPT.format(
                sheet=persona_sheet(persona), items="\n\n".join(rendered), k=len(chunk),
                relation_rule=RELATION_RULE if related else "",
                relation_example=', "relation": 4' if related else "",
            )
            scores = run_judge(prompt, model)
            for n, record in enumerate(chunk, 1):
                record["judge"] = next((s for s in scores if s.get("id") == n), {})
            print(f"  judged {persona_id} {start + len(chunk)}/{len(items)}", flush=True)
    return summarize_judge(records)


def summarize_judge(records: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [r["judge"] for r in records if r.get("judge")]

    def mean(key: str, rows: list[dict[str, Any]] = judged) -> float:
        values = [float(j[key]) for j in rows if key in j]
        return round(statistics.fmean(values), 3) if values else float("nan")

    out: dict[str, Any] = {"judged": len(judged)}
    for key in ("natural", "voice", "fit", "breaks_character"):
        out[key] = mean(key)
    for expect in EXPECTS:
        rows = [r["judge"] for r in records if r.get("judge") and r["expect"] == expect]
        out[f"fit_{expect}"] = mean("fit", rows)
    if any("relation" in j for j in judged):
        out["relation"] = mean("relation")
        owner = [r["judge"] for r in records if r.get("judge") and "owner" in r["tags"]]
        out["relation_owner"] = mean("relation", owner)
        out["natural_owner"] = mean("natural", owner)
        out["fit_owner"] = mean("fit", owner)
    probes = [r for r in records if "ai_probe" in r["tags"] and r.get("judge")]
    out["ai_probe_breaks"] = sum(int(r["judge"].get("breaks_character", 0)) for r in probes)
    return out


# ---------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--policy", choices=("old", "new"), default="old")
    parser.add_argument("--model", help="gguf in .runtime/models/llm/ (default: config.toml)")
    parser.add_argument("--lora", help="new policy only: adapter gguf in .runtime/models/llm/")
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--tag", help="results name (default: <policy>-<model stem>)")
    parser.add_argument("--old-rev", default=OLD_REV,
                        help="git revision for --policy old, or 'worktree'")
    parser.add_argument("--seconds-per-line", type=float, default=4.0)
    parser.add_argument("--only", help="comma-separated conversation ids (smoke runs)")
    parser.add_argument("--stats", action="store_true", help="print test-set stats and exit")
    parser.add_argument("--judge", action="store_true", help="rate replies with the claude CLI")
    parser.add_argument("--judge-only", metavar="TAG", help="judge an existing results file")
    parser.add_argument("--judge-model", default="sonnet")
    parser.add_argument("--judge-batch", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cpu", action="store_true",
                        help="no GPU layers (latencies are then not comparable)")
    parser.add_argument("--lora-uses", help="with --lora: comma-separated parts "
                        "(called,active,line) that use the adapter; default all")
    parser.add_argument("--port", type=int, help="llama-server port (default 18411)")
    args = parser.parse_args()
    global CPU_ONLY, LORA_USES, EVAL_PORT
    CPU_ONLY = args.cpu
    if args.lora_uses:
        LORA_USES = tuple(part.strip() for part in args.lora_uses.split(",") if part.strip())
    if args.port:
        EVAL_PORT = args.port

    data_sha256 = hashlib.sha256(args.data.read_bytes()).hexdigest()
    test_set = load_test_set(args.data)
    if args.only:
        wanted = set(args.only.split(","))
        test_set.conversations = [c for c in test_set.conversations if c.id in wanted]
    if args.stats:
        print_stats(test_set)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.judge_only:
        path = OUT_DIR / f"{args.judge_only}.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["judge"] = judge_records(
            result["records"], test_set, args.judge_model, args.seed, args.judge_batch
        )
        result["judge_model"] = args.judge_model
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print_metrics(result["metrics"], result["judge"])
        print(f"saved {path}")
        return 0

    config = copilot_config()
    model_name = args.model or str(config["llm_model"])
    args.model_path = PROJECT_DIR / ".runtime" / "models" / "llm" / model_name
    if not args.model_path.is_file():
        raise SystemExit(f"model not found: {args.model_path}")
    ensure_llama_server_on_path()
    tag = args.tag or f"{args.policy}-{Path(model_name).stem}"
    print(f"policy {args.policy}  model {model_name}  conversations "
          f"{len(test_set.conversations)}  port {EVAL_PORT}", flush=True)

    git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_DIR,
                              capture_output=True, text=True).stdout.strip()
    # The new policy runs the working tree; note if it differs from HEAD.
    git_dirty = bool(subprocess.run(["git", "status", "--porcelain", "silenttalk"],
                                    cwd=PROJECT_DIR, capture_output=True, text=True).stdout.strip())
    started = time.time()
    runner = run_old if args.policy == "old" else run_new
    records = asyncio.run(runner(args, test_set))
    metrics = compute_metrics(records)
    result: dict[str, Any] = {
        "tag": tag, "policy": args.policy, "model": model_name,
        "old_rev": args.old_rev if args.policy == "old" else None,
        "git_head": git_head, "silenttalk_dirty": git_dirty,
        "data": str(args.data.relative_to(PROJECT_DIR)) if args.data.is_relative_to(PROJECT_DIR)
        else str(args.data),
        "data_sha256": data_sha256,
        "seconds_per_line": args.seconds_per_line,
        "wall_seconds": round(time.time() - started, 1),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": metrics, "records": records,
    }
    path = OUT_DIR / f"{tag}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.judge:
        result["judge"] = judge_records(
            records, test_set, args.judge_model, args.seed, args.judge_batch
        )
        result["judge_model"] = args.judge_model
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_metrics(metrics, result.get("judge"))
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
