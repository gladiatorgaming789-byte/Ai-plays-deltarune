@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto dependencies

echo [Setup] No project environment found. Creating .venv...
call :create_environment
if errorlevel 1 goto setup_failed

:dependencies
rem Run the stdlib-only bootstrap by file path. Using -m here would import
rem deltarune_agent\__init__.py before Pillow/PySide6 are installed.
"%VENV_PY%" "deltarune_agent\bootstrap_dependencies.py"
if errorlevel 1 goto setup_failed

rem Update the checkout before validating generated mod packages. Otherwise an
rem older checkout can reject current package bytes using stale release metadata
rem and fail before the GUI's normal startup updater ever gets a chance to run.
"%VENV_PY%" -m deltarune_agent.auto_update --apply
if errorlevel 1 goto setup_failed

rem The update may have changed requirements.txt or the bootstrap itself. Run
rem the bootstrap again from the updated checkout so the environment marker and
rem required packages match the code that is about to launch.
"%VENV_PY%" "deltarune_agent\bootstrap_dependencies.py"
if errorlevel 1 goto setup_failed

rem Materialize the validated DeltaMod candidates from committed CSX sources.
rem Existing packages are accepted only when their size and SHA-256 match the
rem checked-in release records; missing or invalid packages are rebuilt.
"%VENV_PY%" "mods\build_validated_packages.py"
if errorlevel 1 goto setup_failed

"%VENV_PY%" -m deltarune_agent gui
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

:create_environment
set "PY_LAUNCHER="
where py >nul 2>nul
if not errorlevel 1 set "PY_LAUNCHER=py"
if not defined PY_LAUNCHER if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" set "PY_LAUNCHER=%LocalAppData%\Programs\Python\Launcher\py.exe"
if not defined PY_LAUNCHER goto try_direct_python

"%PY_LAUNCHER%" -3.14 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_314
"%PY_LAUNCHER%" -3.13 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_313
"%PY_LAUNCHER%" -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_312
"%PY_LAUNCHER%" -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 goto create_311
goto try_direct_python

:create_314
"%PY_LAUNCHER%" -3.14 -m venv .venv
exit /b %errorlevel%

:create_313
"%PY_LAUNCHER%" -3.13 -m venv .venv
exit /b %errorlevel%

:create_312
"%PY_LAUNCHER%" -3.12 -m venv .venv
exit /b %errorlevel%

:create_311
"%PY_LAUNCHER%" -3.11 -m venv .venv
exit /b %errorlevel%

:try_direct_python
set "DIRECT_PYTHON="
for %%V in (314 313 312 311) do if not defined DIRECT_PYTHON if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set "DIRECT_PYTHON=%LocalAppData%\Programs\Python\Python%%V\python.exe"
for %%V in (314 313 312 311) do if not defined DIRECT_PYTHON if exist "%ProgramFiles%\Python%%V\python.exe" set "DIRECT_PYTHON=%ProgramFiles%\Python%%V\python.exe"
if not defined DIRECT_PYTHON goto try_python
"%DIRECT_PYTHON%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
"%DIRECT_PYTHON%" -m venv .venv
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
echo Install official Python from python.org once, then double-click this launcher again.
exit /b 1

:setup_failed
echo.
echo [Setup] The project environment, update, or validated mod packages could not be prepared.
echo Nothing was installed globally. Review the error above and try again.
pause
exit /b 1
