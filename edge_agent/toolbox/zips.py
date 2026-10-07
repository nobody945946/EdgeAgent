"""zip_files and unzip_file: a new zip, or a zip extracted, never over existing files.

A new zip is written to a `.partial` file, read back and only then moved
into place. Extraction refuses a zip whose paths would leave the
destination, skips links, password-protected entries and the owner's own
files, and checks each extracted file's size against the zip's record.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, oserror_text, register, resolve_inside
from edge_agent.toolbox.fs import (
    PROTECTED_NOTE,
    cap,
    expand_paths,
    free_name,
    human_count,
    human_size,
    is_protected,
    list_files,
    move_no_clobber,
    shown,
)

# ---------------------------------------------------------------- zip_files


@dataclass
class ZipPlan:
    output: Path
    entries: list[tuple[Path, str, int]]  # file, name inside the zip, size
    dirs: list[str]
    notes: list[str]


def _zip_plan(params: dict, root: Path) -> ZipPlan:
    output = resolve_inside(root, params["output"], must_exist=False)
    if output.suffix.casefold() != ".zip":
        output = output.with_name(output.name + ".zip")
    if os.path.lexists(output):
        raise ToolError(f"「{shown(root, output)}」已經存在，不覆蓋；請換一個 output 名稱")
    items, notes = expand_paths(root, params["paths"])
    if not items:
        raise ToolError("沒有找到要壓縮的檔案。" + " ".join(notes))
    base = Path(os.path.commonpath([str(p.parent) for p in items]))
    entries: list[tuple[Path, str, int]] = []
    dirs: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.is_dir():
            listing = list_files(item, recursive=True)
            files = listing.files
            dirs += [d.relative_to(base).as_posix() for d in [item, *listing.dirs]]
            if listing.links:
                notes.append(f"「{shown(root, item)}」裡的捷徑／連結 {len(listing.links)} 個不壓縮")
        else:
            files = [item]
        for path in files:
            arc = path.relative_to(base).as_posix()
            if arc not in seen:
                seen.add(arc)
                entries.append((path, arc, path.stat().st_size))
    return ZipPlan(output, entries, dirs, notes)


def _zip_head(plan: ZipPlan, root: Path) -> list[str]:
    total = sum(size for *_, size in plan.entries)
    return [f"壓縮檔：「{shown(root, plan.output)}」，{human_count(len(plan.entries))} 個檔案，原始大小 {human_size(total)}",
            *cap([f"- {arc}" for _, arc, _ in plan.entries], 30)]


def zip_preview(params: dict, root: Path) -> str:
    plan = _zip_plan(params, root)
    return "\n".join(["預計建立壓縮檔（還沒有做）：", *_zip_head(plan, root), *plan.notes,
                      "原本的檔案不會被改動。"])


def zip_run(params: dict, root: Path) -> str:
    plan = _zip_plan(params, root)
    tmp = free_name(plan.output.with_name(plan.output.name + ".partial"), set())
    failed = []
    try:
        with zipfile.ZipFile(tmp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for name in sorted(set(plan.dirs)):
                zf.mkdir(name)
            for path, arc, _ in plan.entries:
                try:
                    zf.write(path, arc)
                except OSError as exc:
                    failed.append(f"- {arc}：{oserror_text(exc)}")
        with zipfile.ZipFile(tmp) as zf:
            bad = zf.testzip()
            count = sum(1 for i in zf.infolist() if not i.is_dir())
        if bad is not None:
            raise ToolError(f"壓縮檔檢查失敗（{bad} 損壞），沒有建立")
        move_no_clobber(tmp, plan.output)
    except BaseException:
        tmp.unlink(missing_ok=True)  # our own half-written file
        raise
    lines = [f"已建立壓縮檔，裡面有 {human_count(count)} 個檔案，壓縮後 {human_size(plan.output.stat().st_size)}；"
             "已檢查過每個檔案都讀得回來。", *_zip_head(plan, root), *plan.notes]
    if failed:
        lines += [f"沒能放進去的 {len(failed)} 個：", *cap(failed, 15)]
    return "\n".join(lines)


register(Tool(
    name="zip_files",
    summary="Create a new .zip from files, folders or glob patterns (never overwrites an existing zip).",
    keywords="壓縮 打包 zip 壓縮檔 compress pack archive 寄送 傳給別人",
    parameters={
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "要放進壓縮檔的檔案或資料夾，可以用萬用字元，例如 [\"報告\", \"*.pdf\"]"},
            "output": {"type": "string", "description": "新壓縮檔的名稱，例如「報告.zip」；已經存在就不做"},
        },
        "required": ["paths", "output"],
    },
    kind="change", destructive=False, windows_only=False,
    run=zip_run, preview=zip_preview,
))


# ---------------------------------------------------------------- unzip_file

_WINDOWS_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _entry_name(info: zipfile.ZipInfo) -> str:
    """The real name: zips made on Chinese Windows store Big5 names without saying so."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437")
    for codec in ("utf-8", "cp950"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            pass
    return info.filename


def _safe_part(part: str) -> str:
    """Windows cannot create some names; change them the same way on every system."""
    part = _WINDOWS_BAD.sub("_", part).rstrip(". ") or "_"
    return "_" + part if part.split(".")[0].casefold() in _RESERVED else part


@dataclass
class UnzipPlan:
    archive: Path
    dest: Path
    files: list[tuple[zipfile.ZipInfo, Path, str]]  # entry, target, original name
    dirs: list[Path]
    skipped: list[str]

    @property
    def total(self) -> int:
        return sum(info.file_size for info, _, _ in self.files)


def _unzip_plan(params: dict, root: Path) -> UnzipPlan:
    archive = resolve_inside(root, params["archive"])
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ToolError(f"「{params['archive']}」不是 zip 壓縮檔（目前只支援 .zip，不支援 .rar、.7z）")
    if params["destination"]:
        dest = resolve_inside(root, params["destination"], must_exist=False)
        if dest.exists() and not dest.is_dir():
            raise ToolError(f"「{shown(root, dest)}」是檔案，不能解壓縮到它裡面")
    else:
        dest = free_name(archive.parent / archive.stem, set(), is_dir=True)
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
    dangerous, skipped = [], []
    files: list[tuple[zipfile.ZipInfo, Path, str]] = []
    dirs: list[Path] = []
    taken: set[str] = set()
    for info in infos:
        name = _entry_name(info)
        norm = name.replace("\\", "/")
        parts = [p for p in norm.split("/") if p not in ("", ".")]
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
            dangerous.append(f"- {name}：絕對路徑")
            continue
        if ".." in parts:
            dangerous.append(f"- {name}：用 .. 跳到資料夾外面")
            continue
        if not parts:
            continue
        mode = info.external_attr >> 16
        if (mode & 0o170000) == 0o120000:
            skipped.append(f"- {name}：是連結（symlink），不解開")
            continue
        if info.flag_bits & 0x1:
            skipped.append(f"- {name}：有密碼保護，不解開")
            continue
        if is_protected(parts[-1]):
            skipped.append(f"- {name}：{PROTECTED_NOTE}，不解開")
            continue
        target = dest.joinpath(*(_safe_part(p) for p in parts))
        if info.is_dir():
            dirs.append(target)
            continue
        files.append((info, free_name(target, taken), name))
    if dangerous:
        raise ToolError("這個壓縮檔裡有危險的路徑（會寫到目的地外面），整個不解開：\n" + "\n".join(cap(dangerous, 15)))
    plan = UnzipPlan(archive, dest, files, dirs, skipped)
    probe = next((p for p in [dest, *dest.parents] if p.exists()), root)
    free = shutil.disk_usage(probe).free
    if plan.total > free:
        raise ToolError(f"解開後要 {human_size(plan.total)}，磁碟只剩 {human_size(free)}，沒有解開")
    return plan


def _unzip_lines(plan: UnzipPlan) -> list[str]:
    lines = []
    for _, target, name in plan.files:
        rel = target.relative_to(plan.dest).as_posix()
        lines.append(f"- {rel}" if rel == name.replace("\\", "/").strip("/") else f"- {name} → {rel}（同名或名稱不合法，改名）")
    return lines


def _nothing_to_unzip(plan: UnzipPlan, root: Path) -> str:
    return "\n".join([f"「{shown(root, plan.archive)}」裡沒有可以解開的檔案，什麼都沒動。",
                      *([f"略過 {len(plan.skipped)} 個：", *cap(plan.skipped, 15)] if plan.skipped else [])])


def unzip_preview(params: dict, root: Path) -> str:
    plan = _unzip_plan(params, root)
    if not plan.files:
        return _nothing_to_unzip(plan, root)
    exists = "（已經存在，放進裡面；同名的檔案改名，不覆蓋）" if plan.dest.exists() else "（新資料夾）"
    lines = [f"預計把「{shown(root, plan.archive)}」解壓縮到「{shown(root, plan.dest)}」{exists}（還沒有做）：",
             f"{human_count(len(plan.files))} 個檔案，解開後 {human_size(plan.total)}", *cap(_unzip_lines(plan), 30)]
    if plan.skipped:
        lines += [f"略過 {len(plan.skipped)} 個：", *cap(plan.skipped, 15)]
    return "\n".join(lines)


def unzip_run(params: dict, root: Path) -> str:
    plan = _unzip_plan(params, root)
    if not plan.files:
        return _nothing_to_unzip(plan, root)
    failed = []
    done = 0
    with zipfile.ZipFile(plan.archive) as zf:
        for folder in plan.dirs:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                failed.append(f"- {folder.relative_to(plan.dest).as_posix()}/：{oserror_text(exc)}")
        plan.dest.mkdir(parents=True, exist_ok=True)
        for info, target, name in plan.files:
            created = False
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "xb") as out:  # x: never replace a file
                    created = True
                    shutil.copyfileobj(src, out, 1 << 20)
                if target.stat().st_size != info.file_size:
                    raise ToolError("解開後的大小和壓縮檔記錄的不符")
            except (OSError, zipfile.BadZipFile, ToolError, RuntimeError, NotImplementedError) as exc:
                if created:
                    target.unlink(missing_ok=True)  # our own half-written file
                reason = (oserror_text(exc) if isinstance(exc, OSError) else
                          "這種壓縮方式不支援（例如 Deflate64）" if isinstance(exc, NotImplementedError) else str(exc))
                failed.append(f"- {name}：{reason}")
            else:
                done += 1
                try:
                    stamp = time.mktime((*info.date_time, 0, 0, -1))
                    os.utime(target, (stamp, stamp))
                except (OSError, OverflowError, ValueError):
                    pass  # the original time is a nicety; the content is what matters
    head = (f"已把「{shown(root, plan.archive)}」解壓縮到「{shown(root, plan.dest)}」："
            f"{human_count(done)} 個檔案，{human_size(plan.total)}。")
    lines = [head + (f"{len(failed)} 個失敗。" if failed else "每個檔案的檢查碼都對。")]
    if failed:
        lines += ["失敗的：", *cap(failed, 15)]
    lines += cap(_unzip_lines(plan), 30)
    if plan.skipped:
        lines += [f"略過 {len(plan.skipped)} 個：", *cap(plan.skipped, 15)]
    return "\n".join(lines)


register(Tool(
    name="unzip_file",
    summary="Extract a .zip into a new folder safely (no paths outside, no symlinks, never overwrites).",
    keywords="解壓縮 解開 打開壓縮檔 unzip extract decompress zip 壓縮檔",
    parameters={
        "type": "object",
        "properties": {
            "archive": {"type": "string", "description": "要解開的 .zip 檔（相對於工作區）"},
            "destination": {"type": "string",
                            "description": "解到哪個資料夾；不填就是和壓縮檔同名的新資料夾"},
        },
        "required": ["archive"],
    },
    kind="change", destructive=False, windows_only=False,
    run=unzip_run, preview=unzip_preview,
))
