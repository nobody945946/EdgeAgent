"""The real regex engines, for checking: Python in process, .NET through
PowerShell, JavaScript through Node. A translation is only as exact as these
checks; when an engine is missing the caller has to say it was not checked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PWSH_SCRIPT = r"""
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$names = @{ i = 'IgnoreCase'; m = 'Multiline'; s = 'Singleline'; x = 'IgnorePatternWhitespace'; n = 'ExplicitCapture' }
$cases = [Console]::In.ReadToEnd() | ConvertFrom-Json
$out = foreach ($c in $cases) {
  $opts = [Text.RegularExpressions.RegexOptions]::None
  foreach ($f in ([string]$c.flags).ToCharArray()) { $opts = $opts -bor [Text.RegularExpressions.RegexOptions]$names[[string]$f] }
  try {
    $r = [regex]::new([string]$c.pattern, $opts)
    $groups = @($r.GetGroupNumbers() | Where-Object { $_ -ne 0 } | ForEach-Object { ,@($_, $r.GroupNameFromNumber($_)) })
    $hits = @(); $found = @()
    if ($c.inputs) {
      foreach ($s in $c.inputs) {
        $m = $r.Match([string]$s); $hits += $m.Success
        $found += ,@($(if ($m.Success) { $m.Value } else { $null }), @($m.Groups | Select-Object -Skip 1 | ForEach-Object { if ($_.Success) { $_.Value } else { $null } }))
      }
    }
    [pscustomobject]@{ ok = $true; groups = $groups; matches = $hits; found = $found }
  } catch {
    $e = $_.Exception; if ($e.InnerException) { $e = $e.InnerException }
    [pscustomobject]@{ ok = $false; error = $e.Message }
  }
}
ConvertTo-Json -InputObject @($out) -Depth 6 -Compress
"""

NODE_SCRIPT = r"""
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = cases.map(c => {
  try {
    const r = new RegExp(c.pattern, c.flags);
    const m = new RegExp('(?:' + c.pattern + ')|', c.flags).exec('');
    const found = (c.inputs || []).map(s => { const x = r.exec(s); return x ? [x[0], x.slice(1).map(v => v ?? null)] : [null, []]; });
    return { ok: true, count: m.length - 1, names: m.groups ? Object.keys(m.groups) : [],
             matches: found.map(f => f[0] !== null), found };
  } catch (e) { return { ok: false, error: e.message }; }
});
process.stdout.write(JSON.stringify(out));
"""

PY_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE, "a": re.ASCII}


def find_pwsh() -> str | None:
    for candidate in (os.environ.get("EDGE_AGENT_PWSH"), shutil.which("pwsh"),
                      str(ROOT / ".runtime/pwsh/pwsh"), shutil.which("powershell")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def find_node() -> str | None:
    for candidate in (os.environ.get("EDGE_AGENT_NODE"), shutil.which("node")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def run_python(cases: list[dict]) -> list[dict]:
    out = []
    for case in cases:
        flags = 0
        for letter in case.get("flags", ""):
            flags |= PY_FLAGS[letter]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                compiled = re.compile(case["pattern"], flags)
        except re.error as exc:
            out.append({"ok": False, "error": str(exc)})
            continue
        names = {number: name for name, number in compiled.groupindex.items()}
        found = []
        for text in case.get("inputs", []):
            m = compiled.search(text)
            found.append([m.group(0), list(m.groups())] if m else [None, []])
        out.append({"ok": True,
                    "groups": [[n, names.get(n, "")] for n in range(1, compiled.groups + 1)],
                    "matches": [f[0] is not None for f in found], "found": found})
    return out


def run_dotnet(cases: list[dict]) -> list[dict] | None:
    pwsh = find_pwsh()
    if pwsh is None:
        return None
    done = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", PWSH_SCRIPT],
                          input=json.dumps(cases), capture_output=True, text=True,
                          encoding="utf-8", timeout=300, check=True)
    results = json.loads(done.stdout)
    for result in results:
        if result["ok"]:
            # GroupNameFromNumber gives the number back for unnamed groups.
            result["groups"] = [[n, "" if name == str(n) else name] for n, name in result["groups"]]
    return results


def run_js(cases: list[dict]) -> list[dict] | None:
    node = find_node()
    if node is None:
        return None
    done = subprocess.run([node, "-e", NODE_SCRIPT], input=json.dumps(cases),
                          capture_output=True, text=True, encoding="utf-8", timeout=300, check=True)
    return json.loads(done.stdout)


RUNNERS = {"python": run_python, "dotnet": run_dotnet, "js": run_js}


# ---------------------------------------------------------------- spreadsheet

CALC_IMAGE = "edgeagent-calc:1"  # bench/calc/Dockerfile: LibreOffice Calc, no network


def find_soffice() -> list[str] | None:
    """A LibreOffice with Calc: one installed here, else the bench image."""
    for candidate in (os.environ.get("EDGE_AGENT_SOFFICE"),
                      r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if candidate and Path(candidate).is_file():
            return [candidate]
    docker = shutil.which("docker")
    if docker and subprocess.run([docker, "image", "inspect", CALC_IMAGE], capture_output=True,
                                 check=False).returncode == 0:
        return [docker, "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                "-e", "HOME=/tmp", "-v", "{folder}:/work", CALC_IMAGE, "soffice",
                "-env:UserInstallation=file:///tmp/lo"]
    return None


# Functions newer than Excel 2010 are stored as _xlfn.NAME in the file, as
# Excel itself writes them; Calc only recognises them that way.
FUTURE_FUNCTIONS = re.compile(
    r"(?<![A-Za-z_.])(IFNA|IFS|SWITCH|CONCAT|TEXTJOIN|XLOOKUP|XMATCH|MAXIFS|MINIFS|FILTER|SORT|UNIQUE|"
    r"SEQUENCE|LET|DAYS|ISOWEEKNUM|RANK\.EQ|STDEV\.S|STDEV\.P)\(")


def _xlsx(path: Path, cells: dict[str, object], formulas: list[str]) -> None:
    """The smallest workbook Calc opens: data cells, then one formula per row of
    column Z (rows 1..n), so results come back in order."""
    import zipfile
    from xml.sax.saxutils import escape

    def cell(ref: str, value: object) -> str:
        if isinstance(value, str) and value.startswith("="):
            return f'<c r="{ref}"><f>{escape(FUTURE_FUNCTIONS.sub(r"_xlfn.\\1(", value[1:]))}</f></c>'
        if isinstance(value, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        if isinstance(value, bool):
            return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    rows: dict[int, list[str]] = {}
    for ref, value in cells.items():
        rows.setdefault(int(re.sub(r"[A-Z]", "", ref)), []).append(cell(ref, value))
    for index, formula in enumerate(formulas, 1):
        text = FUTURE_FUNCTIONS.sub(r"_xlfn.\1(", formula.removeprefix("="))
        rows.setdefault(index, []).append(f'<c r="Z{index}"><f>{escape(text)}</f></c>')

    def col_key(c: str) -> tuple[int, str]:
        letters = re.match(r'<c r="([A-Z]+)', c).group(1)
        return len(letters), letters

    data = "".join(f'<row r="{r}">{"".join(sorted(rows[r], key=col_key))}</row>' for r in sorted(rows))
    ns = "http://schemas.openxmlformats.org"
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("[Content_Types].xml", f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{ns}/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr("_rels/.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{ns}/spreadsheetml/2006/main" xmlns:r="{ns}/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        book.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}/spreadsheetml/2006/main"><sheetData>{data}</sheetData></worksheet>')


def run_calc(cells: dict[str, object], formulas: list[str]) -> list[str] | None:
    """Each formula's value as Calc shows it (CSV text), computed in one
    workbook over the given data cells (keep data out of column Z)."""
    import csv
    import tempfile

    command = find_soffice()
    if command is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        _xlsx(Path(folder) / "f.xlsx", cells, formulas)
        local = "{folder}" not in " ".join(command)
        target = folder if local else "/work"
        args = [a.replace("{folder}", folder) for a in command] + [
            "--headless", "--convert-to", "csv", "--outdir", f"{target}/out", f"{target}/f.xlsx"]
        subprocess.run(args, capture_output=True, text=True, timeout=300, check=True)
        with open(Path(folder) / "out" / "f.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    return [(row[25] if len(row) > 25 else "") for row in rows[:len(formulas)]]


# ---------------------------------------------------------------- databases

PG_IMAGE = "postgres:16"
MSSQL_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
MSSQL_PASSWORD = "Lingua!Check2026"  # a throwaway server with no network, removed after the checks


class Databases:
    """SQLite in process, PostgreSQL and SQL Server in containers with no
    network, for checking the SQL translator. Use as a context manager; the
    containers are removed on exit. A missing image leaves that one out."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}

    def __enter__(self) -> Databases:  # noqa: PYI034
        import time
        import uuid

        docker = shutil.which("docker")
        if docker is None:
            return self
        tag = uuid.uuid4().hex[:8]
        for dialect, image, env in (
                ("postgres", PG_IMAGE, ["-e", "POSTGRES_PASSWORD=x"]),
                ("tsql", MSSQL_IMAGE, ["-e", "ACCEPT_EULA=Y", "-e", f"MSSQL_SA_PASSWORD={MSSQL_PASSWORD}"])):
            if subprocess.run([docker, "image", "inspect", image], capture_output=True, check=False).returncode:
                continue
            name = f"lingua-{dialect}-{tag}"
            subprocess.run([docker, "run", "-d", "--rm", "--name", name, "--network", "none", *env, image],
                           capture_output=True, check=True)
            self.names[dialect] = name
        deadline = time.monotonic() + 180
        try:
            for dialect in list(self.names):
                while True:
                    try:
                        self.run(dialect, "SELECT 1")
                        break
                    except (RuntimeError, subprocess.SubprocessError):
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(2)
        except BaseException:
            self.__exit__()  # never leave a server running
            raise
        return self

    def __exit__(self, *_: object) -> None:  # also called on a failed start
        for name in self.names.values():
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    def available(self) -> list[str]:
        return ["sqlite", *self.names]

    def run(self, dialect: str, sql: str) -> list[list[str]]:
        """Run statements and undo them (each call starts from an empty
        database); the rows they return, values as text ('' for NULL)."""
        if dialect == "sqlite":
            import sqlite3

            connection = sqlite3.connect(":memory:")
            try:
                rows: list = []
                for statement in [s for s in sql.split(";") if s.strip()]:
                    try:
                        rows = connection.execute(statement).fetchall()
                    except sqlite3.Error as exc:
                        raise RuntimeError(str(exc)) from exc
                return [["" if v is None else str(v) for v in row] for row in rows]
            finally:
                connection.close()
        name = self.names[dialect]
        if dialect == "postgres":
            command = ["docker", "exec", name, "psql", "-U", "postgres", "-q", "-At", "-F", "|",
                       "-P", "null=NULL", "-v", "ON_ERROR_STOP=1", "-c", f"BEGIN; {sql}; ROLLBACK"]
        else:
            command = ["docker", "exec", name, "/opt/mssql-tools18/bin/sqlcmd", "-S", "127.0.0.1", "-U", "sa",
                       "-P", MSSQL_PASSWORD, "-C", "-l", "5", "-b", "-h", "-1", "-W", "-s", "|", "-Q",
                       f"SET NOCOUNT ON; BEGIN TRANSACTION; {sql}; ROLLBACK"]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip()[:300])
        lines = done.stdout.splitlines() if dialect == "postgres" else [
            line for line in done.stdout.splitlines() if line.strip()]
        return [["" if v == "NULL" else v for v in line.split("|")] for line in lines]
