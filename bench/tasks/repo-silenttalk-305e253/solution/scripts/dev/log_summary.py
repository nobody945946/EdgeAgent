"""Numbers from silenttalk.log for a real Discord test, as a Markdown table.

    .venv\\Scripts\\python.exe scripts\\dev\\log_summary.py            (Windows)
    .venv/bin/python scripts/dev/log_summary.py [--log PATH] [--all]

Only counts, timings and event names are printed, never message text, so
the output can be pasted or committed as it is. By default it covers the
last app session (from the last copilot_runtime_ready on).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
FIELD = re.compile(r"(\w+)=(\S+)")
EVENT_NAME = re.compile(r"[a-z][a-z0-9_]*")


def _exception_type(record: dict) -> str:
    """The exception class of a traceback ("aiohttp.ClientConnectorError"),
    never its message: that is what tells a network drop from a bad token."""
    trace = str(record.get("exception") or "").strip().splitlines()
    if not trace:
        return ""
    name = trace[-1].split(":", 1)[0].strip()
    return f"（{name}）" if re.fullmatch(r"[\w.]+", name) else ""


def _problem_name(record: dict) -> str:
    """Our own events start with a snake_case name. A library's message does
    not ("1 packets were lost being flushed in decoder-5"), so show which
    logger it came from and its first words with numbers masked."""
    text = str(record.get("event", ""))
    name = text.split(" ", 1)[0]
    if EVENT_NAME.fullmatch(name):
        return name
    shape = re.sub(r"\d+", "N", " ".join(text.split()[:7]))[:60]
    return f"{record.get('logger', '?')}：{shape}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, default=PROJECT_DIR / "silenttalk.log")
    parser.add_argument("--all", action="store_true", help="every session in the file")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if not args.log.is_file():
        print(f"找不到 {args.log}")
        return 1

    records = []
    for line in args.log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not args.all:
        # The last app run or --stt-check run, whichever came last.
        starts = [
            i
            for i, r in enumerate(records)
            if r.get("event", "").startswith(
                ("copilot_runtime_ready", "copilot_stt_check_start")
            )
        ]
        if starts:
            records = records[max(0, starts[-1] - 50) :]

    events: dict[str, list[dict[str, str]]] = {}
    problems: Counter[str] = Counter()
    for record in records:
        text = str(record.get("event", ""))
        name = text.split(" ", 1)[0]
        events.setdefault(name, []).append(dict(FIELD.findall(text)))
        if record.get("level") in ("WARNING", "ERROR", "CRITICAL"):
            problems[
                f"{record['level']} {_problem_name(record)}{_exception_type(record)}"
            ] += 1

    def ms(rows: list[dict[str, str]], key: str) -> str:
        values = [
            int(r[key])
            for r in rows
            if r.get(key, "").lstrip("-").isdigit() and int(r[key]) >= 0
        ]
        if not values:
            return "—"
        values.sort()
        p90 = values[min(len(values) - 1, int(len(values) * 0.9))]
        return f"中位數 {statistics.median(values):.0f} ms、p90 {p90} ms（n={len(values)}）"

    lines = events.get("copilot_character_line", [])
    spoke = [r for r in lines if r.get("spoke") == "True"]
    quiet = [r for r in lines if r.get("spoke") == "False"]
    jobs = events.get("job_done", [])
    streamed = [r for r in jobs if r.get("streamed") == "True"]
    devices = [
        r for r in records if str(r.get("event", "")).startswith("copilot_llm_devices")
    ]
    receive = events.get("voice_receive_started", [])
    segments = events.get("copilot_stt_segment", [])
    received = [
        str(r.get("event", ""))
        for r in records
        if str(r.get("event", "")).startswith("voice_receive_stats")
    ]
    checks = events.get("copilot_stt_check", [])
    check_done = events.get("copilot_stt_check_done", [])

    def median_of(rows: list[dict[str, str]], key: str, fmt: str) -> str:
        values = []
        for r in rows:
            try:
                values.append(float(r[key]))
            except (KeyError, ValueError):
                pass
        return format(statistics.median(values), fmt) if values else "—"

    def receive_summary() -> str:
        if not received:
            return "—"
        top = []
        totals = Counter()
        for text in received:
            rates = re.search(r"fps=\[([^\]]*)\]", text)
            if rates and rates.group(1).split():
                top.append(float(rates.group(1).split()[0]))
            for key in (
                "odd_frames",
                "dropped_no_session",
                "decode_errors",
                "padded",
                "dave_dropped",
                "unknown_ssrc",
                "recoveries",
            ):
                found = re.search(rf"{key}=(\d+)", text)
                totals[key] += int(found.group(1)) if found else 0
        dave = re.search(r"dave=(\S+)(.*)$", received[-1])
        return (
            f"{len(received)} 次；說最多的人每秒幀數中位數 "
            f"{statistics.median(top):.1f}（50＝全收到）"
            if top
            else f"{len(received)} 次；沒有收到任何幀"
        ) + (
            f"；大小不對 {totals['odd_frames']}、沒 DAVE session 丟掉 {totals['dropped_no_session']}"
            f"、解碼錯誤 {totals['decode_errors']}、有 padding {totals['padded']}"
            f"、DAVE 解不開丟掉 {totals['dave_dropped']}、SSRC 未對應丟掉 {totals['unknown_ssrc']}"
            f"、DAVE 復原 {totals['recoveries']}；最後一次 DAVE：{dave.group(1)}{dave.group(2)}"
            if dave
            else ""
        )

    rows = [
        (
            "GPU（llama.cpp 看到的）",
            str(devices[-1]["event"]).split(" ", 1)[-1] if devices else "—",
        ),
        (
            "連上 Discord（ready／resumed）",
            (
                f"{len(events.get('discord_ready', []))}"
                f"／{len(events.get('discord_gateway_resumed', []))}"
            ),
        ),
        (
            "語音接收",
            f"dave_version={receive[-1].get('dave_version')}"
            if receive
            else "沒有啟動",
        ),
        ("收音（每 30 秒記一次）", receive_summary()),
        ("STT 斷出的句子", str(len(events.get("copilot_turn_final", [])))),
        (
            "STT 片段音量／頻譜平坦度（人聲 < 0.3，雜訊約 0.56）",
            f"中位數 {median_of(segments, 'level_dbfs', '.1f')} dBFS／"
            f"{median_of(segments, 'flatness', '.2f')}；像雜訊（> 0.4）"
            f"{sum(float(r.get('flatness', 0)) > 0.4 for r in segments)}/{len(segments)} 段",
        ),
        (
            "真人辨識測試（--stt-check）",
            (
                f"經過 Discord 平均錯字率 {float(check_done[-1]['cer']):.1%}（{check_done[-1]['sentences']} 句，"
                f"{'PASS' if check_done[-1].get('passed') == '1' else 'FAIL'}）；"
                + (
                    f"本機麥克風直接收 {float(check_done[-1]['local_cer']):.1%}；"
                    if float(check_done[-1].get("local_cer", -1)) >= 0
                    else "本機麥克風沒量；"
                )
                + f"有結果 {sum(int(r.get('heard_chars', 0)) > 0 for r in checks)}/{len(checks)} 句"
            )
            if check_done
            else "沒有跑",
        ),
        (
            "STT 聲音片段長度（SenseVoice）",
            ms(events.get("copilot_stt_segment", []), "audio_ms"),
        ),
        (
            "STT 辨識一段花的時間",
            ms(events.get("copilot_stt_segment", []), "decode_ms"),
        ),
        (
            "STT 聽到聲音但沒辨識出字（SenseVoice）",
            f"{len(events.get('copilot_stt_empty', []))}"
            + (
                f"（{ms(events.get('copilot_stt_empty', []), 'audio_ms')}）"
                if events.get("copilot_stt_empty")
                else ""
            ),
        ),
        ("打字訊息", str(len(events.get("text_message_received", [])))),
        ("角色判斷次數", f"{len(lines)}（開口 {len(spoke)}、沒開口 {len(quiet)}）"),
        ("聽到 → 角色的話備好（開口時）", ms(spoke, "since_heard_ms")),
        ("聽到 → 決定不開口", ms(quiet, "since_heard_ms")),
        ("模型本身（開口時）", ms(spoke, "latency_ms")),
        (
            "因為還在說話而丟掉的句子",
            str(len(events.get("character_line_dropped", []))),
        ),
        ("說出口的句子", f"{len(jobs)}（串流 {len(streamed)}）"),
        ("TTS 到第一段聲音（串流）", ms(streamed, "synthesis_ms")),
    ]
    print("| 項目 | 數字 |")
    print("|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")
    print()
    print("警告／錯誤（只列事件名稱和次數）：")
    for name, count in problems.most_common() or [("沒有", 0)]:
        print(f"- {name}" + (f" × {count}" if count else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
