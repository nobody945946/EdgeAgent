"""Read-only looks at a folder: its largest files, and files with identical content.

Nothing is moved or deleted; find_duplicate_files points to archive_files
for getting copies out of sight, which needs the owner's consent.
"""

from __future__ import annotations

import hashlib
import heapq
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, register, resolve_inside
from edge_agent.toolbox.fs import (
    cap,
    human_count,
    human_size,
    list_files,
    name_matcher,
    shown,
)

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
    lines = [f"「{shown(root, folder)}」（含子資料夾）：{human_count(len(sizes))} 個檔案，總共 {human_size(total)}"]
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
        lines = [f"{where}：檢查了 {human_count(scanned)} 個檔案，沒有內容完全相同的檔案。"]
    else:
        groups.sort(key=lambda g: (-(g[0] * (len(g[1]) - 1)), str(g[1][0])))
        extra = sum(size * (len(paths) - 1) for size, paths in groups)
        lines = [(f"{where}：檢查了 {human_count(scanned)} 個檔案，找到 {len(groups)} 組內容完全相同（SHA-256 一樣）的檔案，"
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
