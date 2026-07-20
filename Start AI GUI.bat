@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m deltarune_agent gui
    goto finished
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -m deltarune_agent gui
    goto finished
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    python -m deltarune_agent gui
    goto finished
)

echo Python was not found. Complete the README setup first.
pause
exit /b 1

:finished
if %errorlevel% neq 0 pause
