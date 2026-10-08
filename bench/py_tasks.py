"""Python coding tasks with hidden tests, written by the teacher, kept only if they hold.

The owner's main need beyond chat is code, Python first (2026-10-08). Each
task is a small project, a request the way the owner would type it, hidden
pytest tests and a reference solution, in the bench's task format (task.json,
workspace/, solution/, hidden/bench_hidden/). A task is kept only if the
bench's selftest holds in the sandbox: the untouched project fails the hidden
tests and the reference solution passes them. Topics follow what the owner
works on; eval tasks use topics no training task uses. The bank belongs to no
harness: anything that can run pytest on a folder can use it.

    python bench/py_tasks.py generate --config teacher-38-iq3 --split train --count 240
    python bench/py_tasks.py generate --config teacher-38-iq3 --split eval --count 60 --seed 1
    python bench/py_tasks.py validate --split train
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402  (the one way to start a model server, and the selftest)
import scenario_cards as cards  # noqa: E402  (ask, traditional, inside)

OUT = run.ROOT / ".runtime/agentdata/py-tasks"
KINDS = {"fix": 0.4, "feature": 0.35, "script": 0.25}
KIND_TEXT = {
    "fix": "修 bug：專案裡有一個真的會出錯的 bug，使用者貼出錯誤訊息或描述看到的錯誤結果，要助理修好",
    "feature": "加功能：在現有的小專案裡加一個功能或選項，使用者描述要的行為",
    "script": "從零寫：使用者要一個新的小工具或腳本（可以附上要處理的資料檔），描述輸入和要的輸出",
}
TOPICS = [  # every 4th is held out for eval (topics_for)
    "遊戲配裝與傷害計算", "遊戲掉落率與抽卡機率統計", "遊戲存檔或設定檔解析", "電競戰績與排行榜",
    "股價 CSV 的移動平均與報酬率", "投資組合配置與再平衡", "匯率與幣別換算", "財報數字整理與比較",
    "動畫或影集追番清單", "音樂播放清單管理", "電影評分統計", "串流平台訂閱費用比較",
    "記帳與預算", "檔案批次整理與改名", "文字與字幕檔處理", "日期與時區計算",
    "命令列參數與設定檔", "重試與錯誤處理", "資料清理與去重", "正規表示式抽取資料",
]

TASK_PROMPT = """你要為一個寫 Python 的 AI 助理出一道練習題，之後會用隱藏的測試自動驗收它寫的程式。

題型：{kind_text}
主題：{topic}
使用者：台灣的玩家兼工程師，講話直接、常常很簡短，寫 Python 為主。

照下面的格式輸出，每一段用標題行開頭，最後一行是「=== 結束 ===」，不要加其他說明：
=== 標題 ===
一句話的題目名稱
=== 需求 ===
使用者對助理說的話（台灣繁體中文，像真人在聊天室打字；修 bug 的題目要附上錯誤訊息或錯誤的輸出）
=== 檔案：相對路徑 ===
（一開始就有的專案檔案，每個檔案一段；1 到 5 個檔案，每個最多 150 行；只用 Python 標準庫；資料檔也可以）
=== 測試：test_名稱.py ===
（隱藏的 pytest 測試，1 個檔案，最多 80 行：只驗收需求要的行為，結果要確定、不連網、不靠時間或亂數；從工作資料夾 import 模組，或用 subprocess 執行腳本）
=== 答案：相對路徑 ===
（正確做完後的檔案完整內容，每個改過或新增的檔案一段）
=== 結束 ===

要求：
- 需求要具體，但不要把答案講出來；測試只能檢查需求裡講到的事，不要檢查需求沒說的細節（函式名、輸出格式除外，這些要在需求或現有程式裡看得到）。
- 沒做之前測試一定要失敗，照答案做完一定要通過。
- 程式碼裡的註解和字串可以用中文；虛構的人名、公司、數字。"""

SECTION = re.compile(r"^=== (標題|需求|檔案：(.+?)|測試：(.+?)|答案：(.+?)|結束) ===[ \t]*$", re.MULTILINE)


def parse_task(text: str) -> dict | None:
    """The task in TASK_PROMPT's format, or None when a part is missing."""
    marks = list(SECTION.finditer(text))
    task: dict = {"files": {}, "tests": {}, "solution": {}}
    for mark, following in zip(marks, marks[1:] + [None], strict=True):
        body = text[mark.end():following.start() if following else len(text)].strip("\n")
        name = mark.group(1)
        if name == "標題":
            task["title"] = body.strip()
        elif name == "需求":
            task["request"] = body.strip()
        elif mark.group(2):
            task["files"][mark.group(2).strip()] = body + "\n"
        elif mark.group(3):
            task["tests"][mark.group(3).strip()] = body + "\n"
        elif mark.group(4):
            task["solution"][mark.group(4).strip()] = body + "\n"
    if not (task.get("title") and task.get("request") and len(task["tests"]) == 1 and task["solution"]
            and marks and marks[-1].group(1) == "結束"):
        return None
    return task


def problems(task: dict) -> list[str]:
    found = []
    paths = [*task["files"], *task["solution"]]
    if not all(cards.inside(p) and not p.startswith("bench_hidden") for p in paths):
        found.append("paths must stay in the project")
    if len(task["files"]) > 5 or any(text.count("\n") > 200 for text in [*task["files"].values(),
                                                                          *task["solution"].values()]):
        found.append("too big")
    test_name = next(iter(task["tests"]))
    if not re.fullmatch(r"test_\w+\.py", test_name):
        found.append("the test file must be test_*.py")
    if re.search(r"\b(requests|urllib\.request|socket|httpx)\b", next(iter(task["tests"].values()))):
        found.append("tests must not use the network")
    return found


def write_task(root: Path, task_id: str, task: dict) -> Path:
    task_dir = root / task_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    for folder, files in (("workspace", task["files"]), ("solution", task["solution"])):
        (task_dir / folder).mkdir(parents=True)
        for path, text in files.items():
            target = task_dir / folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    hidden = task_dir / "hidden" / "bench_hidden"
    hidden.mkdir(parents=True)
    for path, text in task["tests"].items():
        (hidden / path).write_text(text, encoding="utf-8")
    spec = {"kind": "code", "git": True,
            "may_change": sorted({*task["solution"], "tests/*", "test_*.py"}),
            "prompt": task["request"],
            "check": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "bench_hidden"],
            "source": {k: task[k] for k in ("title", "kind", "topic", "teacher", "prompt")}}
    (task_dir / "task.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return task_dir


def topics_for(split: str) -> list[str]:
    held_out = TOPICS[3::4]
    return held_out if split == "eval" else [t for t in TOPICS if t not in held_out]


def generate(args: argparse.Namespace) -> None:
    config = json.loads((run.BENCH / "configs.json").read_text(encoding="utf-8"))[args.config]
    root = OUT / args.split
    root.mkdir(parents=True, exist_ok=True)
    have = len([d for d in root.iterdir() if (d / "task.json").is_file()])
    rejected = OUT / f"{args.split}-rejected.jsonl"
    stamp = hashlib.sha256(TASK_PROMPT.encode()).hexdigest()[:12]
    topics = topics_for(args.split)
    lock = threading.Lock()
    counter = {"have": have, "started": 0}
    (OUT / f"server-{args.split}").mkdir(parents=True, exist_ok=True)
    server = run.Server(config, OUT / f"server-{args.split}", args.parallel, args.port, fit_slots=True)
    run.close_on_term(server)

    def worker(n: int) -> None:
        rng = random.Random(f"{args.split}-{args.seed}-{n}")
        while True:
            with lock:
                if counter["have"] >= args.count or counter["started"] >= args.count * 3:
                    return
                counter["started"] += 1
            kind = rng.choices(list(KINDS), weights=list(KINDS.values()))[0]
            topic = rng.choice(topics)
            text = cards.ask(server.port, TASK_PROMPT.format(kind_text=KIND_TEXT[kind], topic=topic),
                             max_tokens=12000)
            task = parse_task(text)
            why = ["unreadable"] if task is None else problems(task)
            with lock:
                if why:
                    with rejected.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"why": why, "kind": kind, "topic": topic}, ensure_ascii=False) + "\n")
                    print(f"{kind} {topic}: dropped ({'; '.join(why)})", flush=True)
                    continue
                counter["have"] += 1
                task_id = f"py-{args.split}-{counter['have']:03d}"
                task.update(kind=kind, topic=topic, teacher=config["model"], prompt=stamp,
                            request=cards.traditional(task["request"]))
                write_task(root, task_id, task)
                print(f"{task_id} {kind} {topic}: {task['title']}", flush=True)

    try:
        with ThreadPoolExecutor(server.parallel) as pool:
            list(pool.map(worker, range(server.parallel)))
    finally:
        server.close()


def validate(args: argparse.Namespace) -> None:
    """The bench's selftest per task in the sandbox; a task that does not hold moves to <split>-rejected/."""
    root = OUT / args.split
    run.TASK_ROOT = root
    run.STATE.mkdir(parents=True, exist_ok=True)
    rejected = OUT / f"{args.split}-rejected"
    tasks = sorted(d.name for d in root.iterdir() if (d / "task.json").is_file())
    marker = root / ".validated"
    done = set(marker.read_text().split()) if marker.is_file() else set()
    lock = threading.Lock()

    def one(name: str) -> None:
        try:
            run.selftest(name)
            ok = True
        except SystemExit:
            ok = False
        with lock:
            if not ok:
                rejected.mkdir(exist_ok=True)
                shutil.move(str(root / name), str(rejected / name))
            else:
                with marker.open("a") as handle:
                    handle.write(name + "\n")

    with ThreadPoolExecutor(args.parallel) as pool:
        list(pool.map(one, [t for t in tasks if t not in done]))
    kept = len([d for d in root.iterdir() if (d / "task.json").is_file()])
    print(f"{kept} tasks hold in {root}; rejected in {rejected}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--config", required=True)
    g.add_argument("--split", choices=("train", "eval"), required=True)
    g.add_argument("--count", type=int, required=True)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--port", type=int, default=18390)
    g.add_argument("--parallel", type=int, default=4)
    v = sub.add_parser("validate")
    v.add_argument("--split", choices=("train", "eval"), required=True)
    v.add_argument("--parallel", type=int, default=4)
    args = parser.parse_args()
    {"generate": generate, "validate": validate}[args.command](args)


if __name__ == "__main__":
    main()
