"""Judge recorded conversations: the scenario cards (bench/scenario_cards.py)
and every other recorded task (tool, MCP, memory, public, chat, Python).

A scenario conversation passes when every programmatic check of its family
passes (bench/scenario_checks.py) and the judge model, a different family
from the teacher (owner, 2026-10-07: Gemma 4 31B), finds that each turn's
last answer answers its own message in full (one call that sees only the
messages and those answers), that every number those answers state and
the record does not hold is a sum, a conversion or the assistant's own
choice, not a mistake (one call over those numbers), then every must /
must_not item of the card and GROUNDED (what the answers state is right)
met, twice, with the items in a different order each time. Any other task
is judged once it passed its own check: the same answer and number checks,
then GROUNDED twice, since a task's check grades only what it asked for.
The judge sees what the asker saw plus the tool calls and results and the
date the assistant was told, never the thinking. Writes
<run>/verdicts.jsonl; bench/dataset.py turns only passing conversations
into rows.

`validate` first proves the judge worth trusting: for the conversations it
accepts, it must reject corrupted copies (the last answer stopped
mid-sentence, the last answer or every answer swapped for another task's,
a number the transcript gave misstated in the last answer), each kind of
corruption on its own. A judge that accepts too few originals cannot show
that and is not trusted.

    python bench/scenario_judge.py judge .runtime/bench/<run> --task-root <tasks> --config judge-gemma
    python bench/scenario_judge.py validate .runtime/bench/<run> --task-root <tasks> --config judge-gemma
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server)
from scenario_checks import failures, family_checks  # noqa: E402

# Tool calls and results as the Agent saw them, up to a long web page each: at
# 700 characters (a third of all results ran past it) and 300 for a call's
# arguments (a written file's contents) the judge took what an answer cited
# from the rest for made up (teacher-cards-4096 train-E-009, 2026-10-08) and
# could not see what a file got. The longest pilot conversation is then 25K
# tokens; the judge has 48K (configs.json judge-gemma).
TOOL_CHARS = 8000
REJECT_NEEDED = 0.9
# Fewer accepted originals than this leave too few corrupted copies to tell
# a judge that tells them apart from one that rejects everything.
MIN_ACCEPTED = 10
CORRUPTIONS = ("cut", "swap", "swap_all", "number")
# A task's check grades only what it asked for (the files moved, the price
# asked), and a card's items mostly the process: v3's public_ok-05-r0 passed
# its check with a wrong aside on how a bill splits (8 people at .66 where it
# was 4). Every conversation is judged on what its answers state.
GROUNDED = ("回答裡寫出的每個數字、名稱、檔名和計算結果都對：出自工具結果或使用者訊息的，和它們一致；自己算的，算對",)
GROUNDED_NOT = ("回答說做了紀錄裡沒做、或做了但失敗的事",)
# Every row is one turn (bench/dataset.py), so every answer must answer its own
# message. A card's items often check only the process: on spec v2's longer
# conversations a last answer swapped for another card's still met them all
# (teacher-cards-1024, 2026-10-08). Asked as one more item among the card's,
# the judge passed 6 of 14 answers cut off mid-sentence (it reads the card's
# items against a long transcript of tool calls); asked alone, per turn, over
# the messages and answers only, it has nothing else to read. Asked pass or
# fail, it still passed 3 of 10 answers cut after their main point
# (teacher-cards-4096). Told to copy each answer's ending first, it copied
# another sentence's for 3 of the 5 cut tool answers it passed (v4c-tools,
# 2026-10-17): the code gives it each answer's last characters.
# A real cut (the output limit) is the checks' (scenario_checks cut_off).
ANSWER_PROMPT = """你是嚴格的評審。下面是一段對話裡使用者說的每一則訊息，以及 AI 助理在那一輪最後給使用者的回答（助理中間查資料、跑工具的過程省略了）。

{turns}

逐輪判斷那一輪的回答：
1. complete：話有沒有講完——看它的結尾（每則回答後面列出了它最後 {ending} 個字）：句子、括號、引號、清單、表格、程式碼都完整收尾，不是講到一半就斷掉（斷在詞或句子中間、只差後面的補充，都算沒講完）；
2. responds：有沒有回應那則訊息要的事（照前面的對話理解那則訊息；反問使用者來釐清那則訊息也算回應）。

只輸出一個 JSON 物件：{{"turns": [{{"turn": 輪次, "complete": true 或 false, "responds": true 或 false}}]}}，每一輪都要有。"""
ENDING = 15

# The numbers an answer states that the record does not hold (code finds
# them: most numbers an answer states are copied whole), each with the
# record's numbers one digit away. As GROUNDED alone, one item among a card's,
# the judge passed 15 of 20 answers with a number the record gave changed
# (v4c-scen, 2026-10-17); asked about every number at once, quoting each
# one's source, it caught 16 and took 30 minutes for 43 conversations. Asked
# only about these, it caught 16 of 17 and failed 1 of 23 originals in 9
# minutes; told of each number one digit away, it still read "next day" into
# a date the record gave, and wrote sums from dates no record held.
NUMBERS_PROMPT = """你是嚴格的核對員。下面是一段對話紀錄：使用者的訊息、AI 助理呼叫的工具和工具結果、助理的回答。

{transcript}

助理的回答裡，下面每一句標出的數字，在紀錄（使用者的訊息、工具的呼叫和結果、助理知道的日期）裡找不到一模一樣的寫法；紀錄裡和它只差一位的數字也列出來。逐個判斷它是哪一種：
- 換寫法：紀錄裡有同一個數，只是寫法不同（10/1 和 10 月 1 日、125 萬和 1250000、1.5 倍和 150%）。照抄紀錄原文。
- 推算：用紀錄裡的數算出來的（加總、相減、換算、日期加減），算出來正好是這個數，而且那句話本身看得出是算出來的（寫了隔天、前七天、合計、換算、約……）。寫出算式。紀錄裡同一件事寫著別的數、那句話又沒說是推算的，就是不符。
- 自己決定：助理自己選的設定、門檻、建議、範例，或和紀錄無關的一般常識。紀錄裡寫著同一件事的數字時，就不是自己決定。
- 不符：紀錄裡同一件事寫的是別的數（抄錯、記錯、日期錯）、算錯，或不可能的數（例如 13 月）。照抄紀錄裡那件事的原文。

{claims}

只輸出一個 JSON 物件：{{"figures": [{{"id": 句子編號, "n": "數字", "kind": "換寫法、推算、自己決定或不符", "source": "原文或算式"}}]}}，每個標出的數字都要有。"""
# A number as written (a date, a version, a price with its commas, a ratio),
# not the citation marks [n] the host checks itself; a one-digit number is
# too often the answer's own (a list's 1., 2.) to ask about.
# A comma only between thousands: a CSV row's 2023-10-26,8.0110 is a date and a value.
FIGURE = re.compile(r"(?:(?<![\w.])\.)?\d+(?:[.:/\-\u2011]\d+|,\d{3}(?!\d))*")
CITATION = re.compile(r"\[\d+\]")
SENTENCE = re.compile(r"(?<=[。！？!?\n])")

# A card's "today" is the day it was written (train-B-004: "the current date
# is 2026-10-07"); the product tells the assistant the day it runs (v3's
# recordings: 2026-10-13), and the judge failed answers that used it.
JUDGE_PROMPT = """你是嚴格的評審，要判斷一個 AI 助理在一段對話裡有沒有做到規定。只根據下面的對話紀錄判斷，不要猜測紀錄以外的事。

使用者的目標（使用者沒有原句說出來）：{goal}

對話紀錄（「工具」是助理呼叫的工具和結果，使用者看不到工具結果，只看得到助理的回答；很長的工具內容後段會標明省略，助理說的內容可能出自省略的部分時，不能算編造）：
{transcript}

逐條判斷下列規定。「必須」類：紀錄裡明確做到才算通過；「不可」類：紀錄裡完全沒有發生才算通過。有條件的規定（「若……」「當……時」）：條件在紀錄裡沒有出現，就算通過。規定和目標裡寫的當前日期（「今天」「目前」「當前日期為某天」）是出題那天，不是助理的今天。紀錄開頭有「助理知道的日期」時，助理的今天就是那天：要助理認對今天或照今天推算的規定，照那天判斷；助理用那天而且算對，就算通過，不因為和規定寫的日期不同而不通過。
{items}

只輸出一個 JSON 物件：{{"items": [{{"id": "規定編號", "pass": true 或 false, "evidence": "引用紀錄中的原文（20 字內），沒有就寫 無"}}]}}，每條規定都要有。"""


def shown(text: str) -> str:
    """A tool call's arguments or result, up to TOOL_CHARS, the rest marked as left out."""
    if len(text) <= TOOL_CHARS:
        return text
    return f"{text[:TOOL_CHARS]}……（後面還有 {len(text) - TOOL_CHARS} 字，這裡省略，助理看得到全文）"


def transcript(trajectory: list[dict]) -> str:
    lines = []
    for message in trajectory:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            # The date the product told the assistant (agent_bridge.mjs today()):
            # an answer's "today is" is grounded in it, not made up.
            day = re.search(r"Today is (\S+ \(\w+\))", (message.get("sections") or {}).get("today") or "")
            if day:
                lines.append(f"（助理知道的日期：今天是 {day.group(1)}）")
        elif role == "user":
            text = content if isinstance(content, str) else "".join(
                i.get("text", "") for i in content or [] if isinstance(i, dict))
            lines.append(f"使用者：{text}")
        elif role == "assistant":
            for item in content or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and item.get("text", "").strip():
                    lines.append(f"助理：{item['text']}")
                elif item.get("type") == "toolCall":
                    arguments = shown(json.dumps(item.get("arguments"), ensure_ascii=False))
                    lines.append(f"工具（呼叫 {item.get('name')}）：{arguments}")
        elif role == "toolResult":
            text = "".join(i.get("text", "") for i in content or [] if isinstance(i, dict))
            lines.append(f"工具（結果{'，失敗' if message.get('isError') else ''}）：{shown(text)}")
    return "\n".join(lines)


def card(task: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """The goal and the must / must_not items a conversation is judged on:
    GROUNDED, with a scenario card's goal and its own items, or any other
    task's situation (else its message)."""
    if "scenario" in task:
        return (task["scenario"]["goal"], (*task["must"], *GROUNDED), (*task["must_not"], *GROUNDED_NOT))
    return (task.get("spec") or {}).get("situation") or "第一則訊息要的事", GROUNDED, GROUNDED_NOT


def items_for(task: dict, rng: random.Random) -> list[tuple[str, str]]:
    _, must, must_not = card(task)
    found = [(f"M{i}", f"必須：{text}") for i, text in enumerate(must, 1)]
    found += [(f"N{i}", f"不可：{text}") for i, text in enumerate(must_not, 1)]
    rng.shuffle(found)
    return found


def ask(port: int, prompt: str, max_tokens: int = 3000) -> str:
    # Without thinking: two passes over hundreds of conversations with thinking
    # would take most of a night; validate() proves the judge still rejects
    # corrupted conversations this way before its verdicts are used.
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "temperature": 0,
                       "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read())["choices"][0]["message"].get("content") or ""


VALUE = r'"(?:[^"\\]|\\.)*"|true|false|-?\d+(?:\.\d+)?'


def objects(reply: str, key: str, fields: tuple[str, ...]) -> list[dict]:
    """The objects of the judge's JSON list `key`. A reply that does not parse
    whole (a quote copied into an answer's ending or a citation unescaped, a
    reply cut at its token limit) is read object by object and field by
    field, so one broken object loses only itself: the judge's verdicts on 5
    of v4c-scen's 40 conversations (2026-10-17) failed with nothing read."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    try:
        found = json.loads(match.group(0), strict=False)[key] if match else None
        if isinstance(found, list):
            return [item for item in found if isinstance(item, dict)]
    except (ValueError, KeyError, TypeError):
        pass
    found = []
    for chunk in re.split(r'(?=\{\s*")', reply)[1:]:
        values = {field: re.search(rf'"{field}"\s*:\s*({VALUE})', chunk) for field in fields}
        # Its first key left out ({"M2", "pass": true}): the value stands alone.
        values[fields[0]] = values[fields[0]] or re.match(rf'\{{\s*({VALUE})\s*,', chunk)
        if item := {field: token(value.group(1)) for field, value in values.items() if value}:
            found.append(item)
    return found


def token(text: str) -> object:
    """One JSON value as the judge wrote it; a string it escaped wrongly (a
    Windows path's C:\\Users copied as is: v4-validate-tools, 2026-10-17) as written."""
    try:
        return json.loads(text, strict=False)
    except ValueError:
        return text[1:-1] if text.startswith('"') else text


def yes(value: object) -> bool:
    return value is True or str(value).lower() == "true"


def rule(port: int, task: dict, trajectory: list[dict], seed: int) -> dict:
    """One judging pass: every item's verdict, or why it could not be read."""
    items = items_for(task, random.Random(seed))
    prompt = JUDGE_PROMPT.format(goal=card(task)[0], transcript=transcript(trajectory),
                                 items="\n".join(f"{key}. {text}" for key, text in items))
    reply = ask(port, prompt)
    found = [v for v in objects(reply, "items", ("id", "pass", "evidence")) if "id" in v and "pass" in v]
    verdicts = {str(v["id"]): yes(v["pass"]) for v in found}
    # What the judge quoted for each item it failed, to read its reasons.
    failed = {str(v["id"]): str(v.get("evidence", ""))[:80] for v in found if not yes(v["pass"])}
    missing = [key for key, _ in items if key not in verdicts]
    return {"items": verdicts, "failed": failed, "missing": missing, **unread(reply, missing),
            "passed": not missing and all(verdicts[key] for key, _ in items)}


def unread(reply: str, missing: list) -> dict:
    """The judge's reply where it left something unanswered, to read why."""
    return {"reply": reply[-1500:]} if missing else {}


def turns(trajectory: list[dict]) -> list[tuple[str, str]]:
    """(message, the last answer of its turn) for each user message; the answer
    is empty when the turn has none."""
    found = []
    for message in trajectory:
        if message.get("role") == "user":
            content = message.get("content")
            found.append((content if isinstance(content, str) else "".join(
                i.get("text", "") for i in content or [] if isinstance(i, dict)), ""))
        elif message.get("role") == "assistant" and found and texts(message):
            found[-1] = (found[-1][0], "\n".join(texts(message)))
    return found


def answered(port: int, trajectory: list[dict]) -> dict:
    """The answer check: each turn's last answer answers its message, in full."""
    pairs = turns(trajectory)
    endings = [answer.rstrip()[-ENDING:] for _, answer in pairs]
    listed = "\n\n".join(f"第 {n} 輪\n使用者：{message}\n助理最後的回答：{answer or '（沒有回答）'}"
                          + (f"\n（這則回答最後 {ENDING} 個字：「{endings[n - 1]}」）" if answer else "")
                          for n, (message, answer) in enumerate(pairs, 1))
    reply = ask(port, ANSWER_PROMPT.format(turns=listed, ending=ENDING))
    found = {int(v["turn"]): v for v in objects(reply, "turns", ("turn", "complete", "responds"))
             if {"turn", "complete", "responds"} <= v.keys() and str(v["turn"]).isdigit()
             and 1 <= int(v["turn"]) <= len(pairs)}
    verdicts = {n: yes(found[n]["complete"]) and yes(found[n]["responds"]) for n in found}
    failed = {f"turn {n}": ("cut: " if not yes(found[n]["complete"]) else "off: ") + endings[n - 1]
              for n in found if not verdicts[n]}
    # An answer left open stopped mid-way, whatever the judge reads.
    failed.update({f"turn {n}": f"cut: {endings[n - 1]}" for n, (_, answer) in enumerate(pairs, 1)
                   if answer and left_open(answer)})
    # A turn with no answer at all fails as one: nothing to read.
    failed.update({f"turn {n}": "no answer" for n, (_, answer) in enumerate(pairs, 1) if not answer})
    missing = [f"turn {n}" for n in range(1, len(pairs) + 1) if n not in verdicts]
    return {"turns": verdicts, "failed": failed, "missing": missing, **unread(reply, missing),
            "passed": not missing and not failed}


PAIRS = ("（）", "「」", "『』", "()", "【】", "[]")


def left_open(answer: str) -> bool:
    """The answer stops inside a code block, or its last paragraph inside a
    code span, a bracket or a quote: it was cut, whatever the judge reads (it
    passed "`discounted(100) → 85" as whole: v4c-tools, 2026-10-17). In the
    carried runs' 1,837 answers it finds 3, all cut (py-train-243's "`ma` 用「該").
    Not bold: a ** pair spans paragraphs in whole answers."""
    if answer.count("```") % 2:
        return True
    last = re.sub(r"```.*?```", "", answer, flags=re.DOTALL).rstrip().split("\n\n")[-1]
    if last.count("`") % 2:
        return True
    for opening, closing in PAIRS:
        depth = 0
        for char in last:
            depth += 1 if char == opening else -1 if char == closing and depth else 0
        if depth:
            return True
    return False


def figures(sentence: str) -> list[str]:
    return [f for f in FIGURE.findall(CITATION.sub("", sentence)) if len(f) >= 2]


def claims(trajectory: list[dict]) -> list[str]:
    """The sentences of each turn's last answer that state a number."""
    return [s for _, answer in turns(trajectory) for s in map(str.strip, SENTENCE.split(answer)) if figures(s)]


def close(figure: str, numbers: set[str]) -> list[str]:
    """The record's numbers written like `figure` but for one digit."""
    figure = plain(figure)
    return sorted(n for n in numbers if len(n) == len(figure) and sum(a != b for a, b in zip(n, figure)) == 1
                  and all(a == b or a.isdigit() and b.isdigit() for a, b in zip(n, figure)))[:4]


def numbered(port: int, trajectory: list[dict]) -> dict:
    """The number check: every number a turn's last answer states that the
    record does not hold must be the same number written another way, a sum
    or the assistant's own choice, not a mistake; each one answered."""
    numbers, said = given(trajectory), claims(trajectory)
    found = [(i, s, unheld) for i, s in enumerate(said) if (unheld := [f for f in figures(s) if not taken(f, numbers)])]
    if not found:
        return {"claims": 0, "failed": {}, "missing": [], "passed": True}
    listed = "\n".join(f"{n}. {s}\n   找不到：" + "、".join(
        f + (f"（紀錄裡只差一位的：{'、'.join(near)}）" if (near := close(f, numbers)) else "") for f in unheld)
        for n, (_, s, unheld) in enumerate(found, 1))
    asked = sum(len(unheld) for _, _, unheld in found)
    # Room for every number's source (a date sum's steps run long): 31 numbers cut a 4,000-token reply.
    reply = ask(port, NUMBERS_PROMPT.format(transcript=transcript(trajectory), claims=listed), max(4000, 200 * asked))
    # The judge may write a number its own way (40% for 40) or two as one (10/12 21:00): each number in it.
    kinds = {(str(g.get("id")), plain(figure)): g for g in objects(reply, "figures", ("id", "n", "kind", "source"))
             for figure in FIGURE.findall(str(g.get("n", "")))}
    failed, missing = {}, []
    for n, (i, s, unheld) in enumerate(found, 1):
        # The answer's own numbers so far: each one it did not take whole is checked itself.
        held = {plain(f) for c in said[:i + 1] for f in figures(c)}
        for f in unheld:
            if impossible(f):
                failed[f"claim {n} {f}"] = f"{s[:40]} / no such date"
                continue
            kind = kinds.get((str(n), plain(f)))
            source = str((kind or {}).get("source", ""))
            # A sum from a date the record never gave is no sum ("2023-10-26
            # + 1 day" for a 10/24 the record gave as 10/24). Only the date it
            # starts from: those after it are its steps (2026-10-13 + 2 days =
            # 10/15, 10/16), a difference (2800-2450)/2450 is no date, and a
            # year the judge put to a day the record gave (2023-10-24 for 10/24) is the record's.
            start = next((d for d in FIGURE.findall(source) if dated(d)), None)
            made_up = start is not None and not taken(start, numbers) and plain(start) not in held and not (
                plain(start).count("-") == 2 and taken(plain(start).split("-", 1)[1], numbers))
            if kind is None:
                missing.append(f"claim {n} {f}")
            elif kind.get("kind") == "不符" or kind.get("kind") == "推算" and made_up:
                failed[f"claim {n} {f}"] = f"{s[:40]} / {kind.get('kind')}: {source[:60]}"
    return {"claims": len(found), "failed": failed, "missing": missing, **unread(reply, missing),
            "passed": not failed and not missing}


def checks(task: dict, record: dict) -> dict:
    """A scenario family's programmatic checks that failed; any other task
    comes here only after passing its own check (records)."""
    if "scenario" not in task:
        return {}
    return failures(record, family_checks(task["family"]), web=bool(task.get("web")))


def verdict(port: int | None, task: dict, record: dict) -> dict:
    """The programmatic checks, then (with a judge server) the answer check,
    the number check and the items twice; each pass only runs if the one
    before passed.
    Without a server (--checks-only: the judge failed its own validation) a
    conversation passes on the checks alone."""
    found = checks(task, record)
    if found:
        return {"passed": False, "checks": found}
    if port is None:
        return {"passed": True, "checks": {}, "judge": "checks-only"}
    passes = [answered(port, record["trajectory"])]
    if passes[-1]["passed"]:
        passes.append(numbered(port, record["trajectory"]))
    for seed in (1, 2):  # the second pass only confirms a first pass
        if not passes[-1]["passed"]:
            break
        passes.append(rule(port, task, record["trajectory"], seed))
    return {"passed": all(p["passed"] for p in passes), "checks": {}, "judge": passes}


def records(run_dir: Path, task_root: Path) -> list[tuple[str, dict, dict]]:
    """(job, task, record) for every scenario job of the run, and every other
    job that passed its own check (one that failed never becomes a row)."""
    found = []
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        result = json.loads(line)
        task_file = task_root / result["task"] / "task.json"
        # A crash is the sandbox failing to report (run.py reruns it), not a conversation.
        if result.get("outcome") == "crash" or not task_file.is_file():
            continue
        if result.get("kind") != "scenario" and not run.clean(result):
            continue
        job = f"{result['task']}-r{result['rep']}"
        trajectory_file = run_dir / "traj" / f"{job}.json"
        trajectory = json.loads(trajectory_file.read_text(encoding="utf-8")) if trajectory_file.is_file() else []
        record = {"dialogue": result.get("dialogue") or [], "ended": result.get("ended"),
                  "trajectory": trajectory, "outcome": result.get("outcome")}
        found.append((job, json.loads(task_file.read_text(encoding="utf-8")), record))
    return found


def judge(args: argparse.Namespace, server: run.Server | None) -> None:
    out = args.run / "verdicts.jsonl"
    done = {json.loads(line)["job"] for line in out.read_text(encoding="utf-8").splitlines()} if out.is_file() else set()
    lock = threading.Lock()

    def one(item: tuple[str, dict, dict]) -> None:
        job, task, record = item
        found = verdict(server.port if server is not None else None, task, record)
        with lock, out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"job": job, "family": task.get("family") or task.get("kind", ""), **found},
                                    ensure_ascii=False) + "\n")
        print(f"{job}: {'PASS' if found['passed'] else 'fail'} {list(found['checks'])}", flush=True)

    todo = [item for item in records(args.run, args.task_root) if item[0] not in done]
    with ThreadPoolExecutor(server.parallel if server is not None else 1) as pool:
        list(pool.map(one, todo))


def texts(message: dict) -> list[str]:
    """The answer's text parts (what the transcript shows the judge)."""
    return [x["text"] for x in message.get("content") or []
            if isinstance(x, dict) and x.get("type") == "text" and x.get("text", "").strip()]


def answers(trajectory: list[dict]) -> list[int]:
    return [i for i, m in enumerate(trajectory) if m.get("role") == "assistant" and texts(m)]


SENTENCE_END = re.compile(r"[。！？!?\n]")


def cut_short(text: str) -> str:
    """The answer stopped in the middle of a sentence: the first sentence of 12
    characters or more from 40% of the answer on (else its longest), cut in
    half (cut at 40% alone, an answer could stop right where a sentence ends
    and read as complete)."""
    spans, start = [], 0
    for end in [m.start() for m in SENTENCE_END.finditer(text)] + [len(text)]:
        if end > start:
            spans.append((start, end))
        start = end + 1
    at = len(text) * 2 // 5
    start, end = next(((a, b) for a, b in spans if b > at and b - a >= 12), max(spans, key=lambda s: s[1] - s[0]))
    return text[:start + (end - start) // 2].rstrip("，、：；,;: ")


def given(trajectory: list[dict]) -> set[str]:
    """Every number the judge is shown outside the assistant's answers, as
    written (a whole date, version, price): the user's messages, the tool
    calls and results and the date the assistant was told."""
    silent = [{**m, "content": [x for x in m.get("content") or [] if not (isinstance(x, dict) and x.get("type") == "text")]}
              if m.get("role") == "assistant" else m for m in trajectory]
    return {plain(f) for f in FIGURE.findall(transcript(silent))}


def dated(figure: str) -> bool:
    """A date (2026-10-13, 10/13) or a time (12:00), not a plain number or a
    difference written with a dash (2800-2450, 150-130)."""
    figure = plain(figure)
    if ":" in figure:
        hour, _, minute = figure.partition(":")
        return hour.isdigit() and minute.isdigit() and int(hour) < 24 and int(minute) < 60
    parts = figure.split("-")
    if not all(part.isdigit() for part in parts) or len(parts) not in (2, 3) or len(parts) == 3 and len(parts[0]) != 4:
        return False
    month, day = map(int, parts[-2:])
    return 1 <= month <= 12 and 1 <= day <= 31


def impossible(figure: str) -> bool:
    """A whole date no calendar has (2026-07-00, 2026-02-30), wrong whatever
    the judge reads into it: it took --start 2026-07-00 in a command for the
    assistant's own example (v4-validate-scen, 2026-10-17)."""
    parts = plain(figure).split("-")
    # Shaped as a date: not a phone number (0800-789-889) or a list (8080/3128/1080).
    if len(parts) != 3 or not all(part.isdigit() for part in parts) or [len(part) for part in parts] not in (
            [4, 2, 2], [4, 1, 2], [4, 2, 1], [4, 1, 1]):
        return False
    try:
        date(*map(int, parts))
    except ValueError:
        return True
    return False


def plain(figure: str) -> str:
    """A number as compared: no thousands commas, a date's / or ‑ as -, a
    decimal from its point (a batting average's .381) with its 0."""
    figure = figure.replace(",", "").replace("\u2011", "-").replace("/", "-")
    return "0" + figure if figure.startswith(".") else figure


def taken(figure: str, numbers: set[str]) -> bool:
    """`figure` is one the record gave, whole: the same number as written, or
    a date's month and day without its year (10-15 of 2026-10-15). Its digits
    inside another number (the 10 of 2026-10-12 for a 10-bit video) are not."""
    figure = plain(figure)
    return figure in numbers or (not figure.isdigit() and any(
        n.endswith(sep + figure) for n in numbers for sep in "-."))


def misstate(text: str, numbers: set[str]) -> str | None:
    """`text` with its first number that the record gave, written so that it
    could only come from there (three characters or more: a price, a date, a
    version, a decimal), changed to one the record never gave; None when it
    states none. The first is the answer's own fact; a last one is often an
    aside that stays true changed ("no patch 3.2.2 or newer found" as 3.2.3, a
    threshold "below 3.99" as 3.90), and a two-digit number matches a long
    record by chance (a general 50 km/h: v4c-scen, 2026-10-17). A citation
    mark [n] is the host's to check."""
    for match in FIGURE.finditer(text):
        figure = match.group(0)
        cited = text[match.start() - 1:match.start()] == "[" and text[match.end():match.end() + 1] == "]"
        if len(plain(figure)) < 3 or cited or not taken(figure, numbers):
            continue
        for step in range(1, 10):
            changed = figure[:-1] + str((int(figure[-1]) + step) % 10)
            if not taken(changed, numbers):
                return text[:match.start()] + changed + text[match.end():]
    return None


def corrupt(trajectory: list[dict], other: list[dict], how: str) -> list[dict] | None:
    """A copy whose answers fail the judge for sure: the last turn's answer
    stops mid-sentence (cut), is another task's last answer (swap), every
    answer is another task's (swap_all), or the last answer misstates a
    number the transcript gave (number; None when it states none)."""
    def replaced(index: int, text: str) -> dict:
        message = trajectory[index]
        kept = [x for x in message["content"] if not (isinstance(x, dict) and x.get("type") == "text")]
        return {**message, "content": [*kept, {"type": "text", "text": text}]}

    mine, theirs = answers(trajectory), ["\n".join(texts(other[i])) for i in answers(other)]
    last = "\n".join(texts(trajectory[mine[-1]])).strip()
    out = list(trajectory)
    if how == "cut":
        out[mine[-1]] = replaced(mine[-1], cut_short(last))
    elif how == "swap":
        out[mine[-1]] = replaced(mine[-1], theirs[-1])
    elif how == "number":
        wrong = misstate(last, given(trajectory))
        if wrong is None:
            return None
        out[mine[-1]] = replaced(mine[-1], wrong)
    else:
        for n, index in enumerate(mine):
            out[index] = replaced(index, theirs[n % len(theirs)])
    return out


def spread(found: list, limit: int) -> list:
    """`limit` of `found`, evenly over it. A run's results come task by task:
    the first 40 of v4c-tools were all archive and backup tasks (2026-10-17),
    whose answers list files (a list cut short still reads as one) and look
    alike (another archive task's answer barely differs from its own)."""
    return found if len(found) <= limit else [found[i * len(found) // limit] for i in range(limit)]


def validate(args: argparse.Namespace, server: run.Server) -> None:
    found = spread([(job, task, record) for job, task, record in records(args.run, args.task_root)
                    if answers(record["trajectory"]) and not checks(task, record)], args.limit)

    def one(index: int) -> dict:
        job, task, record = found[index]
        original = verdict(server.port, task, record)
        # Only an accepted original tells anything: rejecting the copies of a
        # rejected one proves nothing (a judge rejecting all would pass).
        if not original["passed"]:
            last = original.get("judge", [{}])[-1]
            return {"job": job, "accepted": False, "pass": len(original.get("judge", [])),
                    **{k: last[k] for k in ("failed", "missing", "reply") if last.get(k)}}
        other = found[(index + 1) % len(found)][2]["trajectory"]
        copies = {how: corrupt(record["trajectory"], other, how) for how in CORRUPTIONS}
        tried = [how for how, copy in copies.items() if copy is not None]
        slipped = [how for how in tried if verdict(server.port, task, {**record, "trajectory": copies[how]})["passed"]]
        return {"job": job, "accepted": True, "tried": tried, "slipped": slipped}

    details, accepted, trials, rejected = [], 0, 0, 0
    with ThreadPoolExecutor(server.parallel) as pool:
        for detail in pool.map(one, range(len(found))):
            details.append(detail)
            accepted += detail["accepted"]
            trials += len(detail.get("tried", []))
            rejected += len(detail.get("tried", [])) - len(detail.get("slipped", []))
            print(f"{detail['job']}: originals accepted {accepted}/{len(details)}, "
                  f"corrupted rejected {rejected}/{trials}", flush=True)
    tried = {how: sum(how in d.get("tried", []) for d in details) for how in CORRUPTIONS}
    slipped = {how: sum(how in d.get("slipped", []) for d in details) for how in CORRUPTIONS}
    # Each kind on its own: a judge that tells a cut answer every time must
    # not pass for one that tells a wrong number some of the time. A kind
    # tried fewer than MIN_ACCEPTED times cannot show it either.
    weak = [how for how in CORRUPTIONS
            if tried[how] < MIN_ACCEPTED or tried[how] - slipped[how] < REJECT_NEEDED * tried[how]]
    summary = {"conversations": len(found), "originals_accepted": accepted, "corrupted": trials,
               "corrupted_rejected": rejected, "tried": tried, "slipped": slipped,
               "trusted": accepted >= MIN_ACCEPTED and not weak}
    (args.run / "judge-validation.json").write_text(json.dumps({**summary, "details": details}, ensure_ascii=False),
                                                    encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    if accepted < MIN_ACCEPTED:
        raise SystemExit(f"the judge accepted only {accepted} originals (needs {MIN_ACCEPTED} to be tested)")
    if weak:
        raise SystemExit("the judge did not prove itself on " + ", ".join(
            f"{how} ({tried[how] - slipped[how]}/{tried[how]} rejected)" for how in weak)
            + f" (each needs {MIN_ACCEPTED} tries and {REJECT_NEEDED:.0%} rejected)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("judge", "validate"))
    parser.add_argument("run", type=Path)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--config", required=True, help="the judge's bench config (configs.json)")
    parser.add_argument("--port", type=int, default=18489)
    parser.add_argument("--limit", type=int, default=30, help="validate: conversations to use")
    parser.add_argument("--parallel", type=int, default=3,
                        help="conversations judged side by side (fewer if the shared GPU holds fewer)")
    parser.add_argument("--checks-only", action="store_true",
                        help="judge: no judge model (it failed validation); the checks decide")
    args = parser.parse_args()
    if args.checks_only:
        judge(args, None)
        return
    config = json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    (args.run / "judge").mkdir(exist_ok=True)
    server = run.Server(config, args.run / "judge", args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)
    try:
        {"judge": judge, "validate": validate}[args.command](args, server)
    finally:
        server.close()


if __name__ == "__main__":
    main()
