"""edge_agent.toolbox.text: text encodings, merging CSV files, summarising logs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from edge_agent.toolbox import ToolError, text
from tests.toolbox_helpers import preview, run, snapshot, write

# ---------------------------------------------------------------- convert_text_encoding


def test_convert_big5_round_trip_and_leave_utf8_alone(tmp_path: Path) -> None:
    content = "第一章：台灣的天氣，今天很好。\r\n第二行"
    big5 = write(tmp_path / "t" / "舊.txt", content.encode("cp950"))
    utf8 = write(tmp_path / "t" / "新.txt", "已經是 UTF-8".encode())
    os.utime(utf8, (1_600_000_000, 1_600_000_000))
    binary = write(tmp_path / "t" / "bin.txt", b"\x00\x81\xff\xfe\x00")
    write(tmp_path / "t" / "settings.json", "{}".encode("cp950"))
    before = snapshot(tmp_path)
    plan = preview("convert_text_encoding", tmp_path, path="t", pattern="*.txt;*.json")
    assert snapshot(tmp_path) == before
    assert "Big5" in plan and "第一章：台灣的天氣" in plan
    out = run("convert_text_encoding", tmp_path, path="t", pattern="*.txt;*.json")
    assert big5.read_bytes() == content.encode("utf-8")  # same text, line endings kept
    assert utf8.read_bytes() == "已經是 UTF-8".encode() and utf8.stat().st_mtime == 1_600_000_000
    assert binary.read_bytes() == b"\x00\x81\xff\xfe\x00"
    assert "已經是 UTF-8" in out and "認不出編碼" in out and "使用者自己的設定檔" in out
    assert "轉換完成 1 個" in out
    assert sorted(x.name for x in (tmp_path / "t").iterdir()) == ["bin.txt", "settings.json", "新.txt", "舊.txt"]


def test_convert_options(tmp_path: Path) -> None:
    f = write(tmp_path / "a.csv", "名稱,數量".encode())
    run("convert_text_encoding", tmp_path, path="a.csv", add_bom=True)
    assert f.read_bytes() == b"\xef\xbb\xbf" + "名稱,數量".encode()
    out = run("convert_text_encoding", tmp_path, path="a.csv", to_encoding="big5")
    assert f.read_bytes() == "名稱,數量".encode("big5") and "Big5" in out
    g = write(tmp_path / "b.txt", "😀".encode())
    out = run("convert_text_encoding", tmp_path, path="b.txt", to_encoding="cp950")
    assert g.read_bytes() == "😀".encode() and "沒有的字" in out
    with pytest.raises(ToolError, match="不認得"):
        run("convert_text_encoding", tmp_path, path="b.txt", to_encoding="nope")
    assert text.detect("繁體".encode("cp950")) == "cp950" and text.detect(b"\xef\xbb\xbfok") == "utf-8-sig"


# ---------------------------------------------------------------- merge_csv


def test_merge_csv_union_of_headers(tmp_path: Path) -> None:
    write(tmp_path / "r" / "1.csv", "名稱,數量\n蘋果,3\n香蕉,5\n")
    write(tmp_path / "r" / "2.csv", "名稱,數量,備註\n橘子,2,\"好吃,甜\"\n".encode("cp950"))
    write(tmp_path / "r" / "3.csv", "數量;名稱\n7;梨\n")
    before = snapshot(tmp_path)
    plan = preview("merge_csv", tmp_path, files=["r/*.csv"], output="合併")
    assert snapshot(tmp_path) == before and "4 列" in plan
    out = run("merge_csv", tmp_path, files=["r/*.csv"], output="合併")
    raw = (tmp_path / "合併.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines() == ["名稱,數量,備註", "蘋果,3,", "香蕉,5,", "橘子,2,\"好吃,甜\"", "梨,7,"]
    assert "標題不完全一樣" in out and "沒有：備註" in out and "Big5" in out and "列數相符" in out
    with pytest.raises(ToolError, match="已經存在"):
        run("merge_csv", tmp_path, files=["r/1.csv"], output="合併.csv")
    write(tmp_path / "same" / "a.csv", "x,y\n1,2\n")
    write(tmp_path / "same" / "b.csv", "x,y\n3,4\n")
    out = run("merge_csv", tmp_path, files=["same"], output="s.csv")
    assert "標題都一樣" in out
    assert (tmp_path / "s.csv").read_text(encoding="utf-8-sig").splitlines() == ["x,y", "1,2", "3,4"]


# ---------------------------------------------------------------- log_summary


def test_log_summary_counts_and_groups(tmp_path: Path) -> None:
    write(tmp_path / "logs" / "app.log", (
        "2026-10-01 10:00:01 INFO start\n"
        "2026-10-01 10:00:02 ERROR Connection refused to 10.0.0.5:5432\n"
        "2026-10-01 10:00:03 [error] Connection refused to 10.0.0.6:5432\n"
        "2026-10-01 10:00:04 WARN slow request 1234ms\n"
        "2026-10-01 10:00:05 INFO retry after error\n"
        "2026-10-01 10:00:06 FATAL out of memory at 0x7fff1234\n"
        "Traceback (most recent call last):\n"
        "2026-10-01 10:00:07 DEBUG id=550e8400-e29b-41d4-a716-446655440000\n"
        "2026-10-01 10:00:08 ERROR Connection refused to 10.0.0.7:5432"))
    write(tmp_path / "logs" / "other.txt", "ERROR not a log by pattern")
    out = run("log_summary", tmp_path, path="logs")
    assert "共 9 行" in out
    assert "錯誤 4（其中 FATAL／CRITICAL 1）、警告 1、INFO 2、DEBUG 1、沒有等級 1" in out
    assert "3 次，第 2 行 到 第 9 行" in out and "Connection refused to <n>.<n>.<n>.<n>:<n>" in out
    assert "out of memory at <hex>" in out and "not a log" not in out
    assert "沒有符合" in run("log_summary", tmp_path, path="logs", pattern="*.nothing")
