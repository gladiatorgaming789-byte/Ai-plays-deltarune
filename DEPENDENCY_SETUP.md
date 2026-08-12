# Automatic dependency setup

The Windows launcher now owns the project environment. A fresh machine no longer needs manual `venv`, `pip`, Pillow, PyAutoGUI, or PySide6 setup.

## Normal use

Double-click:

```text
Start AI GUI.bat
```

On first launch the script:

1. looks for Python 3.13, 3.12, or 3.11;
2. creates the project-local `.venv` when it does not exist;
3. runs the stdlib-only `deltarune_agent/bootstrap_dependencies.py` file directly inside that environment, before importing the `deltarune_agent` package;
4. installs the current branch's `requirements.txt` with pip;
5. runs `pip check`;
6. verifies that the declared project packages are present; and
7. launches the GUI only after setup succeeds.

Nothing is installed into the global Python environment.

The bootstrap is intentionally executed by file path rather than with `python -m deltarune_agent.bootstrap_dependencies`. Module execution imports `deltarune_agent/__init__.py` first, which can require Pillow, PySide6, and other project dependencies before the bootstrap has had a chance to install them.

## Later launches

A successful setup writes `.venv/.deltarune-ai-dependencies.json` with the SHA-256 of `requirements.txt` and the Python major/minor version.

On later launches the bootstrap checks that marker and verifies the packages declared by the current branch. If everything is current, no pip install is run. If `requirements.txt` changed, the Python version changed, or a required package disappeared, the environment is repaired before the GUI opens.

This complements the Git auto-updater: the launcher handles a brand-new checkout, while future project updates can change `requirements.txt` without requiring manual dependency commands.

## Only machine prerequisite

Python itself is not checked into this repository. The launcher requires Python 3.11 or newer to be installed on Windows once. It prefers 3.13, then 3.12, then 3.11.

If Python is missing, the launcher stops without changing the system and tells you to install Python, then you can double-click it again.

## Manual verification

From an existing project environment:

```powershell
.\.venv\Scripts\python.exe .\deltarune_agent\bootstrap_dependencies.py --check
```

To repair/reinstall when needed:

```powershell
.\.venv\Scripts\python.exe .\deltarune_agent\bootstrap_dependencies.py
```

The normal launcher already performs these checks automatically, so these commands are only for troubleshooting.
