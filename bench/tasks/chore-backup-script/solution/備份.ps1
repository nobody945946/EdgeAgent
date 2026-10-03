$target = Join-Path $PSScriptRoot ("備份/" + (Get-Date -Format yyyyMMdd))
$source = Join-Path $PSScriptRoot "文件"
Get-ChildItem $source -Recurse -File | Where-Object { $_.Extension -ne ".tmp" -and -not $_.Name.StartsWith('~$') } | ForEach-Object {
    $rel = [IO.Path]::GetRelativePath($source, $_.FullName)
    $dest = Join-Path $target $rel
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest
}
