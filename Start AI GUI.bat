@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto dependencies

echo [Setup] No project environment found. Creating .venv...
call :create_environment
if errorlevel 1 goto setup_failed

:dependencies
"%VENV_PY%" -m deltarune_agent.bootstrap_dependencies
if errorlevel 1 goto setup_failed

"%VENV_PY%" -m deltarune_agent gui
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

:create_environment
where py >nul 2>nul
if errorlevel 1 goto try_python

py -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_313
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_312
py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_311
goto try_python

:create_313
py -3.13 -m venv .venv
exit /b %errorlevel%

:create_312
py -3.12 -m venv .venv
exit /b %errorlevel%

:create_311
py -3.11 -m venv .venv
exit /b %errorlevel%

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python -m venv .venv
exit /b %errorlevel%

:no_python
echo.
echo [Setup] Python 3.11 or newer was not found.
echo Install Python once, then double-click this launcher again.
exit /b 1

:setup_failed
echo.
echo [Setup] The project environment could not be prepared.
echo Nothing was installed globally. Review the error above and try again.
pause
exit /b 1
