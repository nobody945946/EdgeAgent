@echo off
setlocal
set "WINDOWS_AI_SCRIPT=%~dp0maintain.ps1"
set "WINDOWS_AI_ACTION=%~1"
if not defined WINDOWS_AI_ACTION set "WINDOWS_AI_ACTION=ApplyLight"
powershell.exe -NoProfile -Command "$allowed=@('ApplyLight','RestoreLight','PlanLight','BeginAI','EndAI'); if($allowed -notcontains $env:WINDOWS_AI_ACTION){Write-Error 'Unsupported action'; exit 1}; $stateDirectory=Join-Path $env:USERPROFILE '.codex/windows-ai-maintenance'; $argsForScript=@('-NoProfile','-NoExit','-ExecutionPolicy','Bypass','-File',([char]34+$env:WINDOWS_AI_SCRIPT+[char]34),'-StateDirectory',([char]34+$stateDirectory+[char]34),'-Action',$env:WINDOWS_AI_ACTION); $identity=[Security.Principal.WindowsIdentity]::GetCurrent(); $admin=(New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); if($admin -or $env:WINDOWS_AI_ACTION -eq 'PlanLight'){Start-Process -FilePath powershell.exe -ArgumentList $argsForScript -Wait}else{Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $argsForScript -Wait}"
endlocal
