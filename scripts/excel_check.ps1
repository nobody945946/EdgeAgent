# Checks two Excel-only claims of the formula translator (edge_agent/lingua/excel.py)
# in the real Excel, if it is installed. Opens one new blank workbook, never a
# user's file, never saves, and makes sure Excel exits. Prints one JSON line.
$ErrorActionPreference = 'Stop'
$result = [ordered]@{ excel = $false; vlookup_past_table = $null; xlookup_default = $null; libreoffice = $false }
$soffice = @("$env:ProgramFiles\LibreOffice\program\soffice.exe", "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe") | Where-Object { Test-Path $_ }
$result.libreoffice = [bool]$soffice
$before = @(Get-Process EXCEL -ErrorAction SilentlyContinue | ForEach-Object Id)
$excel = $null
try { $excel = New-Object -ComObject Excel.Application } catch { }
if ($excel) {
  $result.excel = $true
  try {
    $excel.Visible = $false; $excel.DisplayAlerts = $false
    $book = $excel.Workbooks.Add(); $sheet = $book.Worksheets.Item(1)
    $sheet.Range('A1').Formula = '=VLOOKUP(1,{1,"a";3,"b"},3,FALSE)'
    $sheet.Range('A2').Formula2 = '=XLOOKUP(4,{1;3;5},{"a";"b";"c"})'
    $excel.Calculate()
    $result.vlookup_past_table = [string]$sheet.Range('A1').Text
    $result.xlookup_default = [string]$sheet.Range('A2').Text
    $book.Close($false)
  } finally {
    $excel.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    Start-Sleep -Seconds 2
    # Only an Excel this script started, by PID, if it is still there.
    Get-Process EXCEL -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id } | ForEach-Object { Stop-Process -Id $_.Id -Force }
  }
}
$result | ConvertTo-Json -Compress
