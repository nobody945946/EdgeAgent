@echo off
setlocal
set "WINDOWS_AI_SCRIPT=%~dp0maintain.ps1"
set "WINDOWS_AI_ACTION=%~1"
if not defined WINDOWS_AI_ACTION set "WINDOWS_AI_ACTION=ApplyLight"
powershell.exe -NoProfile -Command "$allowed=@('ApplyLight','RestoreLight','PlanLight','BeginAI','EndAI','PlanServices','ApplyServices','RestoreServices'); if($allowed -notcontains $env:WINDOWS_AI_ACTION){Write-Error 'Unsupported action'; exit 1}; $stateDirectory=Join-Path $env:USERPROFILE '.codex/windows-ai-maintenance'; $argsForScript=@('-NoProfile','-ExecutionPolicy','Bypass','-File',([char]34+$env:WINDOWS_AI_SCRIPT+[char]34),'-StateDirectory',([char]34+$stateDirectory+[char]34),'-Action',$env:WINDOWS_AI_ACTION); $identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $admin=(New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); if($admin -or $env:WINDOWS_AI_ACTION -in @('PlanLight','PlanServices')){$child=Start-Process -FilePath powershell.exe -WindowStyle Hidden -ArgumentList $argsForScript -Wait -PassThru}else{$child=Start-Process -FilePath powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList $argsForScript -Wait -PassThru}; exit $child.ExitCode"
exit /b %errorlevel%
endlocal
