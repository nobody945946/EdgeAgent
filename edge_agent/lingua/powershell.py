"""PowerShell commands to plain Traditional Chinese, exactly.

PowerShell itself does the hard part: its parser splits the command line, its
parameter binder decides which value goes to which parameter, and Get-Command
says what each name really is on this machine (`ls` is an alias of
Get-ChildItem on Windows and the ls program on Linux; curl is an alias only
in Windows PowerShell 5.1). Nothing is executed: the script only parses and
looks names up. The PowerShell used is the one the Agent's commands run in
(pwsh, else powershell.exe, as Pi picks it).

The Chinese for each cmdlet and parameter comes from COMMANDS; every entry is
checked against the engine in tests/test_lingua_powershell.py (the command
and parameter exist, and behaviour claims hold in a scratch folder). A name
that is not in the table is shown with what PowerShell itself says, never
with a guess.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from edge_agent.lingua.engines import find_pwsh

ANALYZE = r"""
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$src = [Console]::In.ReadToEnd()
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$tokens, [ref]$errors)
$Lang = 'System.Management.Automation.Language'

function Text($node) { if ($node) { $node.Extent.Text } else { '' } }

function Command($c) {
  $name = $c.GetCommandName()
  $info = $null; $resolved = ''; $type = ''; $targetType = ''; $module = ''; $source = ''
  if ($name) { $info = Get-Command -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1 }
  if ($info) {
    $type = [string]$info.CommandType
    $resolved = $info.Name
    $module = [string]$info.ModuleName
    if ($info.CommandType -eq 'Alias') {
      $target = if ($info.ResolvedCommandName) { [string]$info.ResolvedCommandName } else { [string]$info.Definition }
      $resolved = $target
      $targetCmd = if ($info.ResolvedCommand) { $info.ResolvedCommand } else { Get-Command -Name $target -ErrorAction SilentlyContinue | Select-Object -First 1 }
      if ($targetCmd) { $targetType = [string]$targetCmd.CommandType; $module = [string]$targetCmd.ModuleName }
    }
    if ($info.CommandType -eq 'Application') { $source = [string]$info.Source }
  }
  $bound = @(); $bindErrors = @()
  $kind = $type; if ($targetType) { $kind = $targetType }
  if ($kind -eq 'Cmdlet' -or $kind -eq 'Function') {
    $b = [System.Management.Automation.Language.StaticParameterBinder]::BindCommand($c, $true)
    $metadata = if ($info.CommandType -eq 'Alias') { $targetCmd } else { $info }
    $dynamicMetadata = $false
    # StaticParameterBinder omits provider-specific parameters. Ask the native
    # cmdlet for metadata using only literal arguments, never execute the AST.
    if ($kind -eq 'Cmdlet' -and $module -eq 'Microsoft.PowerShell.Management') {
      $arguments = @(); $literalArguments = $true
      foreach ($e in @($c.CommandElements | Select-Object -Skip 1)) {
        if ($e -is [System.Management.Automation.Language.CommandParameterAst] -and -not $e.Argument) {
          $arguments += '-' + $e.ParameterName
        } elseif ($e -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
          $arguments += $e.Value
        } elseif ($e -is [System.Management.Automation.Language.ConstantExpressionAst]) {
          $arguments += $e.Value
        } else { $literalArguments = $false; break }
      }
      if ($literalArguments) {
        $native = Get-Command -Name ($module + '\' + $resolved) -ArgumentList $arguments -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($native) { $metadata = $native; $dynamicMetadata = $true }
      }
    }
    foreach ($kv in $b.BoundParameters.GetEnumerator()) {
      $p = $kv.Value
      $isSwitch = $false
      if ($p.Parameter) { $isSwitch = [bool]$p.Parameter.SwitchParameter }
      $value = ''; $at = -1
      if ($p.Value -and $p.Value.Extent.Text) { $at = $p.Value.Extent.StartOffset }
      foreach ($e in $c.CommandElements) {
        if ($at -ge 0) { break }
        if ($e -is [System.Management.Automation.Language.CommandParameterAst]) {
          $aliases = @(); if ($p.Parameter) { $aliases = @($p.Parameter.Aliases) }
          if ($kv.Key -like "$($e.ParameterName)*" -or $aliases -contains $e.ParameterName) { $at = $e.Extent.StartOffset }
        }
      }
      if ($p.Value -and -not ($p.Value -is [System.Management.Automation.Language.CommandParameterAst])) { $value = $p.Value.Extent.Text }
      elseif ($p.Value -is [System.Management.Automation.Language.CommandParameterAst] -and $p.Value.Argument) { $value = $p.Value.Argument.Extent.Text }
      $literal = $null
      if ($p.Value -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $literal = $p.Value.Value }
      $bound += [pscustomobject]@{ name = [string]$kv.Key; value = $value; literal = $literal; switch = $isSwitch; at = $at }
    }
    foreach ($kv in $b.BindingExceptions.GetEnumerator()) {
      $ex = $kv.Value.BindingException
      if ($kind -eq 'Cmdlet' -and $ex.ErrorId -in @('AmbiguousParameter', 'NamedParameterNotFound')) {
        $parameters = @($metadata.Parameters.Values | Where-Object { -not $_.IsDynamic -or $dynamicMetadata })
        $matches = @($parameters | Where-Object { $_.Name -eq $kv.Key -or $_.Aliases -contains $kv.Key })
        if ($matches.Count -eq 0) {
          # PowerShell binds static parameter prefixes before provider-specific
          # dynamic prefixes (e.g. Recurse before the filesystem's ReadOnly).
          $matches = @($parameters | Where-Object { -not $_.IsDynamic -and $_.Name.StartsWith($kv.Key, [StringComparison]::OrdinalIgnoreCase) })
          if ($matches.Count -eq 0) { $matches = @($parameters | Where-Object { $_.IsDynamic -and $_.Name.StartsWith($kv.Key, [StringComparison]::OrdinalIgnoreCase) }) }
        }
        if ($matches.Count -eq 1) {
          $parameter = $matches[0]; $elements = @($c.CommandElements)
          for ($index = 1; $index -lt $elements.Count; $index++) {
            $e = $elements[$index]
            if ($e -isnot [System.Management.Automation.Language.CommandParameterAst] -or $e.ParameterName -ne $kv.Key) { continue }
            $isSwitch = $parameter.ParameterType -eq [System.Management.Automation.SwitchParameter]
            $valueNode = $e.Argument
            if (-not $isSwitch -and -not $valueNode -and $index + 1 -lt $elements.Count) { $valueNode = $elements[$index + 1] }
            $literal = $null
            if ($valueNode -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $literal = $valueNode.Value }
            $bound += [pscustomobject]@{ name = $parameter.Name; value = (Text $valueNode); literal = $literal; switch = $isSwitch; at = $e.Extent.StartOffset }
            break
          }
          continue
        }
      }
      $bindErrors += [pscustomobject]@{ name = [string]$kv.Key; message = [string]$ex.Message }
    }
  }
  $redirections = @(foreach ($r in $c.Redirections) {
    if ($r -is [System.Management.Automation.Language.FileRedirectionAst]) {
      [pscustomobject]@{ text = (Text $r); stream = [string]$r.FromStream; to = (Text $r.Location); append = [bool]$r.Append; merge = '' }
    } else {
      [pscustomobject]@{ text = (Text $r); stream = [string]$r.FromStream; to = ''; append = $false; merge = [string]$r.ToStream }
    }
  })
  [pscustomobject]@{
    kind = 'command'; text = (Text $c); name = [string]$name; invocation = [string]$c.InvocationOperator
    resolved = $resolved; type = $type; target_type = $targetType; module = $module; source = $source
    bound = @($bound); binding_errors = @($bindErrors); redirections = @($redirections)
    args = @($c.CommandElements | Select-Object -Skip 1 | ForEach-Object { $_.Extent.Text })
  }
}

function Element($e) {
  if ($e -is [System.Management.Automation.Language.CommandAst]) { return Command $e }
  [pscustomobject]@{ kind = 'expression'; text = (Text $e); type = $e.GetType().Name }
}

function Statement($s) {
  $t = $s.GetType().Name
  if ($t -eq 'PipelineAst') {
    return [pscustomobject]@{ kind = 'pipeline'; text = (Text $s); background = [bool]$s.Background
                               elements = @($s.PipelineElements | ForEach-Object { Element $_ }) }
  }
  if ($t -eq 'PipelineChainAst') {
    return [pscustomobject]@{ kind = 'chain'; text = (Text $s); op = [string]$s.Operator; background = [bool]$s.Background
                               left = (Statement $s.LhsPipelineChain); right = (Statement $s.RhsPipeline) }
  }
  if ($t -eq 'AssignmentStatementAst') {
    return [pscustomobject]@{ kind = 'assignment'; text = (Text $s); target = (Text $s.Left); op = [string]$s.Operator
                               value = (Statement $s.Right) }
  }
  if ($t -eq 'CommandExpressionAst') {
    return [pscustomobject]@{ kind = 'expression'; text = (Text $s); type = $s.Expression.GetType().Name }
  }
  $inner = @($s.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { Command $_ })
  [pscustomobject]@{ kind = 'other'; text = (Text $s); type = $t; commands = $inner }
}

$statements = @()
if ($ast.EndBlock) { $statements = @($ast.EndBlock.Statements | ForEach-Object { Statement $_ }) }
$all = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { Command $_ })
$out = [pscustomobject]@{
  version = $PSVersionTable.PSVersion.ToString(); edition = [string]$PSVersionTable.PSEdition
  windows = [bool]([Environment]::OSVersion.Platform -eq 'Win32NT')
  errors = @($errors | ForEach-Object { [pscustomobject]@{ message = $_.Message; offset = $_.Extent.StartOffset } })
  statements = $statements; all_commands = $all
  other_blocks = [bool]($ast.BeginBlock -or $ast.ProcessBlock -or $ast.ParamBlock)
  simple_literals = @($ast.FindAll({ param($n) $n.GetType().Name -notin @(
    'ScriptBlockAst', 'NamedBlockAst', 'PipelineAst', 'CommandAst',
    'CommandParameterAst', 'StringConstantExpressionAst', 'ConstantExpressionAst'
  ) }, $true)).Count -eq 0
}
ConvertTo-Json -InputObject $out -Depth 12 -Compress
"""


def analyze(script: str, pwsh: str | None = None, cwd: str | None = None, timeout: float = 120) -> dict:
    """What PowerShell itself makes of the script (parsed, never run), in the
    folder the command would run in (relative script names resolve there)."""
    pwsh = pwsh or find_pwsh()
    if pwsh is None:
        raise RuntimeError("找不到 PowerShell，無法精確翻譯")
    done = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ANALYZE],
                          input=script, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, check=False, cwd=cwd)
    if done.returncode != 0 or not done.stdout.strip():
        raise RuntimeError(f"PowerShell 解析失敗：{done.stderr.strip()[:300]}")
    return json.loads(done.stdout)


# ---------------------------------------------------------------- the table
# kinds of effect: delete, overwrite, write, run, network, system, read

@dataclass(frozen=True)
class Cmd:
    text: str
    params: dict[str, str] = field(default_factory=dict)
    effect: str = "read"
    # The parameters naming what the effect lands on (default: the paths).
    targets: tuple[str, ...] = ("Path", "LiteralPath")
    windows_only: bool = False


PATHS = {
    "Path": "對象的路徑（可用萬用字元 * ?）",
    "LiteralPath": "對象的路徑（照字面，不解讀萬用字元）",
}
WHAT_IF = {
    "WhatIf": "只顯示會做什麼，實際上不做",
    "Confirm": "每一項動手前都先詢問",
}
COMMANDS: dict[str, Cmd] = {
    "Get-ChildItem": Cmd("列出資料夾裡的項目（檔案和子資料夾）", {
        **PATHS, "Filter": "只列名稱符合這個樣式的項目（例如 *.log）",
        "Include": "只包含名稱符合這些樣式的項目", "Exclude": "排除名稱符合這些樣式的項目",
        "Recurse": "連所有子資料夾裡的項目一起列出", "Depth": "往下最多幾層子資料夾",
        "Force": "也列出隱藏的項目", "File": "只列檔案", "Directory": "只列資料夾",
        "Name": "只輸出名稱"}),
    "Remove-Item": Cmd("刪除項目（檔案、資料夾等）；檔案直接刪除，不會進資源回收筒", {
        **PATHS, **WHAT_IF, "Recurse": "連資料夾裡的所有內容一起刪除",
        "Force": "連隱藏、唯讀的項目也刪除", "Filter": "只刪除名稱符合這個樣式的項目",
        "Include": "只刪除名稱符合這些樣式的項目", "Exclude": "不刪除名稱符合這些樣式的項目"},
        effect="delete"),
    "Copy-Item": Cmd("複製項目；目的地已有同名檔案時，預設直接覆蓋", {
        **PATHS, **WHAT_IF, "Destination": "複製到哪裡",
        "Recurse": "複製資料夾時連裡面的內容一起複製（沒有這個參數只會建立空資料夾）",
        "Force": "連唯讀的檔案也覆蓋", "Filter": "只複製名稱符合這個樣式的項目",
        "Include": "只複製名稱符合這些樣式的項目", "Exclude": "不複製名稱符合這些樣式的項目",
        "Container": "保留資料夾結構"}, effect="overwrite", targets=("Destination",)),
    "Move-Item": Cmd("移動項目（也可用來改名）", {
        **PATHS, **WHAT_IF, "Destination": "移到哪裡",
        "Force": "目的地已有同名項目時直接覆蓋（沒有這個參數時會出錯不移動）"}, effect="overwrite",
        targets=("Path", "LiteralPath", "Destination")),
    "Rename-Item": Cmd("重新命名項目", {**PATHS, **WHAT_IF, "NewName": "新名稱"}, effect="write"),
    "New-Item": Cmd("建立新的項目（檔案或資料夾）", {
        **WHAT_IF, "Path": "建立在哪裡", "Name": "新項目的名稱", "Value": "新檔案的內容",
        "ItemType": "要建立的種類（File 檔案、Directory 資料夾等）",
        "Force": "已經存在時：檔案會被清空覆蓋；資料夾則沿用不出錯"}, effect="write"),
    "Get-Content": Cmd("讀取檔案內容（預設一行一行讀）", {
        **PATHS, "TotalCount": "只讀前幾行（-Head、-First 也是它）", "Tail": "只讀最後幾行",
        "Raw": "整個檔案當成一整段文字讀入", "Wait": "讀完後繼續等待新加入的內容",
        "Encoding": "用哪種文字編碼讀"}),
    "Set-Content": Cmd("把檔案內容整個換成新內容（檔案不存在就建立）", {
        **PATHS, **WHAT_IF, "Value": "新內容", "Encoding": "用哪種文字編碼寫",
        "NoNewline": "結尾不加換行"}, effect="overwrite"),
    "Add-Content": Cmd("在檔案結尾加上內容（檔案不存在就建立）", {
        **PATHS, **WHAT_IF, "Value": "要加的內容", "Encoding": "用哪種文字編碼寫"}, effect="write"),
    "Clear-Content": Cmd("清空檔案內容（檔案本身保留）", {**PATHS, **WHAT_IF}, effect="delete"),
    "Out-File": Cmd("把前面的輸出寫進檔案（預設覆蓋原內容）", {
        "FilePath": "寫到哪個檔案", "Append": "加在檔案結尾，不覆蓋",
        "NoClobber": "檔案已存在就不覆蓋（改成出錯）", "Encoding": "用哪種文字編碼寫",
        **WHAT_IF}, effect="overwrite", targets=("FilePath",)),
    "Select-String": Cmd("在文字或檔案裡找出符合樣式的行（樣式預設是正規表示式，不分大小寫）", {
        "Pattern": "要找的樣式", **PATHS, "SimpleMatch": "樣式當成一般文字，不是正規表示式",
        "CaseSensitive": "區分大小寫", "NotMatch": "改找不符合的行",
        "Context": "同時列出前後幾行", "List": "每個檔案只列第一個符合的行"}),
    "Where-Object": Cmd("篩選：只留下符合條件的項目", {"FilterScript": "條件"}),
    "ForEach-Object": Cmd("對每一個項目執行一段程式", {"Process": "要執行的程式"}),
    "Sort-Object": Cmd("排序", {"Property": "依哪個屬性排序", "Descending": "由大到小",
                               "Unique": "去掉重複的項目"}),
    "Select-Object": Cmd("選出一部分的項目或屬性", {
        "Property": "只留下這些屬性", "First": "只取前幾個", "Last": "只取最後幾個",
        "Skip": "跳過前幾個", "Unique": "去掉重複的項目", "ExpandProperty": "只取出這個屬性的值"}),
    "Measure-Object": Cmd("計算數量，或總和、平均、最大、最小", {
        "Property": "對哪個屬性計算", "Sum": "總和", "Average": "平均", "Maximum": "最大值",
        "Minimum": "最小值", "Line": "行數", "Word": "字數", "Character": "字元數"}),
    "Get-Process": Cmd("列出正在執行的程式", {"Name": "程式名稱", "Id": "程式編號（PID）"}),
    "Stop-Process": Cmd("強制結束正在執行的程式（未存的資料會遺失）", {
        "Name": "程式名稱", "Id": "程式編號（PID）", **WHAT_IF,
        "Force": "不詢問直接結束，包括不是自己啟動的程式"}, effect="system", targets=("Name", "Id")),
    "Start-Process": Cmd("啟動一個程式", {
        "FilePath": "要啟動的程式或檔案", "ArgumentList": "傳給程式的參數",
        "Verb": "啟動方式（RunAs 是以系統管理員身分執行）", "Wait": "等程式結束才繼續",
        "WindowStyle": "視窗樣式（Hidden 是不顯示視窗）", "NoNewWindow": "在目前的視窗執行"},
        effect="run", targets=("FilePath",)),
    "Invoke-WebRequest": Cmd("從網路取得網頁或下載檔案", {
        "Uri": "網址", "OutFile": "存成這個檔案", "Method": "請求方式（GET、POST 等）",
        "Body": "送出的內容", "Headers": "送出的標頭", "UseBasicParsing": "不用瀏覽器元件解析網頁"},
        effect="network", targets=("Uri",)),
    "Invoke-RestMethod": Cmd("呼叫網路 API，並把回應轉成物件", {
        "Uri": "網址", "Method": "請求方式（GET、POST 等）", "Body": "送出的內容",
        "Headers": "送出的標頭", "OutFile": "存成這個檔案"}, effect="network", targets=("Uri",)),
    "Invoke-Expression": Cmd("把一段文字當成 PowerShell 程式執行（內容是什麼都會照做）", {
        "Command": "要執行的文字"}, effect="run", targets=("Command",)),
    "Set-ExecutionPolicy": Cmd("改變這台電腦允許執行哪些腳本的規則", {
        "ExecutionPolicy": "新規則（Bypass、Unrestricted 都等於不擋任何腳本）",
        "Scope": "套用範圍（CurrentUser 只有自己、LocalMachine 整台電腦）",
        "Force": "不詢問直接改", **WHAT_IF}, effect="system", targets=("ExecutionPolicy",)),
    "Test-Path": Cmd("檢查路徑存不存在（回答 True 或 False）", {
        **PATHS, "PathType": "要檢查的種類（Leaf 檔案、Container 資料夾）"}),
    "Compress-Archive": Cmd("壓縮成 zip 檔", {
        **PATHS, "DestinationPath": "zip 檔存到哪裡", "Force": "已有同名 zip 檔時覆蓋",
        "Update": "更新已有的 zip 檔"}, effect="write", targets=("DestinationPath",)),
    "Expand-Archive": Cmd("解壓縮 zip 檔", {
        **PATHS, "DestinationPath": "解壓到哪裡", "Force": "已有同名檔案時覆蓋"}, effect="overwrite",
        targets=("DestinationPath",)),
    "Write-Output": Cmd("把內容送到輸出（可以接著傳給下一個指令）", {"InputObject": "要輸出的內容"}),
    "Write-Host": Cmd("把文字直接顯示在畫面上（不會傳給下一個指令）", {"Object": "要顯示的內容"}),
    "Set-Location": Cmd("切換目前所在的資料夾", {**PATHS}),
    "Get-Location": Cmd("顯示目前所在的資料夾"),
    "Get-Item": Cmd("取得指定的項目本身（不列出內容）", {**PATHS, "Force": "也包含隱藏的項目"}),
    "Get-FileHash": Cmd("計算檔案的雜湊值（用來比對檔案有沒有被改過）", {
        **PATHS, "Algorithm": "雜湊演算法（預設 SHA256）"}),
    "Start-Sleep": Cmd("暫停一段時間", {"Seconds": "暫停幾秒", "Milliseconds": "暫停幾毫秒"}),
    "Get-Date": Cmd("取得目前的日期時間"),
    "Export-Csv": Cmd("把項目存成 CSV 檔（預設覆蓋原檔）", {
        **PATHS, "NoTypeInformation": "不加型別說明的第一行", "Append": "加在檔案結尾",
        "Encoding": "用哪種文字編碼寫", **WHAT_IF}, effect="overwrite"),
    "Import-Csv": Cmd("讀取 CSV 檔，每一列變成一個項目", {**PATHS, "Encoding": "用哪種文字編碼讀"}),
    "Format-Table": Cmd("用表格顯示（只用於顯示，之後不宜再處理）", {"Property": "要顯示的屬性"}),
    "Format-List": Cmd("一行一個屬性地顯示（只用於顯示）", {"Property": "要顯示的屬性"}),
    "Stop-Service": Cmd("停止 Windows 服務", {"Name": "服務名稱", "Force": "連依賴它的服務一起停止",
                                            **WHAT_IF}, effect="system", targets=("Name",),
                         windows_only=True),
    "Restart-Computer": Cmd("重新開機", {"Force": "不等程式結束，強制重新開機", **WHAT_IF},
                            effect="system", targets=(), windows_only=True),
    "Stop-Computer": Cmd("關機", {"Force": "不等程式結束，強制關機", **WHAT_IF}, effect="system",
                         targets=(), windows_only=True),
    "Clear-RecycleBin": Cmd("清空資源回收筒（裡面的檔案無法再救回）", {
        "DriveLetter": "只清這個磁碟的", "Force": "不詢問直接清空", **WHAT_IF},
        effect="delete", targets=("DriveLetter",), windows_only=True),
}
STATEMENTS = {
    "IfStatementAst": "if 條件判斷", "ForEachStatementAst": "foreach 迴圈", "ForStatementAst": "for 迴圈",
    "WhileStatementAst": "while 迴圈", "DoWhileStatementAst": "do-while 迴圈",
    "DoUntilStatementAst": "do-until 迴圈", "SwitchStatementAst": "switch 分支",
    "TryStatementAst": "try／catch 錯誤處理", "FunctionDefinitionAst": "函式定義",
    "TrapStatementAst": "trap 錯誤處理",
}
EFFECTS = {
    "delete": "會刪除", "overwrite": "可能覆蓋或取代既有內容", "write": "會建立或修改",
    "run": "會執行其他程式或程式碼", "network": "會連上網路", "system": "會改變系統狀態",
}


@dataclass
class Translation:
    script: str
    lines: list[str]
    effects: list[str]
    unknown: list[str]
    environment: str

    def text(self) -> str:
        out = [f"PowerShell：{self.script.strip()}", f"解讀環境：{self.environment}", "逐項說明："]
        out += self.lines
        out.append("影響：" + ("；".join(self.effects) if self.effects else "只讀取或顯示，不改變任何東西"))
        if self.unknown:
            out.append("沒有中文說明的部分（照 PowerShell 本身的資訊列出，不做猜測）：" + "；".join(self.unknown))
        return "\n".join(out)


def describe_command(cmd: dict, depth: int, lines: list[str], effects: list[str],
                     unknown: list[str], windows: bool, piped: bool = False) -> None:
    pad = "  " * depth
    name, resolved = cmd["name"], cmd["resolved"]
    kind = cmd["target_type"] or cmd["type"]
    invocation = {"Ampersand": "（用 & 呼叫）", "Dot": "（用 . 在目前範圍執行）"}.get(cmd["invocation"], "")
    if not name:
        lines.append(f"{pad}- `{cmd['text']}`：執行一段動態決定的指令{invocation}（無法事先確定內容）")
        effects.append("會執行其他程式或程式碼：" + cmd["text"])
        return
    if not kind:
        lines.append(f"{pad}- `{cmd['text']}`：這台電腦找不到名為「{name}」的指令或程式")
        unknown.append(name)
        return
    alias = f"（{name} 是 {resolved} 的別名）" if cmd["type"] == "Alias" else ""
    if kind == "Application":
        lines.append(f"{pad}- `{cmd['text']}`：執行外部程式 {cmd['source'] or name}{invocation}。"
                     "參數由那個程式自己解讀，這裡不翻譯")
        unknown.append(name)
        effects.append("會執行其他程式或程式碼：" + (cmd["source"] or name))
        return
    entry = COMMANDS.get(resolved)
    if entry is None:
        what = {"Cmdlet": "PowerShell 指令", "Function": "函式", "ExternalScript": "腳本檔"}.get(kind, kind)
        lines.append(f"{pad}- `{cmd['text']}`：{what} {resolved}{alias}{invocation}，沒有中文說明")
        unknown.append(resolved)
        if kind == "ExternalScript":
            effects.append("會執行其他程式或程式碼：" + resolved)
    else:
        lines.append(f"{pad}- `{cmd['text']}`：{entry.text}{alias}{invocation}")
    what_if = any(p["name"] == "WhatIf" for p in cmd["bound"])
    for param in sorted(cmd["bound"], key=lambda p: p["at"]):
        meaning = entry.params.get(param["name"]) if entry else None
        value = f"：{param['value']}" if param["value"] and not param["switch"] else ""
        if meaning is None:
            lines.append(f"{pad}  - -{param['name']}{value}（這個參數沒有中文說明）")
            unknown.append(f"{resolved} -{param['name']}")
        else:
            lines.append(f"{pad}  - -{param['name']}{value}：{meaning}")
    for problem in cmd["binding_errors"]:
        lines.append(f"{pad}  - 參數錯誤，PowerShell 會直接出錯、不執行這個指令。PowerShell 的訊息：{problem['message']}")
    for redirect in cmd["redirections"]:
        lines.append(f"{pad}  - `{redirect['text']}`：{describe_redirect(redirect, windows)}")
        if redirect["to"] and redirect["to"].strip("$").lower() != "null":
            effects.append(("寫入" if redirect["append"] else "覆蓋") + f"檔案 {redirect['to']}")
    if entry and entry.effect != "read":
        targets = [p["value"] for p in sorted(cmd["bound"], key=lambda p: p["at"])
                   if p["name"] in entry.targets and p["value"]]
        if targets:
            label = f"{EFFECTS[entry.effect]}：{'、'.join(targets)}"
        elif entry.targets and set(entry.targets) & {"Path", "LiteralPath", "FilePath"} and piped:
            label = f"{EFFECTS[entry.effect]}：前一步傳過來的每一個項目"
        else:
            label = f"{EFFECTS[entry.effect]}（{resolved}）"
        if what_if:
            label += "（但加了 -WhatIf，只是模擬，不會真的做）"
        elif cmd["binding_errors"]:
            label += "（但參數錯誤，這個指令不會執行）"
        effects.append(label)


def describe_redirect(redirect: dict, windows: bool) -> str:
    streams = {"Output": "正常輸出", "Error": "錯誤訊息", "Warning": "警告", "Verbose": "詳細訊息",
               "Debug": "除錯訊息", "Information": "資訊訊息", "All": "所有輸出"}
    source = streams.get(redirect["stream"], redirect["stream"])
    if redirect["merge"]:
        return f"把{source}併入{streams.get(redirect['merge'], redirect['merge'])}"
    if redirect["to"].strip("$").lower() == "null":
        return f"丟掉{source}（不顯示也不存）"
    action = "加在檔案結尾" if redirect["append"] else "寫進檔案（覆蓋原內容）"
    return f"把{source}{action}：{redirect['to']}"


def describe_statement(node: dict, depth: int, lines: list[str], effects: list[str],
                       unknown: list[str], windows: bool) -> None:
    pad = "  " * depth
    kind = node["kind"]
    if kind == "pipeline":
        elements = node["elements"]
        if len(elements) > 1:
            lines.append(f"{pad}- `{node['text']}`：管線，左邊每一步的結果依序交給右邊下一步：")
            depth += 1
        for index, element in enumerate(elements):
            if element["kind"] == "command":
                describe_command(element, depth, lines, effects, unknown, windows, piped=index > 0)
            else:
                lines.append(f"{'  ' * depth}- `{element['text']}`：一個值或運算式（原樣列出，未翻譯）")
        if node["background"]:
            lines.append(f"{pad}  （結尾的 & 表示在背景執行，不等它完成）")
    elif kind == "chain":
        meaning = {"AndAnd": "左邊成功才執行右邊", "OrOr": "左邊失敗才執行右邊"}.get(node["op"], node["op"])
        lines.append(f"{pad}- `{node['text']}`：{meaning}：")
        describe_statement(node["left"], depth + 1, lines, effects, unknown, windows)
        describe_statement(node["right"], depth + 1, lines, effects, unknown, windows)
    elif kind == "assignment":
        lines.append(f"{pad}- `{node['text']}`：把右邊的結果存進變數 {node['target']}：")
        describe_statement(node["value"], depth + 1, lines, effects, unknown, windows)
    elif kind == "expression":
        lines.append(f"{pad}- `{node['text']}`：一個值或運算式（原樣列出，未翻譯）")
    else:
        what = STATEMENTS.get(node["type"], node["type"])
        lines.append(f"{pad}- `{node['text'][:200]}`：{what}。條件和流程本身不翻譯，"
                     "裡面用到的每個指令列在下面（不代表都會執行）")
        unknown.append(what)
        for cmd in node["commands"]:
            describe_command(cmd, depth + 1, lines, effects, unknown, windows)


def translate(script: str, pwsh: str | None = None, cwd: str | None = None,
              timeout: float = 120) -> Translation:
    info = analyze(script, pwsh, cwd, timeout)
    environment = (f"{'Windows PowerShell' if info['edition'] == 'Desktop' else 'PowerShell'} "
                   f"{info['version']}（{'Windows' if info['windows'] else '非 Windows'}）")
    lines: list[str] = []
    effects: list[str] = []
    unknown: list[str] = []
    if info["errors"]:
        for error in info["errors"]:
            lines.append(f"- 語法錯誤（第 {error['offset'] + 1} 個字元）：{error['message']}；"
                         "PowerShell 不會執行這一行")
    for statement in info["statements"]:
        describe_statement(statement, 0, lines, effects, unknown, info["windows"])
    return Translation(script, lines, list(dict.fromkeys(effects)),
                       list(dict.fromkeys(unknown)), environment)
