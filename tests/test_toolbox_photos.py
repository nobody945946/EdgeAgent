"""edge_agent.toolbox.photos: EXIF dates and rename_photos_by_date."""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest

from edge_agent.toolbox import ToolError, photos
from tests.toolbox_helpers import arrows, preview, run, snapshot, write


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
