"""Moving files inside the workspace: organize_folder sorts, archive_files puts away.

Each tool builds one plan; preview prints it and run executes it, so the
owner approves exactly what happens. Moves never replace an existing file
(`name (2).ext` instead), and nothing is ever deleted: archive_files is the
way to get things out of sight.
"""

from __future__ import annotations

import re
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
    is_hidden,
    is_protected,
    key,
    list_files,
    move_no_clobber,
    prune_hidden,
    shown,
)

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
