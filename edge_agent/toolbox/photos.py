"""Rename photos by the time they were taken.

The date comes from EXIF (read here with a small stdlib parser: JPEG APP1,
TIFF-based raw files, PNG eXIf, WebP EXIF, and a marker scan for HEIC), and
only when a photo has none from the file's modified time, which the result
says per photo because a copied file's time is the copy's, not the shot's.
"""

from __future__ import annotations

import mmap
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, oserror_text, register, resolve_inside
from edge_agent.toolbox.fs import (
    PROTECTED_NOTE,
    cap,
    free_name,
    is_hidden,
    is_protected,
    key,
    list_files,
    move_no_clobber,
    prune_hidden,
    shown,
)

JPEG = {".jpg", ".jpeg", ".jpe", ".jfif"}
TIFF = {".tif", ".tiff", ".dng", ".nef", ".cr2", ".arw", ".orf", ".pef", ".srw", ".rw2"}
SCAN = {".heic", ".heif", ".hif", ".avif", ".cr3", ".raf"}  # EXIF sits inside a container
PHOTO_SUFFIXES = JPEG | TIFF | SCAN | {".png", ".webp"}

DATE_TAGS = (
    (0x9003, "EXIF 拍攝時間"),  # DateTimeOriginal, in the Exif IFD
    (0x0132, "EXIF 修改時間"),  # DateTime, in IFD0: what the camera or an editor last wrote
)
MTIME = "檔案修改時間"
_EXIF_DATE = re.compile(rb"(\d{4})[:\-/](\d{2})[:\-/](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


# ---------------------------------------------------------------- EXIF


def _ifd(buf, base: int, offset: int, order: str) -> dict[int, tuple[int, int, bytes]]:
    """tag -> (type, count, the 4-byte value field) of one IFD; {} if it runs off the end."""
    start = base + offset
    if offset <= 0 or start + 2 > len(buf):
        return {}
    (count,) = struct.unpack(order + "H", buf[start:start + 2])
    entries = {}
    for i in range(min(count, 1000)):
        pos = start + 2 + 12 * i
        if pos + 12 > len(buf):
            break
        tag, kind, n = struct.unpack(order + "HHI", buf[pos:pos + 8])
        entries[tag] = (kind, n, bytes(buf[pos + 8:pos + 12]))
    return entries


def _ascii_date(buf, base: int, order: str, entry: tuple[int, int, bytes]) -> datetime | None:
    kind, n, field_ = entry
    if kind != 2 or not 0 < n < 64:
        return None
    if n <= 4:
        raw = field_[:n]
    else:
        (offset,) = struct.unpack(order + "I", field_)
        if base + offset + n > len(buf):
            return None
        raw = bytes(buf[base + offset:base + offset + n])
    m = _EXIF_DATE.match(raw.strip(b"\x00 "))
    if not m:
        return None  # blank or "0000:00:00 00:00:00": cameras without a set clock write these
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def tiff_dates(buf, base: int = 0) -> dict[int, datetime]:
    """DateTimeOriginal (0x9003) and DateTime (0x0132) from a TIFF block at buf[base:]."""
    head = bytes(buf[base:base + 8])
    if len(head) < 8 or head[:2] not in (b"II", b"MM"):
        return {}
    order = "<" if head[:2] == b"II" else ">"
    magic, ifd0 = struct.unpack(order + "HI", head[2:8])
    if magic != 42:
        return {}
    found: dict[int, datetime] = {}
    first = _ifd(buf, base, ifd0, order)
    if 0x0132 in first and (when := _ascii_date(buf, base, order, first[0x0132])):
        found[0x0132] = when
    pointer = first.get(0x8769)
    if pointer and pointer[0] in (4, 13):
        (offset,) = struct.unpack(order + "I", pointer[2])
        exif = _ifd(buf, base, offset, order)
        if 0x9003 in exif and (when := _ascii_date(buf, base, order, exif[0x9003])):
            found[0x9003] = when
    return found


def _jpeg_tiff(f) -> bytes | None:
    """The TIFF block inside the JPEG's APP1 Exif segment, reading segment by segment."""
    if f.read(2) != b"\xff\xd8":
        return None
    while True:
        if f.read(1) != b"\xff":
            return None
        marker = f.read(1)
        while marker == b"\xff":  # fill bytes
            marker = f.read(1)
        if not marker:
            return None
        m = marker[0]
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            continue
        if m in (0xD9, 0xDA):  # end, or image data: no metadata after this
            return None
        raw = f.read(2)
        if len(raw) < 2:
            return None
        (size,) = struct.unpack(">H", raw)
        if size < 2:
            return None
        if m == 0xE1:
            body = f.read(size - 2)
            if body.startswith(b"Exif\x00\x00"):
                return body[6:]
        else:
            f.seek(size - 2, 1)


def _png_tiff(f) -> bytes | None:
    if f.read(8) != b"\x89PNG\r\n\x1a\n":
        return None
    while True:
        head = f.read(8)
        if len(head) < 8:
            return None
        size, kind = struct.unpack(">I4s", head)
        if kind == b"eXIf":
            return f.read(size)
        if kind == b"IEND":
            return None
        f.seek(size + 4, 1)  # data and CRC


def _webp_tiff(f) -> bytes | None:
    head = f.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    while True:
        chunk = f.read(8)
        if len(chunk) < 8:
            return None
        kind, size = struct.unpack("<4sI", chunk)
        if kind == b"EXIF":
            data = f.read(size)
            return data[6:] if data.startswith(b"Exif\x00\x00") else data
        f.seek(size + (size & 1), 1)


def photo_date(path: Path) -> tuple[datetime | None, str]:
    """(EXIF date, which tag) or (None, "") when the photo carries none we can read."""
    suffix = path.suffix.casefold()
    dates: dict[int, datetime] = {}
    try:
        with open(path, "rb") as f:
            if suffix in JPEG:
                block = _jpeg_tiff(f)
                dates = tiff_dates(block) if block else {}
            elif suffix == ".png":
                block = _png_tiff(f)
                dates = tiff_dates(block) if block else {}
            elif suffix == ".webp":
                block = _webp_tiff(f)
                dates = tiff_dates(block) if block else {}
            elif path.stat().st_size > 0:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    if suffix in TIFF:
                        dates = tiff_dates(mm)
                    else:
                        for magic in (b"Exif\x00\x00MM\x00\x2a", b"Exif\x00\x00II\x2a\x00"):
                            at = mm.find(magic)
                            if at >= 0 and (dates := tiff_dates(mm, at + 6)):
                                break
    except (OSError, ValueError, struct.error):
        return None, ""
    for tag, label in DATE_TAGS:
        if tag in dates:
            return dates[tag], label
    return None, ""


# ---------------------------------------------------------------- rename_photos_by_date

_DIRECTIVES = set("YymdHIMSpjbBaA%")
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _check_pattern(pattern: str) -> None:
    used = re.findall(r"%(.)", pattern)
    unknown = sorted({d for d in used if d not in _DIRECTIVES})
    if unknown or pattern.endswith("%") and not pattern.endswith("%%"):
        raise ToolError(f"pattern 裡有不支援的 % 代碼：{'、'.join('%' + d for d in unknown) or '%'}"
                        "（可以用 %Y %m %d %H %M %S 等）")
    sample = datetime(2024, 5, 1, 10, 15, 30).strftime(pattern)
    if not sample.strip() or _BAD_NAME.search(sample) or sample.endswith((".", " ")):
        raise ToolError(f"pattern「{pattern}」產生的名稱「{sample}」不能當檔名（不能有 / \\ : * ? \" < > |）")


def _already_named(stem: str, pattern: str) -> bool:
    for candidate in (stem, re.sub(r"_\d+$", "", stem)):
        try:
            if datetime.strptime(candidate, pattern).strftime(pattern) == candidate:
                return True
        except ValueError:
            pass
    return False


@dataclass
class RenamePlan:
    renames: list[tuple[Path, Path, str]] = field(default_factory=list)  # old, new, date source
    skipped: list[str] = field(default_factory=list)
    already: int = 0
    notes: list[str] = field(default_factory=list)


def _rename_plan(params: dict, root: Path) -> tuple[Path, RenamePlan]:
    folder = resolve_inside(root, params["folder"])
    if not folder.is_dir():
        raise ToolError(f"「{params['folder']}」不是資料夾")
    pattern = params["pattern"]
    _check_pattern(pattern)
    listing = list_files(folder, recursive=params["include_subfolders"], prune=prune_hidden)
    plan = RenamePlan()
    taken: set[str] = set()
    for path in listing.files:
        if path.suffix.casefold() not in PHOTO_SUFFIXES:
            continue
        if is_protected(path) or is_hidden(path):
            plan.skipped.append(f"- {shown(root, path)}：{PROTECTED_NOTE if is_protected(path) else '隱藏檔'}")
            continue
        if _already_named(path.stem, pattern):
            plan.already += 1
            continue
        when, source = photo_date(path)
        if when is None:
            try:
                when, source = datetime.fromtimestamp(path.stat().st_mtime), MTIME
            except OSError as exc:
                plan.skipped.append(f"- {shown(root, path)}：{oserror_text(exc)}")
                continue
        target = path.with_name(when.strftime(pattern) + path.suffix)
        if key(target) == key(path):
            plan.already += 1
            continue
        plan.renames.append((path, free_name(target, taken, style="_{}"), source))
    if listing.links:
        plan.notes.append(f"捷徑／連結 {len(listing.links)} 個不動")
    return folder, plan


def _sources(plan: RenamePlan) -> str:
    counts: dict[str, int] = {}
    for *_, source in plan.renames:
        counts[source] = counts.get(source, 0) + 1
    return "日期來源：" + "、".join(f"{s} {n} 張" for s, n in counts.items())


def _rename_lines(plan: RenamePlan) -> list[str]:
    return [f"- {old.name} → {new.name}（{source}）" for old, new, source in plan.renames]


def _rename_tail(plan: RenamePlan) -> list[str]:
    lines = []
    if plan.already:
        lines.append(f"已經是這種名稱、不改的：{plan.already} 張")
    if plan.skipped:
        lines += [f"略過 {len(plan.skipped)} 個：", *cap(plan.skipped, 15)]
    if any(source == MTIME for *_, source in plan.renames):
        lines.append("注意：用「檔案修改時間」的照片沒有 EXIF 日期，檔案修改時間不一定是拍攝日（例如複製或下載過的檔案）。")
    return [*lines, *plan.notes]


def rename_preview(params: dict, root: Path) -> str:
    folder, plan = _rename_plan(params, root)
    if not plan.renames:
        return "\n".join([f"「{shown(root, folder)}」裡沒有需要改名的照片。", *_rename_tail(plan)])
    return "\n".join([f"預計在「{shown(root, folder)}」裡改名 {len(plan.renames)} 張照片（還沒有做）：",
                      _sources(plan), *cap(_rename_lines(plan)), *_rename_tail(plan),
                      "同一秒的照片會加 _2、_3；不會覆蓋任何檔案，副檔名不變。"])


def rename_run(params: dict, root: Path) -> str:
    folder, plan = _rename_plan(params, root)
    if not plan.renames:
        return "\n".join([f"「{shown(root, folder)}」裡沒有需要改名的照片，什麼都沒動。", *_rename_tail(plan)])
    done, failed = [], []
    for old, new, source in plan.renames:
        try:
            move_no_clobber(old, new)
            done.append(f"- {old.name} → {new.name}（{source}）")
        except OSError as exc:
            failed.append(f"- {shown(root, old)}：{oserror_text(exc)}，沒有改名")
    lines = [f"已在「{shown(root, folder)}」裡改名 {len(done)} 張照片。" + (f"{len(failed)} 張失敗。" if failed else ""),
             _sources(plan)]
    if failed:
        lines += ["沒有改名的：", *cap(failed, 15)]
    return "\n".join([*lines, *cap(done), *_rename_tail(plan)])


register(Tool(
    name="rename_photos_by_date",
    summary="Rename photos by the date they were taken (EXIF), falling back to the file's modified time.",
    keywords="照片 相片 改名 重新命名 拍攝日期 日期 時間 EXIF rename photos pictures by date taken camera "
             "jpg heic 手機照片",
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "照片所在的資料夾（相對於工作區）"},
            "pattern": {"type": "string", "default": "%Y-%m-%d_%H%M%S",
                        "description": "新名稱的格式（Python strftime），預設 %Y-%m-%d_%H%M%S，"
                                       "例如 2024-05-01_101530.jpg；副檔名保持原樣"},
            "include_subfolders": {"type": "boolean", "default": False,
                                   "description": "true 就連子資料夾裡的照片也改名（各自留在原本的資料夾）"},
        },
        "required": ["folder"],
    },
    kind="change", destructive=True, windows_only=False,
    run=rename_run, preview=rename_preview,
))
