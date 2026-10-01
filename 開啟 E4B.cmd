@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: E4B Core Python environment is missing.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "e4b_core\cli.py" install >nul
if errorlevel 1 (
    echo ERROR: Could not install the global e4b command.
    pause
    exit /b 1
)

call "%LOCALAPPDATA%\Microsoft\WinGet\Links\e4b.cmd"
exit /b %errorlevel%
