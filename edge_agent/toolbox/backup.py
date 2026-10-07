"""backup_folder: a new, timestamped copy of a folder or file, or a zip of it, verified.

One plan serves preview and run, so the owner approves exactly what happens.
The backup never replaces anything that exists and the original is not
touched; the result compares the backup's file count and bytes with the
source's.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, oserror_text, register, resolve_inside
from edge_agent.toolbox.fs import (
    PROTECTED_NOTE,
    cap,
    free_name,
    human_count,
    human_size,
    is_protected,
    list_files,
    move_no_clobber,
    shown,
)


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
    lines = [f"來源：「{shown(root, plan.source)}」，{human_count(len(plan.files))} 個檔案，{human_size(plan.total)}",
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
    lines.append(f"驗證：備份裡有 {human_count(got_count)} 個檔案、{human_size(got_size)}"
                 f"（{got_size:,} 位元組）；來源是 {human_count(want_count)} 個、{want_size:,} 位元組"
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
