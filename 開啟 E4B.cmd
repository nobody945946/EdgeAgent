@echo off
setlocal
cd /d "%~dp0"

if /i "%~1"=="--check" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -SetupOnly
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1"
)
if errorlevel 1 (
    echo ERROR: E4B setup failed. Run this launcher again to retry.
    pause
    exit /b 1
)

exit /b 0
