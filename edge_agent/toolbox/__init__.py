"""Ready-made tools for everyday chores, so a small model only fills parameters.

Gemma E4B fails most chores when it writes PowerShell itself (backups,
tidying downloads, renaming photos, Big5 text). Here a program does the work
and the model picks a tool and its parameters. So every tool is deterministic
and conservative: paths stay inside the workspace, nothing is deleted,
nothing existing is overwritten (except convert_text_encoding, which verifies
before replacing), the owner's own files are never touched, and every change
tool can show the owner exactly what it would do (preview and run share one
plan). Results are precise Traditional Chinese the owner can act on.

`python -m edge_agent.toolbox describe|run|preview` is how the Agent calls them.
"""

from __future__ import annotations

import copy
import errno
import functools
import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW: no console flashing up
WINDOWS_ONLY = "這個工具只能在 Windows 上用；這台電腦不是 Windows，所以什麼都沒做。"
MAX_OUTPUT = 6000
KINDS = ("read", "change", "run")


@dataclass(frozen=True)
class Tool:
    name: str  # snake_case, e.g. "organize_folder"
    summary: str  # one English line: what it does (shown after search)
    keywords: str  # extra search words, English and Traditional Chinese
    parameters: dict  # JSON Schema object; every property has a "description"
    kind: str  # "read", "change" (files in the workspace) or "run" (executes programs)
    destructive: bool  # overwrites or moves existing user data
    windows_only: bool
    run: Callable[[dict, Path], str]  # params, workspace root -> result text
    preview: Callable[[dict, Path], str] | None  # what run would change, changing nothing


REGISTRY: dict[str, Tool] = {}


class ToolError(Exception):
    """Why the tool refused or failed, in words the owner can act on."""


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(path))


def is_inside(path: Path, root: Path) -> bool:
    """path is root or below it (both already real paths)."""
    a, b = _key(path), _key(root)
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:  # different drives on Windows
        return False


def resolve_inside(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    """The real path of `rel` (relative to root, or absolute), refused if outside.

    realpath resolves every symlink and junction, so a link that points out of
    the workspace is caught here like a plain `..`.
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ToolError("路徑是空的")
    text = rel.strip().strip('"').strip("'").strip()
    base = Path(os.path.realpath(root))
    given = Path(text)
    real = Path(os.path.realpath(given if given.is_absolute() else base / given))
    if not is_inside(real, base):
        raise ToolError(f"「{text}」在工作區外面，不能用（工作區是 {base}）")
    if must_exist and not real.exists():
        raise ToolError(f"找不到「{text}」（在工作區 {base} 裡）")
    return real


# ---------------------------------------------------------------- parameters

_TRUE = {"true", "yes", "y", "1", "是", "對", "要"}
_FALSE = {"false", "no", "n", "0", "否", "不", "不要"}


def _coerce(name: str, spec: dict, value: object) -> object:
    """Accept what a small model usually means (\"20\" for 20, \"true\" for True)."""
    kind = spec.get("type")
    bad = ToolError(f"參數 {name} 的值不對：{value!r}（{spec['description']}）")
    if kind == "string":
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise bad
        value = str(value)
        if "enum" in spec:
            match = [e for e in spec["enum"] if e.casefold() == value.strip().casefold()]
            if not match:
                raise ToolError(f"參數 {name} 只能是：{'、'.join(spec['enum'])}（收到 {value!r}）")
            value = match[0]
        return value
    if kind in ("integer", "number"):
        if isinstance(value, bool):
            raise bad
        try:
            number = float(value.strip().replace(",", "")) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            raise bad from None
        if kind == "integer":
            if not number.is_integer():
                raise bad
            number = int(number)
        low, high = spec.get("minimum"), spec.get("maximum")
        if (low is not None and number < low) or (high is not None and number > high):
            raise ToolError(f"參數 {name} 要在 {low} 到 {high} 之間（收到 {value!r}）")
        return number
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise bad
    if kind == "array":
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items:
            raise bad
        return [_coerce(name, {"description": spec["description"], **spec["items"]}, v) for v in items]
    raise bad


def check_params(schema: dict, params: object) -> dict:
    """Validate against the tool's schema, fill defaults; unknown names are refused.

    Refusing an unknown name (say `recursive` for `include_subfolders`) makes
    the model retry with the right one instead of silently getting the default.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ToolError("參數要是一個 JSON 物件，例如 {\"folder\": \"下載\"}")
    props: dict = schema.get("properties", {})
    unknown = sorted(set(params) - set(props))
    if unknown:
        known = "、".join(props) or "（沒有參數）"
        raise ToolError(f"不認得的參數：{'、'.join(unknown)}。這個工具的參數有：{known}")
    out: dict = {}
    for name, spec in props.items():
        value = params.get(name)
        if value is None or (isinstance(value, str) and not value.strip()) or value == []:
            if name in schema.get("required", ()):
                raise ToolError(f"缺少參數 {name}：{spec['description']}")
            out[name] = copy.deepcopy(spec.get("default"))
            continue
        out[name] = _coerce(name, spec, value)
    return out


# ---------------------------------------------------------------- output

def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 200)
    cut = cut if cut > 0 else limit - 200
    return text[:cut] + f"\n…（輸出太長，後面省略了 {len(text) - cut} 個字）"


_ERRNO_TEXT = {
    errno.EACCES: "沒有權限",
    errno.EPERM: "沒有權限",
    errno.ENOENT: "找不到",
    errno.ENOSPC: "磁碟空間不足",
    errno.EEXIST: "已經存在",
    errno.EROFS: "唯讀的磁碟",
    errno.ENAMETOOLONG: "名稱太長",
}


def oserror_text(exc: OSError) -> str:
    """One short Chinese reason for a failed file operation."""
    if getattr(exc, "winerror", None) in (32, 33):
        return "檔案正被其他程式使用"
    reason = _ERRNO_TEXT.get(exc.errno or 0) or exc.strerror or str(exc)
    return reason


def register(tool: Tool) -> Tool:
    """Add a tool whose run/preview check parameters and keep the output small."""
    if tool.kind not in KINDS:
        raise ValueError(f"{tool.name}: kind {tool.kind!r}")
    for prop, spec in tool.parameters.get("properties", {}).items():
        if not spec.get("description"):
            raise ValueError(f"{tool.name}.{prop} has no description")
    guarded = replace(tool, run=_guard(tool, tool.run),
                      preview=_guard(tool, tool.preview) if tool.preview else None)
    REGISTRY[tool.name] = guarded
    return guarded


def _guard(tool: Tool, fn: Callable[[dict, Path], str]) -> Callable[[dict, Path], str]:
    @functools.wraps(fn)
    def call(params: dict, root: Path) -> str:
        if tool.windows_only and not IS_WINDOWS:
            return WINDOWS_ONLY
        checked = check_params(tool.parameters, params)
        base = Path(os.path.realpath(root))
        if not base.is_dir():
            raise ToolError(f"工作區不存在：{base}")
        try:
            return clip(fn(checked, base))
        except OSError as exc:  # per-item errors are reported by the tools themselves
            where = f"「{exc.filename}」" if exc.filename else ""
            raise ToolError(f"沒辦法完成：{where}{oserror_text(exc)}") from exc

    return call


# Each module registers its tools on import (after the names above exist).
for _module in ("files", "photos", "text", "system", "dev", "calc"):
    importlib.import_module(f"edge_agent.toolbox.{_module}")
