"""Folder chores: backup, tidy, archive, zip and unzip, find big or repeated files.

Change tools build one plan; preview prints it and run executes it, so the
owner approves exactly what happens. Moves never replace an existing file
(`name (2).ext` instead), and nothing is ever deleted: archive_files is the
way to get things out of sight.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
import shutil
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, oserror_text, register, resolve_inside
from edge_agent.toolbox.fs import (
    PROTECTED_NOTE,
    cap,
    expand_paths,
    free_name,
    human_size,
    is_hidden,
    is_protected,
    key,
    list_files,
    move_no_clobber,
    name_matcher,
    prune_hidden,
    shown,
)


def _count(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------- backup_folder


@dataclass
class BackupPlan:
    source: Path
    dest: Path
    as_zip: bool
    files: list[tuple[Path, int]]
    dirs: list[Path]
    links: list[Path]
    unreadable: list[Path]

    @property
    def total(self) -> int:
        return sum(size for _, size in self.files)


def _backup_plan(params: dict, root: Path) -> BackupPlan:
    source = resolve_inside(root, params["source"])
    as_zip = params["as_zip"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = source.name if source.is_dir() else source.stem
    name = f"{base}-備份-{stamp}" + (".zip" if as_zip else ("" if source.is_dir() else source.suffix))
    if params["destination"]:
        dest = resolve_inside(root, params["destination"], must_exist=False)
        if dest.is_dir():
            dest = dest / name  # an existing folder: the backup goes inside it
        elif as_zip and dest.suffix.casefold() != ".zip":
            dest = dest.with_name(dest.name + ".zip")
    elif source == root:
        raise ToolError("要備份整個工作區時，請用 destination 指定工作區裡的一個資料夾"
                        "（預設會放在來源旁邊，也就是工作區外面）")
    else:
        dest = source.parent / name
    if os.path.lexists(dest):
        raise ToolError(f"「{shown(root, dest)}」已經存在，不覆蓋；請換一個 destination")
    if is_protected(dest):
        raise ToolError(f"「{dest.name}」是{PROTECTED_NOTE}")
    if source.is_dir():
        listing = list_files(source, recursive=True)
        files = []
        for path in listing.files:
            try:
                files.append((path, path.stat().st_size))
            except OSError:
                listing.unreadable.append(path)
        return BackupPlan(source, dest, as_zip, files, listing.dirs, listing.links, listing.unreadable)
    return BackupPlan(source, dest, as_zip, [(source, source.stat().st_size)], [], [], [])


def _backup_head(plan: BackupPlan, root: Path) -> list[str]:
    how = "壓縮成 zip" if plan.as_zip else ("複製整個資料夾" if plan.source.is_dir() else "複製檔案")
    lines = [f"來源：「{shown(root, plan.source)}」，{_count(len(plan.files))} 個檔案，{human_size(plan.total)}",
             f"備份到：「{shown(root, plan.dest)}」（{how}）"]
    if plan.links:
        lines.append(f"捷徑／連結 {len(plan.links)} 個不跟進、不備份：")
        lines += [f"- {shown(root, p)}" for p in cap(plan.links, 10)]
    if plan.unreadable:
        lines.append(f"讀不到的 {len(plan.unreadable)} 個，不會在備份裡：")
        lines += [f"- {shown(root, p)}" for p in cap(plan.unreadable, 10)]
    return lines


def backup_preview(params: dict, root: Path) -> str:
    plan = _backup_plan(params, root)
    return "\n".join(["預計備份（還沒有做）：", *_backup_head(plan, root),
                      "名稱裡的時間以實際執行的時間為準；原本的檔案不會被改動。"])


def backup_run(params: dict, root: Path) -> str:
    plan = _backup_plan(params, root)
    base = plan.source.parent
    failed: list[str] = []
    if plan.as_zip:
        tmp = free_name(plan.dest.with_name(plan.dest.name + ".partial"), set())
        try:
            with zipfile.ZipFile(tmp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                tops = [plan.source] if plan.source.is_dir() else []
                for folder in [*tops, *plan.dirs]:
                    zf.mkdir(folder.relative_to(base).as_posix())
                for path, _ in plan.files:
                    try:
                        zf.write(path, path.relative_to(base).as_posix())
                    except OSError as exc:
                        failed.append(f"- {shown(root, path)}：{oserror_text(exc)}")
            with zipfile.ZipFile(tmp) as zf:
                bad = zf.testzip()
                entries = [i for i in zf.infolist() if not i.is_dir()]
            got_count, got_size = len(entries), sum(i.file_size for i in entries)
            if bad is not None:
                raise ToolError(f"壓縮檔檢查失敗（{bad} 損壞），沒有完成備份")
            move_no_clobber(tmp, plan.dest)
        except BaseException:
            tmp.unlink(missing_ok=True)  # our own half-written file, never the user's
            raise
    else:
        if plan.source.is_dir():
            plan.dest.mkdir(parents=True)
            for folder in plan.dirs:
                (plan.dest / folder.relative_to(plan.source)).mkdir(parents=True, exist_ok=True)
        for path, _ in plan.files:
            target = plan.dest if path == plan.source else plan.dest / path.relative_to(plan.source)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            except OSError as exc:
                failed.append(f"- {shown(root, path)}：{oserror_text(exc)}")
        if plan.dest.is_dir():
            got = list_files(plan.dest, recursive=True).files
            got_count, got_size = len(got), sum(p.stat().st_size for p in got)
        else:
            got_count, got_size = 1, plan.dest.stat().st_size
    want_count, want_size = len(plan.files), plan.total
    ok = not failed and got_count == want_count and got_size == want_size
    lines = [("備份完成。" if ok else "備份沒有完全成功。"), *_backup_head(plan, root)]
    if plan.as_zip:
        lines.append(f"壓縮檔大小：{human_size(plan.dest.stat().st_size)}")
    lines.append(f"驗證：備份裡有 {_count(got_count)} 個檔案、{human_size(got_size)}"
                 f"（{got_size:,} 位元組）；來源是 {_count(want_count)} 個、{want_size:,} 位元組"
                 + ("，相符。" if ok else "，不相符！"))
    if failed:
        lines.append(f"沒能備份的 {len(failed)} 個：")
        lines += cap(failed, 15)
    return "\n".join(lines)


register(Tool(
    name="backup_folder",
    summary="Copy a folder or file to a new timestamped backup (optionally a zip) and verify it.",
    keywords="備份 複製 副本 存一份 backup copy snapshot zip 壓縮 資料夾 folder 保存",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "要備份的資料夾或檔案（相對於工作區）"},
            "destination": {"type": "string", "description":
                            "備份放哪裡；不填就放在來源旁邊，名稱是「名稱-備份-年月日-時分秒」；"
                            "填已經存在的資料夾就放在它裡面"},
            "as_zip": {"type": "boolean", "default": False, "description": "true 就壓縮成一個 .zip 檔"},
        },
        "required": ["source"],
    },
    kind="change", destructive=False, windows_only=False,
    run=backup_run, preview=backup_preview,
))


# ---------------------------------------------------------------- organize_folder

CATEGORIES: dict[str, str] = {
    "圖片": "jpg jpeg jpe jfif png gif bmp webp heic heif tif tiff svg ico avif psd ai raw dng cr2 cr3 nef arw orf rw2",
    "影片": "mp4 m4v mov avi mkv wmv flv webm mpg mpeg 3gp m2ts mts vob",
    "音樂": "mp3 wav flac aac m4a ogg oga opus wma aiff aif mid midi",
    "文件": "doc docx dot dotx odt rtf txt md epub mobi pages",
    "試算表": "xls xlsx xlsm xlsb ods csv tsv numbers",
    "簡報": "ppt pptx pps ppsx pot potx odp key",
    "PDF": "pdf",
    "壓縮檔": "zip rar 7z tar gz tgz bz2 xz zst cab",
    "安裝程式": "exe msi msix msixbundle appx appxbundle apk dmg pkg deb rpm iso img",
    "程式碼": "py pyw ipynb js mjs cjs ts tsx jsx java c h cpp hpp cc cs go rs rb php sh bash ps1 psm1 "
             "bat cmd vbs html htm css scss json xml yaml yml toml ini sql lua kt swift",
}
OTHER = "其他"
EXT_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts.split()}
CATEGORY_NAMES = {*CATEGORIES, OTHER}
# Folder names people ask for, matched to the standard kinds by the words in
# them ("影音" takes both video and audio; spreadsheets, slides and PDFs go to
# a documents folder when there is none of their own). What fits no named
# folder goes to the one meaning "other", or stays where it is.
NAMED_KINDS: dict[str, tuple[str, ...]] = {
    "圖片": ("圖片", "照片", "相片", "影像", "圖檔", "image", "photo", "picture", "pic"),
    "影片": ("影片", "影音", "視訊", "錄影", "電影", "video", "movie"),
    "音樂": ("音樂", "影音", "音訊", "聲音", "錄音", "歌", "audio", "music", "sound"),
    "文件": ("文件", "文書", "文字", "doc"),
    "試算表": ("試算表", "表格", "excel", "sheet"),
    "簡報": ("簡報", "投影片", "ppt", "slide"),
    "PDF": ("pdf",),
    "壓縮檔": ("壓縮", "zip", "archive"),
    "安裝程式": ("安裝", "installer", "setup"),
    "程式碼": ("程式碼", "原始碼", "code", "source"),
}
FALLBACK_KIND = {"試算表": "文件", "簡報": "文件", "PDF": "文件"}
OTHER_WORDS = ("其他", "其它", "雜項", "其餘", "other", "misc")


def named_folders(value: list[str]) -> list[str]:
    """The folder names asked for; a small model may pass them as one string."""
    names: list[str] = []
    for item in value:
        for name in re.split(r"[,、，;；/|]+", item):
            name = name.strip().strip("「」\"'")
            if name and name not in names:
                names.append(name)
    return names


def kind_folders(names: list[str]) -> tuple[dict[str, str], str | None]:
    """Standard kind -> the asked-for folder it goes to, and the "other" folder."""
    def find(words: tuple[str, ...]) -> str | None:
        return next((n for n in names if any(w in n.casefold() for w in words)), None)

    other = find(OTHER_WORDS)
    mapping: dict[str, str] = {}
    for kind, words in NAMED_KINDS.items():
        found = find(words) or (find(NAMED_KINDS[FALLBACK_KIND[kind]]) if kind in FALLBACK_KIND else None)
        if found and found != other:
            mapping[kind] = found
    return mapping, other


PARTIAL_SUFFIXES = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
NO_EXT = "沒有副檔名"


def _category_dir(name: str, by: str, named: list[str] | None = None) -> bool:
    """A folder this mode itself creates, so its files are already sorted."""
    if by == "type":
        return name in (named or CATEGORY_NAMES)
    if by == "month":
        return re.fullmatch(r"\d{4}-\d{2}", name) is not None
    return name == NO_EXT or (name.isascii() and name.isalnum() and name == name.upper()
                              and not name.isdigit())


def _bucket(path: Path, by: str, named: tuple[dict[str, str], str | None] | None = None) -> str | None:
    ext = path.suffix[1:].casefold()
    if by == "type" and named is not None:
        mapping, other = named
        return mapping.get(EXT_CATEGORY.get(ext, OTHER)) or other
    if by == "type":
        return EXT_CATEGORY.get(ext, OTHER)
    if by == "extension":
        return ext.upper() if ext else NO_EXT
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")


def _skip_reason(path: Path) -> str | None:
    if is_protected(path):
        return PROTECTED_NOTE
    if is_hidden(path):
        return "隱藏或系統檔"
    if path.suffix.casefold() in PARTIAL_SUFFIXES:
        return "還沒下載完或暫存的檔案"
    if path.name.startswith("~$"):
        return "Office 開著檔案時的暫存檔"
    return None


@dataclass
class MovePlan:
    moves: list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _organize_plan(params: dict, root: Path) -> tuple[Path, MovePlan]:
    folder = resolve_inside(root, params["folder"])
    if not folder.is_dir():
        raise ToolError(f"「{params['folder']}」不是資料夾")
    by = params["by"]
    recursive = params["include_subfolders"]
    plan = MovePlan()
    pruned: list[str] = []
    names = named_folders(params["folders"] or []) if by == "type" else []
    named = kind_folders(names) if names else None

    def prune(path: Path) -> bool:
        if prune_hidden(path):
            return True
        if _category_dir(path.name, by, names):
            pruned.append(path.name)
            return True
        return False

    listing = list_files(folder, recursive=recursive, prune=prune)
    taken: set[str] = set()
    for path in listing.files:
        reason = _skip_reason(path)
        if reason:
            plan.skipped.append(f"- {shown(root, path)}：{reason}")
            continue
        try:
            bucket = _bucket(path, by, named)
        except OSError as exc:
            plan.skipped.append(f"- {shown(root, path)}：{oserror_text(exc)}")
            continue
        if bucket is None:
            plan.skipped.append(f"- {shown(root, path)}：指定的資料夾裡沒有適合它的（沒有「其他」）")
            continue
        target_dir = folder / bucket
        if key(path.parent) == key(target_dir):
            continue
        plan.moves.append((path, free_name(target_dir / path.name, taken)))
    if pruned:
        plan.notes.append(f"已經是分類資料夾、裡面的檔案不動：{'、'.join(sorted(set(pruned)))}")
    if not recursive:
        subdirs = [p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()]
        if subdirs:
            plan.notes.append(f"子資料夾 {len(subdirs)} 個不動（include_subfolders 是 false，只整理最上層的檔案）")
    if listing.links:
        plan.notes.append(f"捷徑／連結 {len(listing.links)} 個不動")
    return folder, plan


def _move_lines(plan: MovePlan, root: Path) -> list[str]:
    return [f"- {shown(root, src)} → {shown(root, dst)}" for src, dst in plan.moves]


def _by_folder(plan: MovePlan, folder: Path) -> str:
    counts: dict[str, int] = defaultdict(int)
    for _, dst in plan.moves:
        counts[dst.parent.relative_to(folder).as_posix() if dst.parent != folder else "."] += 1
    return "、".join(f"{name} {n} 個" for name, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def _move_tail(plan: MovePlan) -> list[str]:
    lines = [*plan.notes]
    if plan.skipped:
        lines.append(f"略過 {len(plan.skipped)} 個：")
        lines += cap(plan.skipped, 15)
    return lines


def organize_preview(params: dict, root: Path) -> str:
    folder, plan = _organize_plan(params, root)
    if not plan.moves:
        return "\n".join([f"「{shown(root, folder)}」裡沒有需要整理的檔案。", *_move_tail(plan)])
    return "\n".join([f"預計在「{shown(root, folder)}」裡移動 {len(plan.moves)} 個檔案（還沒有做）：",
                      _by_folder(plan, folder), *cap(_move_lines(plan, root)), *_move_tail(plan),
                      "不會刪除任何檔案；同名的會在名稱後面加 (2)、(3)。"])


def _execute_moves(plan: MovePlan, root: Path) -> tuple[list[str], list[str]]:
    done, failed = [], []
    for src, dst in plan.moves:
        try:
            move_no_clobber(src, dst)
            done.append(f"- {shown(root, src)} → {shown(root, dst)}")
        except OSError as exc:
            failed.append(f"- {shown(root, src)}：{oserror_text(exc)}，沒有移動")
    return done, failed


def organize_run(params: dict, root: Path) -> str:
    folder, plan = _organize_plan(params, root)
    if not plan.moves:
        return "\n".join([f"「{shown(root, folder)}」裡沒有需要整理的檔案，什麼都沒動。", *_move_tail(plan)])
    done, failed = _execute_moves(plan, root)
    lines = [f"已在「{shown(root, folder)}」裡移動 {len(done)} 個檔案。" + (f"{len(failed)} 個失敗。" if failed else ""),
             _by_folder(plan, folder)]
    if failed:
        lines += ["沒有移動的：", *cap(failed, 15)]
    lines += [*cap(done), *_move_tail(plan)]
    return "\n".join(lines)


register(Tool(
    name="organize_folder",
    summary="Sort the files of a folder into subfolders by type (圖片, 文件, PDF…), extension or month.",
    keywords="整理 分類 歸類 下載 資料夾 桌面 亂 按類型 依月份 副檔名 sort organize tidy downloads "
             "clean up categorize files by type month extension",
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "要整理的資料夾（相對於工作區），例如「下載」"},
            "by": {"type": "string", "enum": ["type", "extension", "month"], "default": "type",
                   "description": "type：按類型放進 圖片、影片、音樂、文件、試算表、簡報、PDF、壓縮檔、安裝程式、程式碼、其他；"
                                  "extension：按副檔名（JPG、PDF…）；month：按修改月份（2024-05）"},
            "include_subfolders": {"type": "boolean", "default": False,
                                   "description": "true 就連子資料夾裡的檔案也搬到上層的分類資料夾；預設只整理最上層"},
            "folders": {"type": "array", "items": {"type": "string"},
                        "description": "使用者指定的分類資料夾名稱，照他說的寫，例如 [\"圖片\", \"文件\", \"影音\", \"其他\"]；"
                                       "每個檔案放進最符合它類型的那一個。使用者沒指定名稱就不要給"},
        },
        "required": ["folder"],
    },
    kind="change", destructive=True, windows_only=False,
    run=organize_run, preview=organize_preview,
))


# ---------------------------------------------------------------- archive_files


def _archive_plan(params: dict, root: Path) -> tuple[Path, MovePlan]:
    dest = resolve_inside(root, params["destination"] or f"封存/{date.today():%Y%m%d}", must_exist=False)
    if dest.exists() and not dest.is_dir():
        raise ToolError(f"「{shown(root, dest)}」是檔案，不能當封存資料夾")
    items, notes = expand_paths(root, params["paths"])
    plan = MovePlan(notes=list(notes))
    chosen = []
    for item in items:
        reason = None
        if item == root:
            reason = "工作區本身不能封存"
        elif key(dest) == key(item) or item in dest.parents:
            reason = "封存資料夾在它裡面"
        elif dest in item.parents:
            reason = "已經在封存資料夾裡"
        elif ".git" in item.relative_to(root).parts:
            reason = "git 內部的檔案"
        elif is_protected(item):
            reason = PROTECTED_NOTE
        elif item.is_dir():
            inner = next((p for p in list_files(item, recursive=True).files if is_protected(p)), None)
            if inner:
                reason = f"裡面有{PROTECTED_NOTE}（{shown(root, inner)}）"
        if reason:
            plan.skipped.append(f"- {shown(root, item)}：{reason}")
        else:
            chosen.append(item)
    # A file inside a chosen folder moves with the folder.
    chosen = [p for p in chosen if not any(q in p.parents for q in chosen)]
    taken: set[str] = set()
    for item in chosen:
        plan.moves.append((item, free_name(dest / item.relative_to(root), taken, is_dir=item.is_dir())))
    return dest, plan


def archive_preview(params: dict, root: Path) -> str:
    dest, plan = _archive_plan(params, root)
    if not plan.moves:
        return "\n".join(["沒有要封存的東西。", *_move_tail(plan)])
    return "\n".join([(f"預計把 {len(plan.moves)} 個項目移到封存資料夾「{shown(root, dest)}」（還沒有做）："
                      "保留原本的資料夾結構，不刪除任何東西。"),
                      *cap(_move_lines(plan, root)), *_move_tail(plan)])


def archive_run(params: dict, root: Path) -> str:
    dest, plan = _archive_plan(params, root)
    if not plan.moves:
        return "\n".join(["沒有要封存的東西，什麼都沒動。", *_move_tail(plan)])
    done, failed = _execute_moves(plan, root)
    lines = [f"已把 {len(done)} 個項目移到「{shown(root, dest)}」。" + (f"{len(failed)} 個失敗。" if failed else "")
             + "沒有刪除任何東西；要找回就從封存資料夾移回去。"]
    if failed:
        lines += ["沒有移動的：", *cap(failed, 15)]
    lines += [*cap(done), *_move_tail(plan)]
    return "\n".join(lines)


register(Tool(
    name="archive_files",
    summary="Move files or folders (paths or glob patterns) into an archive folder instead of deleting them.",
    keywords="封存 收起來 移走 不要了 清理 刪除 丟掉 歸檔 archive move away instead of delete remove clean up "
             "old files 舊檔案",
    parameters={
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "要封存的檔案或資料夾，可以用萬用字元，例如 [\"舊報告\", \"下載/*.tmp\", \"**/*.bak\"]"},
            "destination": {"type": "string", "description": "封存資料夾；不填就是「封存/今天的年月日」"},
        },
        "required": ["paths"],
    },
    kind="change", destructive=True, windows_only=False,
    run=archive_run, preview=archive_preview,
))


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
    return [f"壓縮檔：「{shown(root, plan.output)}」，{_count(len(plan.entries))} 個檔案，原始大小 {human_size(total)}",
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
    lines = [(f"已建立壓縮檔，裡面有 {_count(count)} 個檔案，壓縮後 {human_size(plan.output.stat().st_size)}；"
             "已檢查過每個檔案都讀得回來。"), *_zip_head(plan, root), *plan.notes]
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
             f"{_count(len(plan.files))} 個檔案，解開後 {human_size(plan.total)}", *cap(_unzip_lines(plan), 30)]
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
            f"{_count(done)} 個檔案，{human_size(plan.total)}。")
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


# ---------------------------------------------------------------- find_large_files


def large_run(params: dict, root: Path) -> str:
    folder = resolve_inside(root, params["folder"])
    if not folder.is_dir():
        raise ToolError(f"「{params['folder']}」不是資料夾")
    listing = list_files(folder, recursive=True)
    sizes = []
    for path in listing.files:
        try:
            sizes.append((path.stat().st_size, path))
        except OSError:
            listing.unreadable.append(path)
    total = sum(size for size, _ in sizes)
    floor = params["min_size_mb"] * 1024 * 1024
    top = heapq.nlargest(params["top"], (s for s in sizes if s[0] >= floor), key=lambda s: s[0])
    lines = [f"「{shown(root, folder)}」（含子資料夾）：{_count(len(sizes))} 個檔案，總共 {human_size(total)}"]
    if not top:
        lines.append(f"沒有 {params['min_size_mb']:g} MB 以上的檔案。")
    else:
        lines.append(f"最大的 {len(top)} 個：")
        lines += [f"{human_size(size):>10}  {shown(root, path)}" for size, path in top]
    if listing.links or listing.unreadable:
        lines.append(f"沒算進去：捷徑／連結 {len(listing.links)} 個、讀不到的 {len(listing.unreadable)} 個")
    return "\n".join(lines)


register(Tool(
    name="find_large_files",
    summary="List the largest files under a folder with sizes, plus the folder's total size.",
    keywords="大檔案 佔空間 空間不夠 容量 磁碟滿了 找出最大 large big files disk space usage size 清空間",
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "要找的資料夾（含子資料夾），例如「.」是整個工作區"},
            "top": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200,
                    "description": "列出幾個最大的檔案"},
            "min_size_mb": {"type": "number", "default": 0, "minimum": 0,
                            "description": "只列出至少這麼大的檔案（MB）"},
        },
        "required": ["folder"],
    },
    kind="read", destructive=False, windows_only=False,
    run=large_run, preview=None,
))


# ---------------------------------------------------------------- find_duplicate_files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_COPY_NAME = re.compile(r" \(\d+\)$|[ _-]?(?:copy|複製|副本|備份|backup)", re.IGNORECASE)


def _original_first(path: Path) -> tuple:
    """Oldest first; on a tie (copies keep the time) the one whose path does not look like a copy."""
    rel = str(path)
    return (path.stat().st_mtime, bool(_COPY_NAME.search(str(path.parent / path.stem))), len(rel), rel)


def duplicates_run(params: dict, root: Path) -> str:
    folder = resolve_inside(root, params["folder"])
    if not folder.is_dir():
        raise ToolError(f"「{params['folder']}」不是資料夾")
    match = name_matcher(params["pattern"])
    listing = list_files(folder, recursive=True)
    by_size: dict[int, list[Path]] = defaultdict(list)
    empty = 0
    for path in listing.files:
        if not match(path, path.relative_to(folder).as_posix()):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            listing.unreadable.append(path)
            continue
        if size == 0:
            empty += 1
        else:
            by_size[size].append(path)
    groups: list[tuple[int, list[Path]]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for path in paths:
            try:
                by_hash[_sha256(path)].append(path)
            except OSError:
                listing.unreadable.append(path)
        groups += [(size, same) for same in by_hash.values() if len(same) > 1]
    scanned = sum(len(p) for p in by_size.values())
    where = f"「{shown(root, folder)}」（含子資料夾" + (f"，只看 {params['pattern']}" if params["pattern"] else "") + "）"
    if not groups:
        lines = [f"{where}：檢查了 {_count(scanned)} 個檔案，沒有內容完全相同的檔案。"]
    else:
        groups.sort(key=lambda g: (-(g[0] * (len(g[1]) - 1)), str(g[1][0])))
        extra = sum(size * (len(paths) - 1) for size, paths in groups)
        lines = [(f"{where}：檢查了 {_count(scanned)} 個檔案，找到 {len(groups)} 組內容完全相同（SHA-256 一樣）的檔案，"
                 f"多出來的副本共佔 {human_size(extra)}。"),
                 "每組最早修改的那個標成「原始」。沒有刪除任何檔案；要移走副本需要主人同意，可以用 archive_files 封存。"]
        for n, (size, paths) in enumerate(groups[:20], 1):
            ordered = sorted(paths, key=_original_first)
            lines.append(f"第 {n} 組：{len(paths)} 個，每個 {human_size(size)}")
            lines += cap([f"  {'原始' if i == 0 else '副本'}  {shown(root, p)}"
                          f"（{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}）"
                          for i, p in enumerate(ordered)], 10)
        if len(groups) > 20:
            lines.append(f"…還有 {len(groups) - 20} 組")
    if empty:
        lines.append(f"空檔案 {empty} 個不比較。")
    if listing.links or listing.unreadable:
        lines.append(f"沒比較：捷徑／連結 {len(listing.links)} 個、讀不到的 {len(listing.unreadable)} 個")
    return "\n".join(lines)


register(Tool(
    name="find_duplicate_files",
    summary="Find files with identical content (same size and SHA-256) under a folder; deletes nothing.",
    keywords="重複 一樣的檔案 重複檔案 副本 相同 duplicate same identical copies 照片重複 清理 省空間",
    parameters={
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "要找的資料夾（含子資料夾）"},
            "pattern": {"type": "string",
                        "description": "只看符合的檔名，例如「*.jpg」或「*.jpg;*.png」；不填就看全部"},
        },
        "required": ["folder"],
    },
    kind="read", destructive=False, windows_only=False,
    run=duplicates_run, preview=None,
))
