"""Text files: fix encodings (Big5 to UTF-8), merge CSVs, summarise logs.

Encodings are only ever accepted on a strict, error-free decode; a file
nothing decodes cleanly is reported and left as it was. A conversion replaces
the file only after the new bytes were written next to it and read back to
the identical text.
"""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from edge_agent.toolbox import Tool, ToolError, oserror_text, register, resolve_inside
from edge_agent.toolbox.fs import (
    PROTECTED_NOTE,
    cap,
    expand_paths,
    is_hidden,
    is_protected,
    list_files,
    name_matcher,
    prune_hidden,
    shown,
)

# Tried in this order after UTF-8: Taiwan first, then the neighbours.
AUTO_CANDIDATES = ("cp950", "gb18030", "shift_jis")
LABELS = {
    "utf-8": "UTF-8", "utf-8-sig": "UTF-8（有 BOM）", "utf-16": "UTF-16", "cp950": "Big5（cp950）",
    "big5": "Big5", "big5hkscs": "Big5-HKSCS", "gb18030": "簡體中文 GB18030", "gbk": "簡體中文 GBK",
    "gb2312": "簡體中文 GB2312", "shift_jis": "日文 Shift-JIS", "cp932": "日文 cp932", "euc_jp": "日文 EUC-JP",
}
MAX_TEXT = 100 * 1024 * 1024


def label(codec: str) -> str:
    try:
        name = codecs.lookup(codec).name
    except LookupError:
        name = codec
    return LABELS.get(name, LABELS.get(codec, codec))


def _codec(name: str, param: str) -> str:
    try:
        info = codecs.lookup(name.strip())
    except LookupError:
        raise ToolError(f"{param} 不認得「{name}」這個編碼（例如 utf-8、big5、cp950、gb18030）") from None
    if not getattr(info, "_is_text_encoding", True):
        raise ToolError(f"{param}「{name}」不是文字編碼")
    return info.name


def detect(data: bytes) -> str | None:
    """The first encoding that decodes all of data without a single error, or None."""
    boms = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))
    for bom, codec in boms:
        if data.startswith(bom):
            try:
                data.decode(codec)
                return codec
            except UnicodeDecodeError:
                return None
    candidates = ("utf-8",) if b"\x00" in data else ("utf-8", *AUTO_CANDIDATES)  # NUL: binary, not text
    for codec in candidates:
        try:
            data.decode(codec)
            return codec
        except UnicodeDecodeError:
            pass
    return None


def _sample(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:40] + ("…" if len(flat) > 40 else "")


# ---------------------------------------------------------------- convert_text_encoding


@dataclass
class Decision:
    path: Path
    action: str  # "convert", "same" (already right) or "skip" (cannot, left unchanged)
    note: str
    new: bytes = b""
    text: str = ""
    decode_as: str = ""


def _decide(path: Path, source: str, target: str, add_bom: bool) -> Decision:
    """What to do with one file; the one function both preview and run use."""
    if is_protected(path):
        return Decision(path, "skip", PROTECTED_NOTE)
    data = path.read_bytes()
    if not data:
        return Decision(path, "skip", "空檔案，沒動")
    if len(data) > MAX_TEXT:
        return Decision(path, "skip", "超過 100 MB，不當成文字檔處理，沒動")
    if source == "auto":
        found = detect(data)
        if found is None:
            return Decision(path, "skip", "認不出編碼（試過 UTF-8、Big5、GB18030、Shift-JIS，都有解不開的位元組；"
                                          "也可能不是文字檔），沒動")
    else:
        found = "utf-8-sig" if source == "utf-8" and data.startswith(codecs.BOM_UTF8) else source
        try:
            data.decode(found)
        except UnicodeDecodeError as exc:
            return Decision(path, "skip", f"用 {label(source)} 解不開（第 {exc.start} 個位元組），沒動")
    text = data.decode(found)
    if target in ("utf-8", "utf-8-sig"):
        bom = add_bom or target == "utf-8-sig"
        if found == "utf-8-sig":
            return Decision(path, "same", "已經是 UTF-8（有 BOM），沒動")
        if found == "utf-8" and not bom:
            return Decision(path, "same", "已經是 UTF-8" + ("（只有英數字）" if data.isascii() else "") + "，沒動")
        new, decode_as, to = codecs.BOM_UTF8 * bom + text.encode("utf-8"), "utf-8-sig", "UTF-8" + "（有 BOM）" * bom
    else:
        if codecs.lookup(found).name == target:
            return Decision(path, "same", f"已經是 {label(target)}，沒動")
        if data.isascii():
            return Decision(path, "same", f"只有英數字，{label(target)} 也是一樣的內容，沒動")
        try:
            new = text.encode(target)
        except UnicodeEncodeError as exc:
            return Decision(path, "skip", f"有 {label(target)} 沒有的字（「{text[exc.start]}」），沒動")
        decode_as, to = target, label(target)
    if new.decode(decode_as) != text:
        return Decision(path, "skip", "轉換後讀回來的內容不一樣，沒動")
    return Decision(path, "convert", f"{label(found)} → {to}；內容開頭「{_sample(text)}」", new, text, decode_as)


def _replace_verified(path: Path, decision: Decision) -> None:
    """Write next to the file, read it back, then swap it in atomically."""
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".轉碼中")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(decision.new)
            f.flush()
            os.fsync(f.fileno())
        back = tmp.read_bytes()
        if back != decision.new or back.decode(decision.decode_as) != decision.text:
            raise ToolError("寫入後讀回來的內容不一樣")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # our own temporary file
        raise
    os.chmod(path, mode)
    if path.read_bytes() != decision.new:
        raise ToolError("替換後讀回來的內容不一樣")


def _encoding_targets(params: dict, root: Path) -> tuple[Path, list[Path], list[str]]:
    target = resolve_inside(root, params["path"])
    if target.is_file():
        return target, [target], []
    match = name_matcher(params["pattern"])
    listing = list_files(target, recursive=params["include_subfolders"], prune=prune_hidden)
    files = [p for p in listing.files if match(p, p.relative_to(target).as_posix()) and not is_hidden(p)]
    notes = [f"捷徑／連結 {len(listing.links)} 個不動"] if listing.links else []
    return target, files, notes


def _encoding_options(params: dict) -> tuple[str, str, bool]:
    source = "auto" if params["from_encoding"].strip().casefold() == "auto" else _codec(params["from_encoding"],
                                                                                       "from_encoding")
    return source, _codec(params["to_encoding"], "to_encoding"), params["add_bom"]


def _tally(decisions: list[Decision]) -> str:
    counts = Counter(d.action for d in decisions)
    return f"要轉換 {counts['convert']} 個、已經是目標編碼 {counts['same']} 個、不能轉而沒動 {counts['skip']} 個"


def encoding_preview(params: dict, root: Path) -> str:
    source, target, bom = _encoding_options(params)
    where, files, notes = _encoding_targets(params, root)
    if not files:
        return f"「{shown(root, where)}」裡沒有符合「{params['pattern']}」的檔案。"
    decisions = [_decide(p, source, target, bom) for p in files]
    order = {"convert": 0, "skip": 1, "same": 2}
    lines = [f"預計處理 {len(files)} 個檔案（還沒有做）：{_tally(decisions)}",
             *cap([f"- {shown(root, d.path)}：{d.note}" for d in sorted(decisions, key=lambda d: order[d.action])]),
             *notes]
    if any(d.action == "convert" for d in decisions):
        lines.append("請看「內容開頭」是不是正常的字；如果是亂碼，表示自動判斷錯了，請用 from_encoding 指定。")
    return "\n".join(lines)


def encoding_run(params: dict, root: Path) -> str:
    source, target, bom = _encoding_options(params)
    where, files, notes = _encoding_targets(params, root)
    if not files:
        return f"「{shown(root, where)}」裡沒有符合「{params['pattern']}」的檔案，什麼都沒動。"
    done, failed, same, skipped = [], [], [], []
    for path in files:
        try:
            decision = _decide(path, source, target, bom)
            if decision.action == "convert":
                _replace_verified(path, decision)
                done.append(f"- {shown(root, path)}：已從 {decision.note}")
            elif decision.action == "same":
                same.append(f"- {shown(root, path)}：{decision.note}")
            else:
                skipped.append(f"- {shown(root, path)}：{decision.note}")
        except (OSError, ToolError) as exc:
            reason = oserror_text(exc) if isinstance(exc, OSError) else str(exc)
            failed.append(f"- {shown(root, path)}：{reason}，沒動")
    lines = [f"處理了 {len(files)} 個檔案：轉換完成 {len(done)} 個（每個都讀回來確認過）、"
             f"已經是目標編碼 {len(same)} 個、沒動 {len(skipped) + len(failed)} 個。"]
    for title, group, limit in (("失敗的：", failed, 15), ("轉換的：", done, 30),
                                ("不能轉的：", skipped, 15), ("本來就對的：", same, 10)):
        if group:
            lines += [title, *cap(group, limit)]
    return "\n".join([*lines, *notes])


register(Tool(
    name="convert_text_encoding",
    summary="Convert text files to UTF-8 (or another encoding), auto-detecting Big5/GB18030/Shift-JIS; verified.",
    keywords="編碼 轉碼 亂碼 Big5 UTF-8 BOM 文字檔 txt 轉成 encoding convert charset mojibake garbled text "
             "cp950 簡體 日文",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "一個檔案，或資料夾（資料夾就處理裡面符合 pattern 的檔案）"},
            "pattern": {"type": "string", "default": "*.txt",
                        "description": "資料夾裡要處理的檔名，例如「*.txt」或「*.txt;*.csv」"},
            "include_subfolders": {"type": "boolean", "default": False,
                                   "description": "true 就連子資料夾裡的檔案也處理"},
            "from_encoding": {"type": "string", "default": "auto",
                              "description": "原本的編碼；auto 自動判斷（UTF-8 不動，其餘依序試 Big5、GB18030、Shift-JIS）"},
            "to_encoding": {"type": "string", "default": "utf-8", "description": "要轉成的編碼，預設 utf-8"},
            "add_bom": {"type": "boolean", "default": False,
                        "description": "轉成 UTF-8 時加上 BOM（舊版記事本、Excel 開 CSV 需要）"},
        },
        "required": ["path"],
    },
    kind="change", destructive=True, windows_only=False,
    run=encoding_run, preview=encoding_preview,
))


# ---------------------------------------------------------------- merge_csv


@dataclass
class CsvFile:
    path: Path
    encoding: str
    delimiter: str
    keys: list[tuple[str, int]]
    rows: list[list[str]]
    extra_rows: int = 0


@dataclass
class MergePlan:
    output: Path
    files: list[CsvFile]
    columns: list[tuple[str, int]]
    names: dict[tuple[str, int], str]
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(f.rows) for f in self.files)


def _read_csv(path: Path, encoding: str, root: Path) -> CsvFile:
    data = path.read_bytes()
    if encoding == "auto":
        tried = ("utf-16",) if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else ("utf-8-sig", "cp950")
    else:
        tried = ("utf-8-sig",) if encoding == "utf-8" else (encoding,)
    for codec in tried:
        try:
            text = data.decode(codec)
            break
        except UnicodeDecodeError:
            pass
    else:
        raise ToolError(f"「{shown(root, path)}」的編碼認不出來（試過 {'、'.join(label(c) for c in tried)}），"
                        "沒有合併任何檔案；可以用 encoding 指定")
    first = re.sub(r'"[^"]*"', "", text.split("\n", 1)[0])
    counts = {d: first.count(d) for d in (",", "\t", ";")}
    delimiter = max(counts, key=lambda d: (counts[d], d == ","))
    rows = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter) if any(c.strip() for c in r)]
    seen: Counter = Counter()
    keys = []
    for cell in rows[0] if rows else []:
        name = cell.strip()
        seen[name] += 1
        keys.append((name, seen[name]))
    return CsvFile(path, "UTF-8" if codec == "utf-8-sig" else label(codec), delimiter, keys, rows[1:])


def _merge_plan(params: dict, root: Path) -> MergePlan:
    output = resolve_inside(root, params["output"], must_exist=False)
    if not output.suffix:
        output = output.with_name(output.name + ".csv")
    if os.path.lexists(output):
        raise ToolError(f"「{shown(root, output)}」已經存在，不覆蓋；請換一個 output 名稱")
    if is_protected(output):
        raise ToolError(f"「{output.name}」是{PROTECTED_NOTE}")
    items, notes = expand_paths(root, params["files"])
    paths: list[Path] = []
    for item in items:
        if item.is_dir():
            found = [p for p in list_files(item, recursive=False).files if p.suffix.casefold() == ".csv"]
            notes.append(f"資料夾「{shown(root, item)}」：用裡面的 {len(found)} 個 .csv")
            paths += found
        elif item.suffix.casefold() in (".xlsx", ".xls", ".xlsm", ".ods"):
            raise ToolError(f"「{shown(root, item)}」是 Excel 活頁簿，不是 CSV；要先在 Excel 另存成 CSV")
        else:
            paths.append(item)
    paths = [p for p in dict.fromkeys(paths) if p != output]
    if not paths:
        raise ToolError("沒有找到要合併的 CSV 檔。" + " ".join(notes))
    encoding = "auto" if params["encoding"].strip().casefold() == "auto" else _codec(params["encoding"], "encoding")
    files = [_read_csv(p, encoding, root) for p in paths]
    columns: list[tuple[str, int]] = []
    names: dict[tuple[str, int], str] = {}
    for f in files:
        for k in f.keys:
            if k not in names:
                columns.append(k)
                names[k] = k[0]
        width = len(f.keys)
        for row in f.rows:
            if len(row) > width:
                f.extra_rows += 1
                for i in range(width, len(row)):
                    k = ("", 10_000 + i)
                    if k not in names:
                        columns.append(k)
                        names[k] = f"（沒有標題的第 {i + 1} 欄）"
    plan = MergePlan(output, files, columns, names, notes)
    first = files[0].keys
    if all(f.keys == first for f in files):
        plan.notes.append(f"每個檔案的標題都一樣（{len(first)} 欄），合併後只寫一次。")
    elif all(sorted(f.keys) == sorted(first) for f in files):
        plan.notes.append("每個檔案的欄位相同但順序不同，已經按欄名對齊。")
    else:
        plan.notes.append(f"標題不完全一樣：合併後有 {len(columns)} 欄（所有出現過的欄，依第一次出現的順序），"
                          "某個檔案沒有的欄位留空。")
        for f in files:
            missing = [names[k] or "（空白欄名）" for k in columns if k not in f.keys and k[0]]
            if missing:
                plan.notes.append(f"- {shown(root, f.path)} 沒有：{'、'.join(missing[:12])}"
                                  + (f"…等 {len(missing)} 欄" if len(missing) > 12 else ""))
    for f in files:
        if f.extra_rows:
            plan.notes.append(f"- {shown(root, f.path)} 有 {f.extra_rows} 列的格子比標題多，多的放在「沒有標題」的欄。")
    return plan


def _merge_lines(plan: MergePlan, root: Path) -> list[str]:
    def sep(d: str) -> str:
        return "" if d == "," else f"，用 {'Tab' if d == chr(9) else d} 分隔"
    return cap([f"- {shown(root, f.path)}：{len(f.rows):,} 列（{f.encoding}{sep(f.delimiter)}）" for f in plan.files])


def merge_preview(params: dict, root: Path) -> str:
    plan = _merge_plan(params, root)
    return "\n".join([f"預計把 {len(plan.files)} 個 CSV 合併成「{shown(root, plan.output)}」（還沒有做），"
                      f"共 {plan.total:,} 列資料（不含標題）：", *_merge_lines(plan, root), *plan.notes,
                      "輸出是 UTF-8 加 BOM、逗號分隔（Excel 直接開不會亂碼）；原本的檔案不會被改動。"])


def merge_run(params: dict, root: Path) -> str:
    plan = _merge_plan(params, root)
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    with open(plan.output, "x", encoding="utf-8-sig", newline="") as out:  # x: never replace a file
        writer = csv.writer(out)
        writer.writerow([plan.names[k] for k in plan.columns])
        for f in plan.files:
            for row in f.rows:
                cells = dict(zip(f.keys, row, strict=False))
                cells.update({("", 10_000 + i): v for i, v in enumerate(row) if i >= len(f.keys)})
                writer.writerow([cells.get(k, "") for k in plan.columns])
    with open(plan.output, encoding="utf-8-sig", newline="") as back:
        written = sum(1 for _ in csv.reader(back)) - 1
    check = "讀回來確認列數相符" if written == plan.total else f"讀回來是 {written:,} 列，和預期的 {plan.total:,} 列不符！"
    return "\n".join([f"已合併 {len(plan.files)} 個 CSV 成「{shown(root, plan.output)}」：共 {plan.total:,} 列資料"
                      f"（不含標題），{len(plan.columns)} 欄；{check}。", *_merge_lines(plan, root), *plan.notes,
                      "輸出是 UTF-8 加 BOM、逗號分隔，Excel 可以直接開。"])


register(Tool(
    name="merge_csv",
    summary="Merge several CSV files into one new CSV (header once, columns aligned by name, Excel-friendly UTF-8).",
    keywords="合併 CSV 報表 表格 串接 合在一起 Excel merge combine concatenate csv files 匯總 彙整",
    parameters={
        "type": "object",
        "properties": {
            "files": {"type": "array", "items": {"type": "string"},
                      "description": "要合併的 CSV 檔或資料夾，可以用萬用字元，例如 [\"報表/*.csv\"]；依順序合併"},
            "output": {"type": "string", "description": "合併後的新檔名，例如「合併.csv」；已經存在就不做"},
            "encoding": {"type": "string", "default": "auto",
                         "description": "來源檔的編碼；auto 會先試 UTF-8 再試 Big5"},
        },
        "required": ["files", "output"],
    },
    kind="change", destructive=False, windows_only=False,
    run=merge_run, preview=merge_preview,
))


# ---------------------------------------------------------------- log_summary

_LEVEL = re.compile(r"(?<![A-Za-z0-9_])(FATAL|CRITICAL|ERROR|ERR|WARNING|WARN|INFO|DEBUG)(?![A-Za-z0-9_])", re.I)
_GROUP = {"fatal": "error", "critical": "error", "error": "error", "err": "error",
          "warning": "warn", "warn": "warn", "info": "info", "debug": "debug"}
_NORMALIZE = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"),
     "<時間>"),
    (re.compile(r"\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?"), "<時間>"),
    (re.compile(r"\b0[xX][0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"\d+"), "<n>"),
)


def normalize(message: str) -> str:
    for pattern, token in _NORMALIZE:
        message = pattern.sub(token, message)
    return " ".join(message.split())[:200]


@dataclass
class Group:
    count: int = 0
    first: str = ""
    last: str = ""
    example: str = ""


def _lines(path: Path):
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        with open(path, encoding="utf-16", errors="replace") as f:
            yield from f
        return
    with open(path, "rb") as f:
        for raw in f:
            for codec in ("utf-8", "cp950"):
                try:
                    yield raw.decode(codec)
                    break
                except UnicodeDecodeError:
                    pass
            else:
                yield raw.decode("utf-8", errors="replace")


def _is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(8192)
    return b"\x00" in head and not head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))


def log_run(params: dict, root: Path) -> str:
    target = resolve_inside(root, params["path"])
    notes: list[str] = []
    if target.is_file():
        files = [target]
    else:
        match = name_matcher(params["pattern"])
        listing = list_files(target, recursive=True, prune=prune_hidden)
        files = [p for p in listing.files if match(p, p.relative_to(target).as_posix())]
        if not files:
            return f"「{shown(root, target)}」（含子資料夾）裡沒有符合「{params['pattern']}」的檔案。"
    levels: Counter = Counter()
    fatal = 0
    groups: dict[str, dict[str, Group]] = {"error": {}, "warn": {}}
    total = 0
    read: list[Path] = []
    for path in files:
        try:
            if _is_binary(path):
                notes.append(f"- {shown(root, path)}：看起來是二進位檔（可能壓縮過），略過")
                continue
            where = f"{shown(root, path)} " if len(files) > 1 else ""
            for number, line in enumerate(_lines(path), 1):
                total += 1
                m = _LEVEL.search(line)
                if not m:
                    levels["none"] += 1
                    continue
                word = m.group(1).casefold()
                level = _GROUP[word]
                levels[level] += 1
                fatal += word in ("fatal", "critical")
                if level not in groups:
                    continue
                message = line[m.end():].strip().lstrip(":]|-)>\t ").strip() or line.strip()
                group = groups[level].setdefault(normalize(message), Group())
                group.count += 1
                group.last = f"{where}第 {number:,} 行"
                if group.count == 1:
                    group.first, group.example = group.last, message[:150]
            read.append(path)
        except OSError as exc:
            notes.append(f"- {shown(root, path)}：{oserror_text(exc)}，略過")
    if not read:
        return "\n".join(["沒有讀到任何記錄檔。", *notes])
    head = f"「{shown(root, read[0])}」" if len(read) == 1 else f"{len(read)} 個記錄檔（「{shown(root, target)}」裡）"
    lines = [f"{head}：共 {total:,} 行",
             f"等級：錯誤 {levels['error']:,}" + (f"（其中 FATAL／CRITICAL {fatal:,}）" if fatal else "")
             + f"、警告 {levels['warn']:,}、INFO {levels['info']:,}、DEBUG {levels['debug']:,}、"
               f"沒有等級 {levels['none']:,}"]
    for level, title, top in (("error", "錯誤", 15), ("warn", "警告", 5)):
        ranked = sorted(groups[level].items(), key=lambda kv: -kv[1].count)
        if not ranked:
            continue
        lines.append(f"不同的{title}訊息 {len(ranked)} 種（數字、時間、十六進位、UUID 不同也算同一種），"
                     f"最多的 {min(top, len(ranked))} 種：")
        for n, (pattern, g) in enumerate(ranked[:top], 1):
            span = g.first if g.count == 1 else f"{g.first} 到 {g.last}"
            lines.append(f"{n:>2}. {g.count:,} 次，{span}")
            lines.append(f"    {pattern[:150]}")
            if g.example != pattern[:150]:
                lines.append(f"    例：{g.example}")
    if not levels["error"] and not levels["warn"]:
        lines.append("沒有錯誤或警告。")
    if len(read) > 1:
        lines.append("讀了：" + "、".join(shown(root, p) for p in read[:20]) + ("…" if len(read) > 20 else ""))
    return "\n".join([*lines, *cap(notes, 10)])


register(Tool(
    name="log_summary",
    summary="Summarise a log file (or a folder of logs): counts per level and the most frequent distinct errors.",
    keywords="記錄檔 日誌 log 錯誤 警告 統計 分析 摘要 error warning summary analyze logs 出了什麼錯 哪裡壞了",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "記錄檔，或放記錄檔的資料夾（含子資料夾）"},
            "pattern": {"type": "string", "default": "*.log;*.log.*",
                        "description": "資料夾裡要讀的檔名，預設「*.log;*.log.*」"},
        },
        "required": ["path"],
    },
    kind="read", destructive=False, windows_only=False,
    run=log_run, preview=None,
))
