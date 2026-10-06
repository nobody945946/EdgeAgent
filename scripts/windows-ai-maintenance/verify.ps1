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
foreach ($name in @('ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo')) {
    $script:fakeServices[$name]=[pscustomobject]@{Name=$name;StartMode='Manual';State='Stopped';DelayedAutoStart=[pscustomobject]@{Path=$fixtureRegistry;Name=($name+'Delayed');Exists=$false;Kind=$null;Data=$null}}
}
$script:fakeVisual=@([pscustomobject]@{Name='WindowAnimation';Value=1},[pscustomobject]@{Name='ClientAreaAnimation';Value=0})
function Assert-Administrator {}
function Measure-System {[pscustomobject]@{AvailableRAMMiB=1000}}
function Get-AppxPackage {return @()}
function Get-Service($Name) {
    [pscustomobject]@{DependentServices=if($script:runningDependent){@([pscustomobject]@{Status='Running'})}else{@()}}
}
function Read-ServiceState($names) {@($names | ForEach-Object {$script:fakeServices[$_]})}
function Set-Service($Name,$StartupType) {
    $script:fakeServices[$Name].StartMode=if($StartupType -eq 'Automatic'){'Auto'}else{$StartupType}
}
function Stop-Service($Name) {
    if ($script:failNextStop -and (!$script:failStopName -or $script:failStopName -eq $Name)) {$script:failNextStop=$false;throw 'Injected service stop failure'}
    $script:fakeServices[$Name].State='Stopped'
}
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
    # New controls must capture their originals without replacing an older profile.
    $oldVisualSnapshot=(Read-State).Light.Visual | ConvertTo-Json -Compress
    $lightRegistry+=@{Path=$fixtureRegistry;Name='NonzeroTarget';Value=1}
    New-ItemProperty -LiteralPath $fixtureRegistry -Name NonzeroTarget -PropertyType DWord -Value 0 | Out-Null
    $script:fakeVisual+=@([pscustomobject]@{Name='MenuFade';Value=1})
    Invoke-LightProfile 'ApplyLight' | Out-Null
    Assert ((Get-ItemPropertyValue -LiteralPath $fixtureRegistry -Name NonzeroTarget) -eq 1 -and @($script:fakeVisual | Where-Object Value -ne 0).Count -eq 0) 'Extended controls did not apply their target values'
    Assert (((Read-State).Light.Visual | Where-Object Name -ne 'MenuFade' | ConvertTo-Json -Compress) -eq $oldVisualSnapshot) 'Profile extension overwrote existing visual originals'
    $extendedSnapshot=[IO.File]::ReadAllText($snapshotPath)
    Invoke-LightProfile 'ApplyLight' | Out-Null
    Assert ([IO.File]::ReadAllText($snapshotPath) -eq $extendedSnapshot) 'Repeated extended apply replaced originals'
    Invoke-LightProfile 'RestoreLight' | Out-Null
    Assert ((Get-ItemPropertyValue -LiteralPath $fixtureRegistry -Name NonzeroTarget) -eq 0) 'Nonzero target did not restore its original value'
    Assert (@($script:fakeVisual | Where-Object {$_.Name -eq 'MenuFade' -and $_.Value -eq 1}).Count -eq 1) 'New visual control did not restore its original value'
    $serviceBefore=Read-ServiceState @('DiagTrack','ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo') | ConvertTo-Json -Depth 5 -Compress
    Invoke-ServiceProfile 'PlanServices' | Out-Null
    Assert (!(Read-State).ServiceProfile) 'Read-only service plan changed the snapshot'
    $script:runningDependent=$true
    $dependentFailed=$false
    try {Invoke-ServiceProfile 'ApplyServices' | Out-Null} catch {$dependentFailed=$true}
    $script:runningDependent=$false
    Assert $dependentFailed 'Running service dependent was not rejected'
    Assert (!(Read-State).ServiceProfile -and (Read-ServiceState @('DiagTrack','ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo') | ConvertTo-Json -Depth 5 -Compress) -eq $serviceBefore) 'Dependent rejection changed service state or snapshot'
    Invoke-ServiceProfile 'ApplyServices' | Out-Null
    foreach ($name in @('DiagTrack','ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo')) {
        Assert ($script:fakeServices[$name].StartMode -eq 'Disabled' -and $script:fakeServices[$name].State -eq 'Stopped') ('Service default-off failed: '+$name)
    }
    Assert ($script:fakeServices.WSearch.State -eq 'Running') 'Service profile affected Windows Search'
    $firstServiceState=[IO.File]::ReadAllText($snapshotPath)
    Invoke-ServiceProfile 'ApplyServices' | Out-Null
    Assert ([IO.File]::ReadAllText($snapshotPath) -eq $firstServiceState) 'Repeated service apply overwrote original state'
    Invoke-ServiceProfile 'RestoreServices' | Out-Null
    Assert ((Read-ServiceState @('DiagTrack','ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo') | ConvertTo-Json -Depth 5 -Compress) -eq $serviceBefore) 'Service restore lost original start mode or running state'
    $script:failNextStop=$true
    $script:failStopName='ASUSSystemDiagnosis'
    $serviceFailed=$false
    try {Invoke-ServiceProfile 'ApplyServices' | Out-Null} catch {$serviceFailed=$true}
    Assert $serviceFailed 'Service stop failure did not propagate'
    Assert ((Read-ServiceState @('DiagTrack','ASUSSystemAnalysis','ASUSSystemDiagnosis','RetailDemo') | ConvertTo-Json -Depth 5 -Compress) -eq $serviceBefore) 'Failed service apply did not restore original state'
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
function Start-Process($FilePath,$ArgumentList,[switch]$Wait,$Verb,$WindowStyle,[switch]$PassThru) {
    $script:launch=[pscustomobject]@{File=$FilePath;Arguments=$ArgumentList;Wait=$Wait;Verb=$Verb;WindowStyle=$WindowStyle;PassThru=$PassThru}
    return [pscustomobject]@{ExitCode=0}
}
$oldScript=$env:WINDOWS_AI_SCRIPT; $oldAction=$env:WINDOWS_AI_ACTION
try {
    $env:WINDOWS_AI_SCRIPT='C:\Example directory with spaces\maintain.ps1'
    $env:WINDOWS_AI_ACTION='PlanLight'
    # Run the launcher's exit statement in a child scope with exit captured as a return.
    $testCommand=$command -replace 'exit \$child.ExitCode','return $child.ExitCode'
    & ([scriptblock]::Create($testCommand)) | Out-Null
    Assert ($script:launch.Arguments[4] -eq ('"'+$env:WINDOWS_AI_SCRIPT+'"')) 'Launcher did not quote a path containing spaces'
    Assert ($script:launch.Arguments[-1] -eq 'PlanLight' -and !$script:launch.Verb) 'Read-only preview incorrectly requires elevation'
    Assert ($script:launch.Arguments[5] -eq '-StateDirectory' -and $script:launch.Arguments[6] -eq ('"'+(Join-Path $env:USERPROFILE '.codex/windows-ai-maintenance')+'"')) 'Launcher did not preserve the original user state directory across elevation'
    Assert ($script:launch.File -eq 'powershell.exe' -and $script:launch.Wait) 'Launcher did not use the expected process boundary'
    Assert ($script:launch.WindowStyle -eq 'Hidden' -and $script:launch.PassThru) 'Launcher must hide helper windows and capture completion'
    Assert ($script:launch.Arguments -notcontains '-NoExit') 'Launcher leaves a permanent helper process'
} finally {$env:WINDOWS_AI_SCRIPT=$oldScript; $env:WINDOWS_AI_ACTION=$oldAction}
Write-Output ('PASS: '+$checks+' checks; native visual round trip restored; profile/service logic isolated; no permanent deployment.')
