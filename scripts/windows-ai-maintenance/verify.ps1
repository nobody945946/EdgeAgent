$ErrorActionPreference='Stop'
$owner=Join-Path $PSScriptRoot 'maintain.ps1'
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile($owner,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) {throw ($errors | Out-String)}
. $owner -Action PlanLight | Out-Null
$checks=0
function Assert($condition,$message) {
    if (!$condition) {throw $message}
    $script:checks++
}
# Real Win32 API round trip; the original desktop settings are restored in finally.
$visualBefore=@(Read-Visual)
try {
    Write-Visual @($visualBefore | ForEach-Object {[pscustomobject]@{Name=$_.Name;Value=0}})
    foreach ($item in (Read-Visual)) {Assert ($item.Value -eq 0) ('Native setter failed: '+$item.Name)}
} finally {Write-Visual $visualBefore}
Assert ((Read-Visual | ConvertTo-Json -Compress) -eq ($visualBefore | ConvertTo-Json -Compress)) 'Native visual restoration failed'

# Exercise the production profile engine with real, isolated registry values and state JSON.
# Service operations and visual operations are mocked so no system service is changed.
$fixtureRegistry='HKCU:\Software\WindowsAiMaintenanceVerification_'+[guid]::NewGuid().ToString('N')
$snapshotPath=Join-Path $maintenanceRoot 'verification-state.json'
if (Test-Path -LiteralPath $snapshotPath) {throw 'Unexpected verification file already exists'}
$lightRegistry=@(
    @{Path=$fixtureRegistry;Name='Existing';Value=0},
    @{Path=$fixtureRegistry;Name='Expanded';Value=0},
    @{Path=$fixtureRegistry;Name='Absent';Value=0}
)
$script:fakeServices=@{
    DiagTrack=[pscustomobject]@{Name='DiagTrack';StartMode='Auto';State='Running';DelayedAutoStart=[pscustomobject]@{Path=$fixtureRegistry;Name='DiagDelayed';Exists=$false;Kind=$null;Data=$null}}
    WSearch=[pscustomobject]@{Name='WSearch';StartMode='Auto';State='Running';DelayedAutoStart=[pscustomobject]@{Path=$fixtureRegistry;Name='SearchDelayed';Exists=$true;Kind='DWord';Data=1}}
}
$script:fakeVisual=@([pscustomobject]@{Name='WindowAnimation';Value=1},[pscustomobject]@{Name='ClientAreaAnimation';Value=0})
function Assert-Administrator {}
function Measure-System {[pscustomobject]@{AvailableRAMMiB=1000}}
function Get-AppxPackage {return @()}
function Get-Service($Name) {[pscustomobject]@{DependentServices=@()}}
function Read-ServiceState($names) {@($names | ForEach-Object {$script:fakeServices[$_]})}
function Set-Service($Name,$StartupType) {
    $script:fakeServices[$Name].StartMode=if($StartupType -eq 'Automatic'){'Auto'}else{$StartupType}
}
function Stop-Service($Name) {$script:fakeServices[$Name].State='Stopped'}
function Start-Service($Name) {$script:fakeServices[$Name].State='Running'}
function Read-Visual {return $script:fakeVisual}
function Write-Visual($values) {
    if ($script:failNextWrite) {$script:failNextWrite=$false;throw 'Injected visual failure'}
    $script:fakeVisual=@($values)
}
try {
    New-Item -Path $fixtureRegistry | Out-Null
    New-ItemProperty -LiteralPath $fixtureRegistry -Name Existing -PropertyType DWord -Value 1 | Out-Null
    New-ItemProperty -LiteralPath $fixtureRegistry -Name Expanded -PropertyType ExpandString -Value '%TEMP%\fixture' | Out-Null
    New-ItemProperty -LiteralPath $fixtureRegistry -Name SearchDelayed -PropertyType DWord -Value 1 | Out-Null
    $before=@($lightRegistry | ForEach-Object {Read-Registry $_}) | ConvertTo-Json -Compress
    $vBefore=$script:fakeVisual | ConvertTo-Json -Compress
    Invoke-LightProfile 'ApplyLight' | Out-Null
    Assert ($script:fakeServices.DiagTrack.StartMode -eq 'Disabled' -and $script:fakeServices.DiagTrack.State -eq 'Stopped') 'Service apply failed'
    Assert (@($lightRegistry | Where-Object {(Read-Registry $_).Data -ne 0}).Count -eq 0) 'Registry apply failed'
    $first=[IO.File]::ReadAllText($snapshotPath)
    Invoke-LightProfile 'ApplyLight' | Out-Null
    Assert ([IO.File]::ReadAllText($snapshotPath) -eq $first) 'Repeated apply overwrote the originals'
    Invoke-LightProfile 'RestoreLight' | Out-Null
    Assert ((@($lightRegistry | ForEach-Object {Read-Registry $_}) | ConvertTo-Json -Compress) -eq $before) 'Registry existence/type/data restoration failed'
    Assert (($script:fakeVisual | ConvertTo-Json -Compress) -eq $vBefore) 'Mixed visual settings not restored'
    Assert ($script:fakeServices.DiagTrack.StartMode -eq 'Auto' -and $script:fakeServices.DiagTrack.State -eq 'Running') 'Service original state not restored'
    Invoke-LightProfile 'RestoreLight' | Out-Null
    Assert ((@($lightRegistry | ForEach-Object {Read-Registry $_}) | ConvertTo-Json -Compress) -eq $before) 'Repeated restore changed the original values'
    $script:failNextWrite=$true
    $failed=$false
    try {Invoke-LightProfile 'ApplyLight' | Out-Null} catch {$failed=$true}
    Assert $failed 'Injected failure did not propagate'
    Assert ((@($lightRegistry | ForEach-Object {Read-Registry $_}) | ConvertTo-Json -Compress) -eq $before) 'Partial failure did not roll back registry'
    Assert ($script:fakeServices.DiagTrack.State -eq 'Running' -and $script:fakeServices.DiagTrack.StartMode -eq 'Auto') 'Partial failure did not roll back service'
    Invoke-LightProfile 'BeginAI' | Out-Null
    Assert ($script:fakeServices.WSearch.StartMode -eq 'Manual' -and $script:fakeServices.WSearch.State -eq 'Stopped') 'Search pause failed'
    $failed=$false
    try {Invoke-LightProfile 'BeginAI' | Out-Null} catch {$failed=$true}
    Assert $failed 'Duplicate Search session not rejected'
    Invoke-LightProfile 'EndAI' | Out-Null
    Assert ($script:fakeServices.WSearch.StartMode -eq 'Auto' -and $script:fakeServices.WSearch.State -eq 'Running') 'Search not restored'
    Assert ((Get-ItemPropertyValue -LiteralPath $fixtureRegistry -Name SearchDelayed) -eq 1) 'Delayed automatic start not restored'
    Assert (!(Read-State).SearchSession) 'Search session not cleared'
    Assert ((Read-State).Light.Registry.Count -eq 3) 'Search updates lost Light snapshot'
    Assert (!(Test-Path -LiteralPath ($snapshotPath+'.pending'))) 'Atomic snapshot write left a pending file'
    Invoke-LightProfile 'BeginAI' | Out-Null
    Invoke-LightProfile 'RestoreLight' | Out-Null
    Assert (!(Read-State).SearchSession -and $script:fakeServices.WSearch.StartMode -eq 'Auto' -and $script:fakeServices.WSearch.State -eq 'Running') 'Profile restore left Search paused'
    Assert ((Read-State).Registry.Count -eq 5) 'Core baseline not captured before the first Light apply'
} finally {
    if (Test-Path -LiteralPath $fixtureRegistry) {Remove-Item -LiteralPath $fixtureRegistry}
    if (Test-Path -LiteralPath $snapshotPath) {Remove-Item -LiteralPath $snapshotPath}
    if (Test-Path -LiteralPath ($snapshotPath+'.pending')) {Remove-Item -LiteralPath ($snapshotPath+'.pending')}
}
# Parse and invoke the launcher's actual command with the process boundary mocked.
$launcher=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy.cmd') | Where-Object {$_ -like 'powershell.exe *'}
$command=$launcher -replace '^powershell\.exe -NoProfile -Command "','' -replace '"$',''
$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseInput($command,[ref]$tokens,[ref]$errors) | Out-Null
Assert ($errors.Count -eq 0) 'Launcher command does not parse'
function Start-Process($FilePath,$ArgumentList,[switch]$Wait,$Verb) {
    $script:launch=[pscustomobject]@{File=$FilePath;Arguments=$ArgumentList;Wait=$Wait;Verb=$Verb}
}
$oldScript=$env:WINDOWS_AI_SCRIPT; $oldAction=$env:WINDOWS_AI_ACTION
try {
    $env:WINDOWS_AI_SCRIPT='C:\Example directory with spaces\maintain.ps1'
    $env:WINDOWS_AI_ACTION='PlanLight'
    & ([scriptblock]::Create($command))
    Assert ($script:launch.Arguments[5] -eq ('"'+$env:WINDOWS_AI_SCRIPT+'"')) 'Launcher did not quote a path containing spaces'
    Assert ($script:launch.Arguments[-1] -eq 'PlanLight' -and !$script:launch.Verb) 'Read-only preview incorrectly requires elevation'
    Assert ($script:launch.Arguments[6] -eq '-StateDirectory' -and $script:launch.Arguments[7] -eq ('"'+(Join-Path $env:USERPROFILE '.codex/windows-ai-maintenance')+'"')) 'Launcher did not preserve the original user state directory across elevation'
    Assert ($script:launch.File -eq 'powershell.exe' -and $script:launch.Wait) 'Launcher did not use the expected process boundary'
} finally {$env:WINDOWS_AI_SCRIPT=$oldScript; $env:WINDOWS_AI_ACTION=$oldAction}
Write-Output ('PASS: '+$checks+' checks; native visual round trip restored; profile/service logic isolated; no permanent deployment.')
