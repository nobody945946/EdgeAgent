"""git_overview: a repository's status, log, diffs, one commit or branches, in Chinese.

git runs with fixed safe options (no pager, no colour, real UTF-8 paths, no
optional locks) and read-only subcommands only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from edge_agent.toolbox import NO_WINDOW, Tool, ToolError, register, resolve_inside
from edge_agent.toolbox.fs import cap, shown


def _git(repo: Path, *args: str, timeout: float = 60) -> tuple[int, str, str]:
    exe = shutil.which("git")
    if not exe:
        raise ToolError("這台電腦沒有 git")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}
    try:
        done = subprocess.run([exe, "--no-pager", "-c", "core.quotepath=off", "-c", "color.ui=never",
                               "-C", str(repo), *args], capture_output=True, timeout=timeout, env=env,
                              check=False, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ToolError(f"git 超過 {timeout:.0f} 秒沒有回應") from None
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _check_ref(ref: str) -> str:
    if ref.startswith("-") or re.search(r"\s|[\x00-\x1f]", ref):
        raise ToolError(f"ref「{ref}」不是合法的版本名稱（例如 HEAD、HEAD~1、main、abc1234、main..feature）")
    return ref


X_LABEL = {"M": "已暫存的修改", "A": "已暫存的新增", "D": "已暫存的刪除", "R": "已暫存的改名", "C": "已暫存的複製",
           "T": "已暫存的類型變更"}
Y_LABEL = {"M": "修改（未暫存）", "D": "刪除（未暫存）", "T": "類型變更（未暫存）"}
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _branch_text(line: str) -> str:
    if line.startswith("No commits yet on "):
        return f"分支：{line[len('No commits yet on '):]}（還沒有任何提交）"
    if line.startswith("HEAD (no branch)"):
        return "目前不在任何分支上（detached HEAD）"
    m = re.match(r"(.+?)(?:\.\.\.(\S+))?(?: \[(.+)\])?$", line)
    if not m:
        return f"分支：{line}"
    name, upstream, track = m.groups()
    text = f"分支：{name}" + (f"，追蹤 {upstream}" if upstream else "，沒有追蹤遠端分支")
    if track:
        track = track.replace("ahead", "領先").replace("behind", "落後").replace("gone", "遠端分支已經不在")
        text += f"（{track}）"
    return text


def git_status_text(out: str) -> str:
    """porcelain v1 -z --branch output, said in Chinese."""
    tokens = out.split("\0")
    head = tokens[0][3:] if tokens and tokens[0].startswith("## ") else ""
    entries: list[tuple[str, str]] = []
    i = 1 if head else 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if len(token) < 4:
            continue
        xy, path = token[:2], token[3:]
        if xy[0] in "RC" and i < len(tokens):
            path = f"{tokens[i]} → {path}"
            i += 1
        entries.append((xy, path))
    lines = [_branch_text(head)] if head else []
    if not entries:
        return "\n".join([*lines, "工作目錄是乾淨的：沒有修改，也沒有未追蹤的檔案。"])
    counts = {"已修改": 0, "新增": 0, "已刪除": 0, "改名": 0, "未追蹤": 0, "衝突": 0}
    staged = unstaged = 0
    listed = []
    for xy, path in entries:
        if xy == "??":
            counts["未追蹤"] += 1
            listed.append(f"- 未追蹤  {path}")
            continue
        if xy in CONFLICTS:
            counts["衝突"] += 1
            listed.append(f"- 衝突（要先解決）  {path}")
            continue
        x, y = xy
        counts["已修改"] += x in "MT" or y in "MT"
        counts["新增"] += x == "A"
        counts["已刪除"] += x == "D" or y == "D"
        counts["改名"] += x in "RC"
        staged += x != " "
        unstaged += y != " "
        labels = [X_LABEL[x]] if x in X_LABEL else []
        labels += [Y_LABEL[y]] if y in Y_LABEL else []
        listed.append(f"- {'、'.join(labels) or xy}  {path}")
    summary = "、".join(f"{k} {v}" for k, v in counts.items() if v)
    lines.append(f"有變動的檔案 {len(entries)} 個：{summary}（已暫存 {staged}、未暫存 {unstaged}）")
    return "\n".join([*lines, *cap(listed)])


def _capped_patch(text: str, max_lines: int = 200, budget: int = 3800) -> list[str]:
    lines = text.splitlines()
    kept, used = [], 0
    for line in lines[:max_lines]:
        line = line if len(line) <= 200 else line[:200] + "…"
        if used + len(line) > budget:
            break
        kept.append(line)
        used += len(line) + 1
    if len(kept) < len(lines):
        kept.append(f"（差異太長，只列前 {len(kept)} 行；共 {len(lines)} 行）")
    return kept


def git_run(params: dict, root: Path) -> str:
    repo = resolve_inside(root, params["repo"])
    if not repo.is_dir():
        raise ToolError(f"「{params['repo']}」不是資料夾")
    code, top, err = _git(repo, "rev-parse", "--show-toplevel")
    if code != 0:
        raise ToolError(f"「{shown(root, repo)}」不是 git 儲存庫（{_first_line(err)}）")
    what, limit = params["what"], params["limit"]
    ref = _check_ref(params["ref"]) if params["ref"] else None
    spec: list[str] = []
    if params["path"]:
        target = resolve_inside(root, params["path"], must_exist=False)
        spec = ["--", os.path.relpath(target, repo)]
    where = f"「{shown(root, repo)}」"

    def run(*args: str) -> str:
        code, out, err = _git(repo, *args)
        if code != 0:
            raise ToolError(f"git {args[0]} 失敗：{_first_line(err) or _first_line(out)}")
        return out

    if what == "status":
        return f"{where}的 git 狀態：\n" + git_status_text(run("status", "--porcelain=v1", "-z", "--branch", *spec))
    if what == "log":
        out = run("log", f"-n{limit}", "--date=format-local:%Y-%m-%d %H:%M",
                  "--pretty=format:%h%x09%ad%x09%an%x09%s", *([ref] if ref else []), *spec)
        rows = ["  ".join(line.split("\t")) for line in out.splitlines() if line]
        if not rows:
            return f"{where}沒有符合的提交。"
        return "\n".join([f"{where}最近 {len(rows)} 個提交（新的在上；縮寫、時間、作者、訊息）：", *rows])
    if what in ("diff", "staged_diff"):
        base = ["diff", *(["--cached"] if what == "staged_diff" else []), *([ref] if ref else [])]
        stat = run(*base, "--stat=100", *spec).rstrip()
        if not stat:
            which = "已暫存（staged）的" if what == "staged_diff" else "還沒暫存的"
            return f"{where}沒有{which}差異" + (f"（和 {ref} 比）" if ref else "") + "。"
        patch = run(*base, *spec)
        stat_lines = stat.splitlines()
        stat_lines = [*stat_lines[:-1][:40], *(["…"] if len(stat_lines) > 41 else []), stat_lines[-1]]
        title = "已暫存的差異" if what == "staged_diff" else "差異" + (f"（和 {ref} 比）" if ref else "（還沒暫存的）")
        return "\n".join([f"{where}的{title}：", "改了哪些檔案：", *stat_lines, "", *_capped_patch(patch)])
    if what == "show":
        target = ref or "HEAD"
        head = run("show", "--stat=100", "--date=format-local:%Y-%m-%d %H:%M",
                   "--format=提交 %H%n作者 %an <%ae>%n時間 %ad%n%n%B", target, *spec).rstrip()
        patch = run("show", "--format=", "--patch", target, *spec)
        head_lines = head.splitlines()
        return "\n".join([*head_lines[:60], *(["…"] if len(head_lines) > 60 else []), "",
                          *_capped_patch(patch, budget=3000)])
    out = run("for-each-ref", "--sort=-committerdate",
              "--format=%(HEAD)%09%(refname)%09%(refname:short)%09%(objectname:short)%09%(committerdate:short)"
              "%09%(upstream:short)%09%(upstream:track)%09%(contents:subject)", "refs/heads", "refs/remotes")
    rows = []
    for line in out.splitlines():
        current, full, name, sha, day, upstream, track, subject = (line.split("\t") + [""] * 8)[:8]
        if full.endswith("/HEAD"):
            continue
        kind = "本地" if full.startswith("refs/heads/") else "遠端"
        extra = f"，追蹤 {upstream}{(' ' + track) if track else ''}" if upstream else ""
        rows.append(f"{'* ' if current == '*' else '  '}{name}（{kind}）{sha} {day}{extra}  {subject[:80]}")
    if not rows:
        return f"{where}還沒有任何分支（沒有提交）。"
    return "\n".join([f"{where}的分支（* 是目前的分支，最近有提交的在上）：", *cap(rows, limit)])


register(Tool(
    name="git_overview",
    summary="Read-only git: status, recent log, diff, staged diff, one commit, or branches, summarised.",
    keywords="git 版本 狀態 改了什麼 差異 提交 紀錄 分支 commit log diff status branch staged show 版控",
    parameters={
        "type": "object",
        "properties": {
            "repo": {"type": "string", "default": ".", "description": "git 儲存庫的資料夾，預設是工作區"},
            "what": {"type": "string", "enum": ["status", "log", "diff", "staged_diff", "show", "branches"],
                     "description": "status 狀態、log 最近的提交、diff 還沒暫存的差異、staged_diff 已暫存的差異、"
                                    "show 某個提交的內容、branches 分支"},
            "ref": {"type": "string",
                    "description": "版本（log／diff／show 用），例如 HEAD~1、main、abc1234；show 不填就是 HEAD"},
            "path": {"type": "string", "description": "只看這個檔案或資料夾"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200,
                      "description": "log 列幾個提交、branches 列幾個分支"},
        },
        "required": ["what"],
    },
    kind="read", destructive=False, windows_only=False,
    run=git_run, preview=None,
))
