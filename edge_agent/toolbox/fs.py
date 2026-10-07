"""Shared file handling: walking, globbing, names, moves. One owner for the rules.

The rules every tool keeps live here so no tool can drift: links are never
followed (a link may point out of the workspace), the owner's own files are
never changed, a new name never replaces an existing file, and lists in the
results are capped so the owner still reads them.
"""

from __future__ import annotations

import errno
import fnmatch
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from edge_agent.toolbox import ToolError, is_inside, resolve_inside

# The owner's own files: settings, secrets, the character's state.
PROTECTED_NAMES = {"settings.json", "personas.json", "character_state.json"}
PROTECTED_NOTE = "使用者自己的設定檔，工具不碰"
GLOB_CHARS = "*?["
# Windows Explorer's own bookkeeping files, hidden or not.
SYSTEM_NAMES = {"desktop.ini", "thumbs.db", "$recycle.bin"}


def is_protected(path: Path | str) -> bool:
    name = Path(path).name.casefold()
    return name in PROTECTED_NAMES or name == ".env" or name.startswith(".env.")


def is_hidden(path: Path) -> bool:
    if path.name.startswith(".") or path.name.casefold() in SYSTEM_NAMES:
        return True
    try:
        attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def human_size(size: float) -> str:
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
    raise AssertionError


def human_count(n: int) -> str:
    return f"{n:,}"


def cap(lines: list[str], limit: int = 40) -> list[str]:
    if len(lines) <= limit:
        return lines
    return [*lines[:limit], f"…還有 {len(lines) - limit} 個"]


def shown(root: Path, path: Path) -> str:
    """How a path is written in results: relative to the workspace."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    return "工作區根目錄" if str(rel) == "." else str(rel)


def key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


# ---------------------------------------------------------------- walking


@dataclass
class Listing:
    files: list[Path] = field(default_factory=list)
    dirs: list[Path] = field(default_factory=list)  # every folder walked into, below the top
    links: list[Path] = field(default_factory=list)  # links and junctions, not followed
    unreadable: list[Path] = field(default_factory=list)


def list_files(folder: Path, *, recursive: bool,
               prune: Callable[[Path], bool] = lambda _: False) -> Listing:
    """Files under folder in a stable order (each folder's files, then its subfolders)."""
    listing = Listing()
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            listing.unreadable.append(current)
            continue
        subdirs = []
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink() or entry.is_junction():
                    listing.links.append(path)
                elif entry.is_dir(follow_symlinks=False):
                    if recursive and not prune(path):
                        subdirs.append(path)
                elif entry.is_file(follow_symlinks=False):
                    listing.files.append(path)
            except OSError:
                listing.unreadable.append(path)
        listing.dirs.extend(subdirs)
        stack.extend(reversed(subdirs))
    return listing


def prune_hidden(path: Path) -> bool:
    return is_hidden(path)


def name_matcher(pattern: str | None) -> Callable[[Path, str], bool]:
    """Case-insensitive file-name patterns, several joined by ; , or |.

    A pattern with a slash is matched against the path relative to the
    searched folder instead of the bare name.
    """
    parts = [p.strip().replace("\\", "/").casefold()
             for p in re.split(r"[;,|]", pattern or "") if p.strip()]

    def match(path: Path, relative: str) -> bool:
        if not parts:
            return True
        name, rel = path.name.casefold(), relative.replace("\\", "/").casefold()
        return any(fnmatch.fnmatchcase(rel if "/" in p else name, p) for p in parts)

    return match


def expand_paths(root: Path, items: Iterable[str]) -> tuple[list[Path], list[str]]:
    """Files and folders named by paths or glob patterns, deduplicated, links skipped.

    A path outside the workspace refuses the whole call; a missing path or a
    pattern without matches is only noted, so the owner sees it in the result.
    """
    found: list[Path] = []
    seen: set[str] = set()
    notes: list[str] = []
    for item in items:
        text = item.strip().strip('"').strip("'")
        if any(c in text for c in GLOB_CHARS):
            parts = Path(text.replace("\\", "/")).parts
            if ".." in parts:
                raise ToolError(f"樣式「{text}」不能有 ..")
            split = next(i for i, p in enumerate(parts) if any(c in p for c in GLOB_CHARS))
            base = resolve_inside(root, str(Path(*parts[:split])) if split else ".", must_exist=False)
            matches = sorted(base.glob("/".join(parts[split:]))) if base.is_dir() else []
            if not matches:
                notes.append(f"「{text}」沒有符合的檔案")
        else:
            lexical = Path(os.path.abspath(text if Path(text).is_absolute() else root / text))
            if not os.path.lexists(lexical):
                resolve_inside(root, text, must_exist=False)  # outside the workspace refuses first
                notes.append(f"找不到「{text}」")
                continue
            matches = [lexical]
        for match in matches:
            if is_link(match):
                notes.append(f"「{shown(root, match)}」是捷徑／連結，不跟進")
                continue
            real = Path(os.path.realpath(match))
            if not is_inside(real, root):
                raise ToolError(f"「{match}」在工作區外面，不能用")
            if key(real) not in seen:
                seen.add(key(real))
                found.append(real)
    return found, notes


# ---------------------------------------------------------------- names and moves


def free_name(path: Path, taken: set[str], *, style: str = " ({})", is_dir: bool = False) -> Path:
    """path, or the first `name (2).ext`, `name (3).ext`… that exists nowhere and is not planned."""
    stem, suffix = (path.name, "") if is_dir else (path.stem, path.suffix)
    candidate, n = path, 1
    # Planned names collide ignoring case on every system, as they would on Windows.
    while os.path.lexists(candidate) or key(candidate).casefold() in taken:
        n += 1
        candidate = path.with_name(f"{stem}{style.format(n)}{suffix}")
    taken.add(key(candidate).casefold())
    return candidate


def move_no_clobber(src: Path, dst: Path) -> None:
    """Move src to dst; never replace anything already at dst."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "目的地已經有同名的檔案", str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(src, dst)  # another drive: copy, then remove the source
