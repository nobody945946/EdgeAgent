"""edge_agent.toolbox folder chores: backup, organize, find, archive, zip and unzip.

Each tool's main path and the safety edge it must keep.
"""

from __future__ import annotations

import os
import time
import zipfile
from datetime import date
from pathlib import Path

import pytest

from edge_agent.toolbox import ToolError
from tests.toolbox_helpers import arrows, preview, run, snapshot, write

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
