"""edge_agent.toolbox: every tool's main path and the safety edge it must keep."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from edge_agent.toolbox import (
    IS_WINDOWS,
    REGISTRY,
    WINDOWS_ONLY,
    ToolError,
    check_params,
    clip,
    photos,
    resolve_inside,
    system,
    text,
)

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, root: Path, **params) -> str:
    return REGISTRY[name].run(params, root)


def preview(name: str, root: Path, **params) -> str:
    return REGISTRY[name].preview(params, root)


def snapshot(root: Path) -> dict[str, tuple[bytes, int] | None]:
    """Every file's bytes and modified time, and every folder, under root."""
    state: dict[str, tuple[bytes, int] | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        state[rel] = (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
    return state


def arrows(result: str) -> list[str]:
    """The per-item plan lines (`- a → b`) of a preview or a run."""
    return sorted(line for line in result.splitlines() if line.startswith("- ") and "→" in line)


def write(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def cli(*args: str, stdin: str = "", cwd: Path = ROOT) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-m", "edge_agent.toolbox", *args], input=stdin.encode(), cwd=cwd,
                          capture_output=True, timeout=120, check=False, env=env)
    return done.returncode, done.stdout.decode("utf-8")


# ---------------------------------------------------------------- interface


def test_describe_is_json_with_described_parameters() -> None:
    code, out = cli("describe")
    assert code == 0
    tools = json.loads(out)
    assert {t["name"] for t in tools} == set(REGISTRY) and len(tools) == 20
    for t in tools:
        assert set(t) == {"name", "summary", "keywords", "parameters", "kind", "destructive", "windows_only"}
        assert t["kind"] in ("read", "change", "run") and t["summary"] and t["keywords"]
        assert t["parameters"]["type"] == "object"
        for name, spec in t["parameters"]["properties"].items():
            assert spec.get("description"), (t["name"], name)
        assert set(t["parameters"].get("required", [])) <= set(t["parameters"]["properties"])
        if t["kind"] == "read":
            assert not t["destructive"] and REGISTRY[t["name"]].preview is None
        if t["kind"] == "change":
            assert REGISTRY[t["name"]].preview is not None
        assert any("\u4e00" <= ch <= "\u9fff" for ch in t["keywords"]), t["name"]  # Chinese search words too


def test_cli_exit_codes(tmp_path: Path) -> None:
    code, out = cli("run", "calculate", stdin='{"expression": "1/4 + 1/4"}')
    assert code == 0 and "結果：0.5（精確值）" in out
    write(tmp_path / "a.txt")
    code, out = cli("run", "find_large_files", stdin='{"folder": "."}', cwd=tmp_path)
    assert code == 0 and "a.txt" in out
    code, out = cli("run", "find_large_files", stdin='{"folder": ".."}', cwd=tmp_path)
    assert code == 2 and "工作區外面" in out
    code, out = cli("run", "no_such_tool", stdin="{}")
    assert code == 2 and "沒有叫做" in out
    code, out = cli("run", "calculate", stdin="{not json")
    assert code == 2 and "JSON" in out
    code, out = cli("preview", "calculate", stdin='{"expression": "1"}')
    assert code == 2 and "不需要預覽" in out
    code, out = cli("preview", "organize_folder", stdin='\ufeff{"folder": "."}', cwd=tmp_path)
    assert code == 0 and "預計" in out


def test_resolve_inside_refuses_outside_and_links_out(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    write(root / "in.txt")
    write(tmp_path / "secret.txt")
    assert resolve_inside(root, "in.txt") == (root / "in.txt").resolve()
    assert resolve_inside(root, str(root / "in.txt")) == (root / "in.txt").resolve()
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), "sub/../../secret.txt"):
        with pytest.raises(ToolError, match="工作區外面"):
            resolve_inside(root, bad, must_exist=False)
    try:
        (root / "link.txt").symlink_to(tmp_path / "secret.txt")
        (root / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    for bad in ("link.txt", "linkdir/secret.txt"):
        with pytest.raises(ToolError, match="工作區外面"):
            resolve_inside(root, bad)
    with pytest.raises(ToolError, match="找不到"):
        resolve_inside(root, "missing.txt")


def test_parameters_are_checked_and_coerced() -> None:
    schema = REGISTRY["find_large_files"].parameters
    assert check_params(schema, {"folder": "x", "top": "5", "min_size_mb": "1.5"}) == \
        {"folder": "x", "top": 5, "min_size_mb": 1.5}
    with pytest.raises(ToolError, match="不認得的參數：recursive"):
        check_params(schema, {"folder": "x", "recursive": True})
    with pytest.raises(ToolError, match="缺少參數 folder"):
        check_params(schema, {})
    with pytest.raises(ToolError, match="之間"):
        check_params(schema, {"folder": "x", "top": 0})
    organize = REGISTRY["organize_folder"].parameters
    assert check_params(organize, {"folder": "x", "by": "Month", "include_subfolders": "true"})["by"] == "month"
    with pytest.raises(ToolError, match="只能是"):
        check_params(organize, {"folder": "x", "by": "size"})
    assert check_params(REGISTRY["zip_files"].parameters, {"paths": "a.txt", "output": "o"})["paths"] == ["a.txt"]


def test_output_is_capped() -> None:
    long = "\n".join(f"第 {i} 行" for i in range(5000))
    out = clip(long)
    assert len(out) < 6100 and "後面省略了" in out


# ---------------------------------------------------------------- backup_folder


def test_backup_copies_and_verifies(tmp_path: Path) -> None:
    write(tmp_path / "報告" / "a.txt", "aaa")
    write(tmp_path / "報告" / "sub" / "b.txt", "bb")
    (tmp_path / "報告" / "empty").mkdir()
    before = snapshot(tmp_path)
    plan = preview("backup_folder", tmp_path, source="報告")
    assert "2 個檔案" in plan and snapshot(tmp_path) == before
    out = run("backup_folder", tmp_path, source="報告")
    assert out.startswith("備份完成") and "相符" in out
    copies = [p for p in tmp_path.iterdir() if p.name.startswith("報告-備份-")]
    assert len(copies) == 1
    assert (copies[0] / "sub" / "b.txt").read_text() == "bb" and (copies[0] / "empty").is_dir()
    assert snapshot(tmp_path / "報告") == {k[len("報告/"):]: v for k, v in before.items() if k.startswith("報告/")}


def test_backup_zip_into_existing_folder_and_refusals(tmp_path: Path) -> None:
    write(tmp_path / "data" / "a.txt", "hello")
    (tmp_path / "備份").mkdir()
    out = run("backup_folder", tmp_path, source="data", as_zip=True, destination="備份")
    assert "相符" in out
    [made] = list((tmp_path / "備份").iterdir())
    with zipfile.ZipFile(made) as zf:
        assert zf.read("data/a.txt") == b"hello"
    write(tmp_path / "taken.zip")
    with pytest.raises(ToolError, match="已經存在"):
        run("backup_folder", tmp_path, source="data", as_zip=True, destination="taken.zip")
    with pytest.raises(ToolError, match="工作區外面"):
        run("backup_folder", tmp_path, source="data", destination="../elsewhere")
    with pytest.raises(ToolError, match="destination"):
        run("backup_folder", tmp_path, source=".")
    assert not list(tmp_path.rglob("*.partial"))


# ---------------------------------------------------------------- organize_folder


def make_downloads(root: Path) -> Path:
    d = root / "下載"
    for name in ("a.jpg", "B.PDF", "c.docx", "d.xlsx", "e.zip", "f.exe", "g.py", "h.unknown", "song.mp3"):
        write(d / name, name)
    write(d / "圖片" / "a.jpg", "already there")
    write(d / "half.crdownload")
    write(d / ".hidden.jpg")
    write(d / "settings.json", "{}")
    write(d / ".env", "SECRET=1")
    write(d / "sub" / "deep.png", "deep")
    return d


def test_organize_by_type_matches_preview_and_keeps_protected(tmp_path: Path) -> None:
    d = make_downloads(tmp_path)
    before = snapshot(tmp_path)
    plan = preview("organize_folder", tmp_path, folder="下載")
    assert snapshot(tmp_path) == before
    out = run("organize_folder", tmp_path, folder="下載")
    assert arrows(plan) == arrows(out) and len(arrows(out)) == 9
    assert (d / "圖片" / "a (2).jpg").read_text() == "a.jpg"  # collision: never clobbered
    assert (d / "圖片" / "a.jpg").read_text() == "already there"
    for folder, name in (("PDF", "B.PDF"), ("文件", "c.docx"), ("試算表", "d.xlsx"), ("壓縮檔", "e.zip"),
                         ("安裝程式", "f.exe"), ("程式碼", "g.py"), ("其他", "h.unknown"), ("音樂", "song.mp3")):
        assert (d / folder / name).is_file(), name
    for kept in ("half.crdownload", ".hidden.jpg", "settings.json", ".env", "sub/deep.png"):
        assert (d / kept).is_file(), kept
    assert (d / ".env").read_text() == "SECRET=1"
    assert "使用者自己的設定檔" in out and "還沒下載完" in out


def test_organize_subfolders_and_month(tmp_path: Path) -> None:
    d = make_downloads(tmp_path)
    run("organize_folder", tmp_path, folder="下載", include_subfolders=True)
    assert (d / "圖片" / "deep.png").read_text() == "deep"
    assert (d / "圖片" / "a.jpg").read_text() == "already there"  # a category folder is left alone
    m = tmp_path / "m"
    stamp = time.mktime((2023, 7, 15, 12, 0, 0, 0, 0, -1))
    os.utime(write(m / "old.txt"), (stamp, stamp))
    out = run("organize_folder", tmp_path, folder="m", by="month")
    assert (m / "2023-07" / "old.txt").is_file() and "2023-07" in out
    assert "沒有需要整理" in run("organize_folder", tmp_path, folder="m", by="month")


def test_organize_does_not_follow_links(tmp_path: Path) -> None:
    outside = write(tmp_path / "outside" / "x.jpg")
    ws = tmp_path / "ws"
    write(ws / "a.jpg")
    try:
        (ws / "link.jpg").symlink_to(outside)
    except OSError:
        pytest.skip("cannot create symlinks here")
    out = run("organize_folder", ws, folder=".")
    assert (ws / "link.jpg").is_symlink() and outside.is_file() and "連結" in out


# ---------------------------------------------------------------- rename_photos_by_date


def tiff_block(order: bytes, original: str | None = None, modified: str | None = None) -> bytes:
    e = "<" if order == b"II" else ">"
    count0 = 1 + bool(modified)
    exif_at = 8 + 2 + 12 * count0 + 4
    data_at = exif_at + 2 + 12 + 4
    data, ifd0 = b"", b""
    if modified:
        ifd0 += struct.pack(e + "HHII", 0x0132, 2, 20, data_at)
        data += modified.encode() + b"\0"
    ifd0 += struct.pack(e + "HHII", 0x8769, 4, 1, exif_at)
    exif = struct.pack(e + "H", 1)
    if original:
        exif += struct.pack(e + "HHII", 0x9003, 2, 20, data_at + len(data))
        data += original.encode() + b"\0"
    else:
        exif += struct.pack(e + "HHII", 0x9000, 7, 4, 0)
    return (order + struct.pack(e + "HI", 42, 8) + struct.pack(e + "H", count0) + ifd0 + b"\0" * 4
            + exif + b"\0" * 4 + data)


def jpeg(block: bytes | None) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
    app1 = b"\xff\xe1" + struct.pack(">H", len(block) + 8) + b"Exif\0\0" + block if block else b""
    return b"\xff\xd8" + app0 + app1 + b"\xff\xda\0\x02\xff\xd9"


def test_exif_parser_both_byte_orders_and_containers(tmp_path: Path) -> None:
    for order in (b"II", b"MM"):
        dates = photos.tiff_dates(tiff_block(order, "2024:05:01 10:15:30", "2025:01:01 00:00:00"))
        assert dates[0x9003].isoformat() == "2024-05-01T10:15:30"
        assert dates[0x0132].isoformat() == "2025-01-01T00:00:00"
    assert photos.tiff_dates(tiff_block(b"II", "0000:00:00 00:00:00")) == {}
    assert photos.tiff_dates(b"II*\0\xff\xff\xff\x7f") == {}  # IFD offset past the end
    assert photos.tiff_dates(b"garbage") == {}
    when, source = photos.photo_date(write(tmp_path / "a.jpg", jpeg(tiff_block(b"MM", "2021:02:03 04:05:06"))))
    assert (when.isoformat(), source) == ("2021-02-03T04:05:06", "EXIF 拍攝時間")
    heic = write(tmp_path / "b.heic", b"\0\0\0\x18ftypheic" + b"\x11" * 300 + b"Exif\0\0"
                 + tiff_block(b"MM", "2020:12:31 23:59:59") + b"\x22" * 100)
    assert photos.photo_date(heic)[0].isoformat() == "2020-12-31T23:59:59"
    block = tiff_block(b"II", "2019:06:07 08:09:10")
    png = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 13, b"IHDR") + b"\0" * 17
           + struct.pack(">I4s", len(block), b"eXIf") + block + b"\0" * 4 + struct.pack(">I4s", 0, b"IEND") + b"\0" * 4)
    assert photos.photo_date(write(tmp_path / "c.png", png))[0].isoformat() == "2019-06-07T08:09:10"
    assert photos.photo_date(write(tmp_path / "d.jpg", b"\xff\xd8\xff\xe1\x00"))[0] is None  # truncated


def test_rename_photos(tmp_path: Path) -> None:
    p = tmp_path / "相簿"
    write(p / "IMG_1.jpg", jpeg(tiff_block(b"II", "2024:05:01 10:15:30")))
    write(p / "IMG_2.JPG", jpeg(tiff_block(b"MM", "2024:05:01 10:15:30")))
    write(p / "IMG_3.jpg", jpeg(tiff_block(b"MM", None, "2023:01:02 03:04:05")))
    stamp = time.mktime((2022, 2, 3, 4, 5, 6, 0, 0, -1))
    os.utime(write(p / "IMG_4.jpeg", jpeg(None)), (stamp, stamp))
    write(p / "2020-01-01_000000.jpg", jpeg(None))
    write(p / "notes.txt")
    before = snapshot(tmp_path)
    plan = preview("rename_photos_by_date", tmp_path, folder="相簿")
    assert snapshot(tmp_path) == before
    out = run("rename_photos_by_date", tmp_path, folder="相簿")
    assert arrows(plan) == arrows(out)
    names = {x.name for x in p.iterdir()}
    assert names == {"2024-05-01_101530.jpg", "2024-05-01_101530_2.JPG", "2023-01-02_030405.jpg",
                     "2022-02-03_040506.jpeg", "2020-01-01_000000.jpg", "notes.txt"}
    assert "（EXIF 拍攝時間）" in out and "（EXIF 修改時間）" in out and "（檔案修改時間）" in out
    assert "已經是這種名稱、不改的：1 張" in out
    with pytest.raises(ToolError, match="不能當檔名"):
        run("rename_photos_by_date", tmp_path, folder="相簿", pattern="%Y/%m")


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


# ---------------------------------------------------------------- find_large_files / find_duplicate_files


def test_find_large_files(tmp_path: Path) -> None:
    write(tmp_path / "big.bin", b"0" * 3_000_000)
    write(tmp_path / "sub" / "mid.bin", b"0" * 200_000)
    write(tmp_path / "small.txt", b"0" * 10)
    out = run("find_large_files", tmp_path, folder=".", top=2)
    lines = out.splitlines()
    assert "3 個檔案" in lines[0] and "big.bin" in lines[2] and "mid.bin" in lines[3] and "small" not in out
    assert "沒有 5 MB 以上的檔案" in run("find_large_files", tmp_path, folder=".", min_size_mb=5)


def test_find_duplicates_marks_oldest_and_deletes_nothing(tmp_path: Path) -> None:
    old = write(tmp_path / "a" / "photo.jpg", b"same bytes")
    os.utime(old, (1_500_000_000, 1_500_000_000))
    write(tmp_path / "b" / "photo copy.jpg", b"same bytes")
    write(tmp_path / "c.jpg", b"same size!")  # same size, different content
    write(tmp_path / "empty1.txt", b"")
    write(tmp_path / "empty2.txt", b"")
    before = snapshot(tmp_path)
    out = run("find_duplicate_files", tmp_path, folder=".")
    assert snapshot(tmp_path) == before
    assert "找到 1 組" in out and "沒有刪除任何檔案" in out and "archive_files" in out
    assert any("原始" in line and "photo.jpg" in line for line in out.splitlines())
    assert any("副本" in line and "photo copy.jpg" in line for line in out.splitlines())
    assert "c.jpg" not in out and "空檔案 2 個" in out
    assert "沒有內容完全相同" in run("find_duplicate_files", tmp_path, folder=".", pattern="*.txt;*.png")


# ---------------------------------------------------------------- archive_files


def test_archive_moves_with_structure_and_skips_protected(tmp_path: Path) -> None:
    write(tmp_path / "下載" / "a.tmp", "a")
    write(tmp_path / "下載" / "b.tmp", "b")
    write(tmp_path / "舊專案" / "x.txt")
    write(tmp_path / "設定" / ".env", "K=1")
    write(tmp_path / "personas.json", "[]")
    dest = f"封存/{date.today():%Y%m%d}"
    write(tmp_path / dest / "下載" / "a.tmp", "earlier")
    before = snapshot(tmp_path)
    plan = preview("archive_files", tmp_path, paths=["下載/*.tmp", "舊專案", "設定", "personas.json", "none*"])
    assert snapshot(tmp_path) == before
    out = run("archive_files", tmp_path, paths=["下載/*.tmp", "舊專案", "設定", "personas.json", "none*"])
    assert arrows(plan) == arrows(out) and len(arrows(out)) == 3
    assert (tmp_path / dest / "下載" / "a (2).tmp").read_text() == "a"
    assert (tmp_path / dest / "下載" / "a.tmp").read_text() == "earlier"
    assert (tmp_path / dest / "舊專案" / "x.txt").is_file() and not (tmp_path / "舊專案").exists()
    assert (tmp_path / "設定" / ".env").read_text() == "K=1" and (tmp_path / "personas.json").is_file()
    assert "沒有符合" in out and "沒有刪除任何東西" in out
    with pytest.raises(ToolError, match="工作區外面"):
        run("archive_files", tmp_path, paths=["../x"])


# ---------------------------------------------------------------- zip_files / unzip_file


def test_zip_files(tmp_path: Path) -> None:
    write(tmp_path / "報告" / "a.txt", "a")
    write(tmp_path / "報告" / "sub" / "b.txt", "b")
    write(tmp_path / "c.pdf", "c")
    before = snapshot(tmp_path)
    assert "2 個檔案" in preview("zip_files", tmp_path, paths=["報告"], output="報告")
    assert snapshot(tmp_path) == before
    out = run("zip_files", tmp_path, paths=["報告", "*.pdf"], output="打包")
    assert "3 個檔案" in out
    with zipfile.ZipFile(tmp_path / "打包.zip") as zf:
        assert sorted(n for n in zf.namelist() if not n.endswith("/")) == ["c.pdf", "報告/a.txt", "報告/sub/b.txt"]
    with pytest.raises(ToolError, match="已經存在"):
        run("zip_files", tmp_path, paths=["c.pdf"], output="打包.zip")


def test_unzip_extracts_without_overwriting(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / "包.zip", "w") as zf:
        zf.writestr("資料/a.txt", "new a")
        zf.writestr("b.txt", "b")
        zf.writestr("settings.json", "{}")
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
    before = snapshot(tmp_path)
    plan = preview("unzip_file", tmp_path, archive="包.zip")
    assert snapshot(tmp_path) == before and "2 個檔案" in plan
    out = run("unzip_file", tmp_path, archive="包.zip")
    assert (tmp_path / "包" / "資料" / "a.txt").read_text() == "new a"
    assert not (tmp_path / "包" / "link").exists() and not (tmp_path / "包" / "settings.json").exists()
    assert "symlink" in out and "使用者自己的設定檔" in out
    write(tmp_path / "into" / "b.txt", "mine")
    out = run("unzip_file", tmp_path, archive="包.zip", destination="into")
    assert (tmp_path / "into" / "b.txt").read_text() == "mine" and (tmp_path / "into" / "b (2).txt").read_text() == "b"
    assert "b (2).txt" in out
    run("unzip_file", tmp_path, archive="包.zip")
    assert (tmp_path / "包 (2)" / "b.txt").is_file()  # the default folder exists: a new one


@pytest.mark.parametrize("bad", ["../escape.txt", "/abs.txt", "C:/win.txt", "ok/../../escape.txt"])
def test_unzip_refuses_zip_slip(tmp_path: Path, bad: str) -> None:
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
        zf.writestr("fine.txt", "x")
        zf.writestr(bad, "x")
    before = snapshot(tmp_path)
    with pytest.raises(ToolError, match="危險的路徑"):
        run("unzip_file", tmp_path, archive="evil.zip")
    assert snapshot(tmp_path) == before and not (tmp_path.parent / "escape.txt").exists()


def test_unzip_big5_names(tmp_path: Path) -> None:
    class Big5Info(zipfile.ZipInfo):  # as zips made on Chinese Windows store names
        def _encodeFilenameFlags(self):
            return self.filename.encode("cp950"), self.flag_bits

    with zipfile.ZipFile(tmp_path / "舊.zip", "w") as zf:
        zf.writestr(Big5Info("報告.txt"), "x")
    run("unzip_file", tmp_path, archive="舊.zip")
    assert (tmp_path / "舊" / "報告.txt").read_text() == "x"


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
    write(tmp_path / "logs" / "app.log", "\n".join([
        "2026-10-01 10:00:01 INFO start",
        "2026-10-01 10:00:02 ERROR Connection refused to 10.0.0.5:5432",
        "2026-10-01 10:00:03 [error] Connection refused to 10.0.0.6:5432",
        "2026-10-01 10:00:04 WARN slow request 1234ms",
        "2026-10-01 10:00:05 INFO retry after error",
        "2026-10-01 10:00:06 FATAL out of memory at 0x7fff1234",
        "Traceback (most recent call last):",
        "2026-10-01 10:00:07 DEBUG id=550e8400-e29b-41d4-a716-446655440000",
        "2026-10-01 10:00:08 ERROR Connection refused to 10.0.0.7:5432",
    ]))
    write(tmp_path / "logs" / "other.txt", "ERROR not a log by pattern")
    out = run("log_summary", tmp_path, path="logs")
    assert "共 9 行" in out
    assert "錯誤 4（其中 FATAL／CRITICAL 1）、警告 1、INFO 2、DEBUG 1、沒有等級 1" in out
    assert "3 次，第 2 行 到 第 9 行" in out and "Connection refused to <n>.<n>.<n>.<n>:<n>" in out
    assert "out of memory at <hex>" in out and "not a log" not in out
    assert "沒有符合" in run("log_summary", tmp_path, path="logs", pattern="*.nothing")


# ---------------------------------------------------------------- system


def test_system_status_reports_each_part() -> None:
    out = run("system_status", Path.cwd())
    for part in ("作業系統：", "CPU：", "記憶體：", "磁碟：", "顯示卡記憶體", "用最多記憶體的程式"):
        assert part in out or part.rstrip("：") in out, part


@pytest.mark.skipif(IS_WINDOWS, reason="checks the message other systems get")
@pytest.mark.parametrize(("name", "params"), [("installed_apps", {}), ("startup_apps", {}),
                                              ("windows_info", {"topic": "power"})])
def test_windows_only_tools_say_so(tmp_path: Path, name: str, params: dict) -> None:
    assert REGISTRY[name].windows_only and run(name, tmp_path, **params) == WINDOWS_ONLY


class FakeKey:
    def __init__(self, hive: str, view: int, path: str) -> None:
        self.hive, self.view, self.path = hive, view, path

    def __enter__(self) -> FakeKey:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeReg:
    """Enough of winreg: keys are (hive, view, path) -> values."""

    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"
    KEY_READ, KEY_WOW64_64KEY, KEY_WOW64_32KEY = 0x20019, 0x100, 0x200

    def __init__(self, keys: dict[tuple[str, int, str], dict]) -> None:
        self.keys = keys

    def OpenKey(self, parent, sub: str, reserved: int = 0, access: int = 0x20019) -> FakeKey:  # noqa: N802
        if isinstance(parent, FakeKey):
            hive, view, path = parent.hive, parent.view, f"{parent.path}\\{sub}"
        else:
            hive, view, path = parent, access & 0x300, sub
        if not any(k[:2] == (hive, view) and (k[2] == path or k[2].startswith(path + "\\")) for k in self.keys):
            raise FileNotFoundError(path)
        return FakeKey(hive, view, path)

    def EnumKey(self, key: FakeKey, i: int) -> str:  # noqa: N802
        children = sorted({k[2][len(key.path) + 1:].split("\\")[0] for k in self.keys
                           if k[:2] == (key.hive, key.view) and k[2].startswith(key.path + "\\")})
        if i >= len(children):
            raise OSError("no more keys")
        return children[i]

    def QueryValueEx(self, key: FakeKey, name: str):  # noqa: N802
        values = self.keys.get((key.hive, key.view, key.path), {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def EnumValue(self, key: FakeKey, i: int):  # noqa: N802
        items = list(self.keys.get((key.hive, key.view, key.path), {}).items())
        if i >= len(items):
            raise OSError("no more values")
        return (*items[i], 1)


def test_installed_apps_from_registry() -> None:
    u = system.UNINSTALL
    reg = FakeReg({
        ("HKLM", 0x100, f"{u}\\Py"): {"DisplayName": "Python 3.12.4 (64-bit)", "DisplayVersion": "3.12.4",
                                      "Publisher": "Python Software Foundation", "InstallDate": "20240115"},
        ("HKLM", 0x200, f"{u}\\Py"): {"DisplayName": "Python 3.12.4 (64-bit)", "DisplayVersion": "3.12.4"},
        ("HKLM", 0x200, f"{u}\\Zip"): {"DisplayName": "7-Zip 23.01", "DisplayVersion": "23.01"},
        ("HKLM", 0x100, f"{u}\\Hidden"): {"DisplayName": "Runtime part", "SystemComponent": 1},
        ("HKLM", 0x100, f"{u}\\KB1"): {"DisplayName": "Update for X", "ParentKeyName": "X"},
        ("HKLM", 0x100, f"{u}\\NoName"): {"DisplayVersion": "1"},
        ("HKCU", 0, f"{u}\\Code"): {"DisplayName": "Microsoft Visual Studio Code (User)", "DisplayVersion": "1.90"},
    })
    entries = system.uninstall_entries(reg)
    assert [e["name"] for e in entries] == ["7-Zip 23.01", "Microsoft Visual Studio Code (User)",
                                            "Python 3.12.4 (64-bit)"]
    out = system.installed_text(entries, "PYTHON")
    assert "1 個" in out and "版本 3.12.4" in out and "安裝於 2024-01-15" in out and "7-Zip" not in out
    assert "沒有名稱" in system.installed_text(entries, "photoshop")


def test_startup_apps_from_registry_and_folder(tmp_path: Path) -> None:
    run_key = f"{system.RUN_KEYS}\\Run"
    reg = FakeReg({
        ("HKCU", 0, run_key): {"OneDrive": '"C:\\OneDrive.exe" /background', "Discord": "C:\\Discord.exe"},
        ("HKCU", 0, f"{system.APPROVED}\\Run"): {"Discord": b"\x03\0\0\0"},
        ("HKLM", 0x100, run_key): {"SecurityHealth": "C:\\Windows\\SecurityHealthSystray.exe"},
    })
    folder = tmp_path / "Startup"
    write(folder / "tool.lnk")
    write(folder / "desktop.ini")
    entries = system.startup_entries(reg, [("目前使用者「啟動」資料夾", folder)],
                                     lambda paths: {str(p): "C:\\tool.exe --tray" for p in paths})
    out = system.startup_text(entries)
    assert "項目 4 個（啟用 3、在工作管理員裡停用 1）" in out
    assert "Discord（已停用）" in out and "C:\\tool.exe --tray" in out and "desktop.ini" not in out
    assert "沒有找到" in system.startup_text([])


def test_windows_info_formatting(tmp_path: Path) -> None:
    real = tmp_path / "bin"
    real.mkdir()
    out = "\n".join(system.path_text(f"{real};{tmp_path / 'gone'};{real}", str(real), str(tmp_path / "gone")))
    assert "3 個資料夾" in out and "1 個不存在" in out and "｜系統｜存在" in out and "重複" in out
    lines = system.network_text(
        [{"Name": "Wi-Fi", "InterfaceDescription": "Intel AX201", "Status": "Up", "LinkSpeed": "866.7 Mbps",
          "MacAddress": "AA-BB"}, {"Name": "乙太網路", "InterfaceDescription": "Realtek", "Status": "Disconnected",
                                   "MacAddress": "CC-DD"}],
        [{"alias": "Wi-Fi", "ipv4": ["192.168.1.20"], "ipv6": [], "gw": ["192.168.1.1"], "dns": ["8.8.8.8"]}])
    assert lines[0].startswith("- Wi-Fi") and "IPv4：192.168.1.20" in "\n".join(lines) and "Disconnected" in lines[-1]


# ---------------------------------------------------------------- git_overview


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="no git here")
def test_git_overview(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    write(repo / "a.txt", "one\n")
    write(repo / "刪.txt", "x")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "第一個提交")
    write(repo / "a.txt", "".join(f"line {i}\n" for i in range(600)))
    (repo / "刪.txt").unlink()
    write(repo / "新.txt", "n")
    write(repo / "staged.txt", "s")
    git(repo, "add", "staged.txt")
    out = run("git_overview", tmp_path, repo="repo", what="status")
    assert "分支：main" in out and "已修改 1" in out and "新增 1" in out and "已刪除 1" in out and "未追蹤 1" in out
    assert "刪.txt" in out and "新.txt" in out  # real UTF-8 names, not quoted octal
    assert "第一個提交" in run("git_overview", tmp_path, repo="repo", what="log")
    out = run("git_overview", tmp_path, repo="repo", what="diff")
    assert "a.txt" in out.split("\n")[2] and "差異太長，只列前" in out
    assert "staged.txt" in run("git_overview", tmp_path, repo="repo", what="staged_diff")
    assert "第一個提交" in run("git_overview", tmp_path, repo="repo", what="show")
    assert "* main" in run("git_overview", tmp_path, repo="repo", what="branches")
    with pytest.raises(ToolError, match="不是合法的版本名稱"):
        run("git_overview", tmp_path, repo="repo", what="diff", ref="--output=/tmp/x")
    with pytest.raises(ToolError, match="不是 git 儲存庫"):
        run("git_overview", tmp_path, repo=".", what="status")


# ---------------------------------------------------------------- run_checks


def test_run_checks_pass_and_fail(tmp_path: Path) -> None:
    write(tmp_path / "proj" / "tests" / "test_a.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    before = snapshot(tmp_path)
    plan = preview("run_checks", tmp_path, path="proj")
    assert "pytest" in plan and "會在「proj」裡執行" in plan and snapshot(tmp_path) == before
    out = run("run_checks", tmp_path, path="proj")
    assert out.startswith("檢查通過") and "1 通過" in out
    write(tmp_path / "proj" / "tests" / "test_b.py", "def test_bad():\n    assert 1 == 2, '數字不對'\n")
    out = run("run_checks", tmp_path, path="proj")
    assert out.startswith("檢查沒通過（結束代碼 1）") and "1 失敗、1 通過" in out
    assert "tests/test_b.py::test_bad：AssertionError: 數字不對" in out
    with pytest.raises(ToolError, match="找不到這個專案的測試設定"):
        run("run_checks", tmp_path, path=".")


def test_run_checks_command_and_timeout(tmp_path: Path) -> None:
    out = run("run_checks", tmp_path, command="echo 你好")
    assert out.startswith("檢查通過") and "你好" in out
    assert run("run_checks", tmp_path, command="exit 3").startswith("檢查沒通過（結束代碼 3）")
    sleeper = "Start-Sleep 30" if IS_WINDOWS else "sleep 30 & sleep 30; wait"
    start = time.monotonic()
    out = run("run_checks", tmp_path, command=sleeper, timeout_s=1)
    assert out.startswith("檢查沒通過：超過 1 秒") and time.monotonic() - start < 20


# ---------------------------------------------------------------- code_outline


def test_code_outline_and_query(tmp_path: Path) -> None:
    write(tmp_path / "src" / "app.py", "class Loader:\n    def read(self, n):\n        return n\n\n"
                                       "    class Inner:\n        async def go(self): ...\n\n"
                                       "def load_config(path, *, strict=False):\n    '''讀取設定檔'''\n")
    write(tmp_path / "src" / "web.mjs", "export async function startServer(port) {}\nexport const ping = () => 1;\n"
                                        "class Cache {\n  get(key) {\n    return 1;\n  }\n}\n")
    write(tmp_path / "src" / "tool.ps1", "function Get-Report {\n}\n")
    write(tmp_path / "node_modules" / "dep.js", "function hidden() {}\n")
    out = run("code_outline", tmp_path, path="src")
    for expected in ("2  def Loader.read(self, n)", "6  async def Loader.Inner.go(self)", "8  def load_config(",
                     "1  function startServer", "2  function ping", "4  method Cache.get",
                     "1  function Get-Report"):
        assert expected in out, expected
    assert "hidden" not in run("code_outline", tmp_path, path=".")
    out = run("code_outline", tmp_path, path=".", query="load config")
    assert out.splitlines()[1].startswith(os.path.join("src", "app.py") + ":8")
    out = run("code_outline", tmp_path, path=".", query="設定")
    assert "app.py:9" in out
    assert "找不到" in run("code_outline", tmp_path, path=".", query="zzzz")


# ---------------------------------------------------------------- calculate / date_calc


@pytest.mark.parametrize(("expression", "phrase"), [
    ("0.1 + 0.2", "結果：0.3（精確值）"),
    ("1,234,567 * 2", "結果：2,469,134（精確值）"),
    ("15% * 3200", "結果：480（精確值）"),
    ("10 % 3", "結果：1（精確值）"),
    ("2^10", "結果：1024（精確值）"),
    ("1/3", "結果：1/3（分數，精確值）≈ 0.3333333333"),
    ("sqrt(16/9)", "4/3"),
    ("sqrt(2)", "結果 ≈ 1.414213562"),
    ("max(1,234)", "結果：234"),
    ("round(2.675, 2)", "結果：2.68"),
    ("３×４", "結果：12"),
    ("(1,200 + 350) * 1.05", "結果：1627.5（精確值）"),
])
def test_calculate(expression: str, phrase: str) -> None:
    out = run("calculate", Path.cwd(), expression=expression)
    assert phrase in out and out.startswith("算式（解讀為）："), out


@pytest.mark.parametrize(("expression", "reason"), [
    ("__import__('os').system('x')", "只能算數學算式"), ("1/0", "除以零"), ("2**100000", "位數太多"),
    ("open('f')", "只能算數學算式"), ("2(3+4)", "乘法要寫"), ("a + 1", "只能算數學算式"), ("sqrt(-1)", "負數"),
])
def test_calculate_refuses(expression: str, reason: str) -> None:
    with pytest.raises(ToolError, match=reason):
        run("calculate", Path.cwd(), expression=expression)


def test_date_calc() -> None:
    here = Path.cwd()
    assert "相差 278 天（39 週又 5 天）" in run("date_calc", here, op="diff", date="2026-01-01", other="2026-10-06")
    assert "2024-02-29（星期四）" in run("date_calc", here, op="add", date="2024-03-01", days=-1)
    soon = date.today() + timedelta(days=100)
    assert soon.isoformat() in run("date_calc", here, op="add", date="今天", days="100")
    assert run("date_calc", here, op="weekday", date="民國115年10月6日") == "2026-10-06 是星期二。"
    assert "用今天" in run("date_calc", here, op="diff", date="2000-01-01")
    with pytest.raises(ToolError, match="沒有「2026-02-30」這一天"):
        run("date_calc", here, op="weekday", date="2026-02-30")
    with pytest.raises(ToolError, match="要給 days"):
        run("date_calc", here, op="add", date="2026-01-01")


def test_catalog_search_reads_chinese_and_english():
    """edge_agent/catalog.mjs: Chinese requests are indexed as character pairs."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from edge_agent.toolbox import specs

    script = (
        "import { rank, searchText } from './edge_agent/catalog.mjs';"
        "const tools = JSON.parse(process.argv[1]).map(t => ({ name: t.name, text: searchText({ ...t, description: t.summary }) }));"
        "console.log(JSON.stringify(['把下載資料夾整理一下', '照片 改名 日期', 'convert Big5 text to UTF-8', '跑測試']"
        ".map(q => rank(q, tools, 3).map(m => m.name))));"
    )
    out = subprocess.run([node, "--input-type=module", "-e", script, json.dumps(specs())],
                         capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], check=True)
    organize, photos, encoding, checks = json.loads(out.stdout)
    assert organize[0] == "organize_folder"
    assert photos[0] == "rename_photos_by_date"
    assert encoding[0] == "convert_text_encoding"
    assert "run_checks" in checks


def test_organize_into_the_folders_the_user_named(tmp_path):
    """The user's own folder names: 影音 takes video and audio, documents take
    PDFs and slides, the rest goes to 其他; names may come as one string."""
    from edge_agent.toolbox import REGISTRY

    d = tmp_path / "下載"
    d.mkdir()
    for name in ("a.jpg", "b.mp4", "c.m4a", "d.pdf", "e.pptx", "f.zip", "g.ttf", "setup.exe", "noext"):
        (d / name).write_bytes(b"x")
    tool = REGISTRY["organize_folder"]
    params = {"folder": "下載", "folders": ["圖片、文件", "壓縮檔", "影音", "其他"]}
    before = sorted(p.name for p in d.iterdir())
    assert "影音" in tool.preview(params, tmp_path) and sorted(p.name for p in d.iterdir()) == before
    tool.run(params, tmp_path)
    where = {p.name: p.parent.name for p in d.rglob("*") if p.is_file()}
    assert where == {"a.jpg": "圖片", "b.mp4": "影音", "c.m4a": "影音", "d.pdf": "文件", "e.pptx": "文件",
                     "f.zip": "壓縮檔", "g.ttf": "其他", "setup.exe": "其他", "noext": "其他"}
    # Without an "other" folder, what fits nothing stays and is reported.
    (d / "h.ttf").write_bytes(b"x")
    out = tool.run({"folder": "下載", "folders": ["圖片"]}, tmp_path)
    assert (d / "h.ttf").is_file() and "沒有適合" in out


def test_a_servers_tools_are_listed_together():
    """Chinese words that only name a server tie its English-described tools;
    once one is found, the rest of that small server come along (catalog.mjs)."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "import { rank, searchText, withServers } from './edge_agent/catalog.mjs';"
        "const ns = { name: 'mcp__drone', description: '無人機：狀態、位置、起飛、降落' };"
        "const tools = ['arm', 'land', 'return_to_launch', 'get_position', 'takeoff', 'get_status']"
        ".map(n => ({ name: 'mcp__drone__' + n, description: n === 'get_status' ? 'Vehicle status: armed, flight mode, "
        "battery voltage and percentage, GPS fix and satellites' : n, namespace: ns }));"
        "const docs = tools.map(t => ({ name: t.name, text: searchText(t) }));"
        "const found = rank('無人機電池狀態', docs, 5);"
        "console.log(JSON.stringify([found.map(m => m.name), withServers(found, tools.map(t => ({ name: t.name, server: t.namespace.name })))]));"
    )
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[1], check=True)
    ranked, listed = json.loads(out.stdout)
    assert "mcp__drone__get_status" not in ranked  # the tie the cut would lose
    assert "mcp__drone__get_status" in listed and len(listed) == 6
