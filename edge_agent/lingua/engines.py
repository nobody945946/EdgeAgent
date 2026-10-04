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
    $hits = @()
    if ($c.inputs) { $hits = @($c.inputs | ForEach-Object { $r.IsMatch([string]$_) }) }
    [pscustomobject]@{ ok = $true; groups = $groups; matches = $hits }
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
    return { ok: true, count: m.length - 1, names: m.groups ? Object.keys(m.groups) : [],
             matches: (c.inputs || []).map(s => r.test(s)) };
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
        out.append({"ok": True,
                    "groups": [[n, names.get(n, "")] for n in range(1, compiled.groups + 1)],
                    "matches": [bool(compiled.search(s)) for s in case.get("inputs", [])]})
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
