from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


DEVELOPMENT_BRANCH = "development"
SAFE_LAUNCHER_NAME = "Start Deltarune Agent Safe.cmd"


def _message(title: str, body: str, *, error: bool = False) -> None:
    if os.name == "nt":
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, body, title, flags)
    else:
        print(f"{title}: {body}", file=sys.stderr if error else sys.stdout)


def _find_git() -> str | None:
    regular = shutil.which("git")
    if regular:
        return regular
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    candidates = sorted(
        (Path(local_app_data) / "GitHubDesktop").glob(
            "app-*/resources/app/git/cmd/git.exe"
        ),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _git(git: str, root: Path, *args: str, timeout: float = 30.0) -> str:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [git, "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=creation_flags,
        check=False,
    )
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout or "Git command failed").strip())
    return result.stdout.strip()


def verify_checkout(project_root: Path) -> tuple[bool, str]:
    git = _find_git()
    if git is None:
        return False, "GitHub Desktop's bundled Git could not be found."
    # Worktrees and some linked checkouts store .git as a text file rather than
    # a directory. Existence is the correct repository preflight here.
    if not (project_root / ".git").exists():
        return False, "This folder is not a cloned Git repository."
    try:
        branch = _git(git, project_root, "branch", "--show-current")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"The current branch could not be checked:\n\n{exc}"
    if branch != DEVELOPMENT_BRANCH:
        return (
            False,
            f'Current branch: {branch or "unknown"}\n\n'
            f'Switch GitHub Desktop to "{DEVELOPMENT_BRANCH}" before testing.',
        )
    try:
        _git(
            git,
            project_root,
            "fetch",
            "--quiet",
            "origin",
            DEVELOPMENT_BRANCH,
        )
        counts = _git(
            git,
            project_root,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...origin/{DEVELOPMENT_BRANCH}",
        ).split()
        if len(counts) != 2:
            raise ValueError(f"unexpected rev-list output: {counts!r}")
        ahead, behind = (int(value) for value in counts)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return (
            False,
            "The latest development version could not be verified. Open GitHub "
            f"Desktop, fetch origin, and try again.\n\n{exc}",
        )
    if behind:
        return (
            False,
            f"This development checkout is {behind} commit(s) behind origin.\n\n"
            "Pull origin in GitHub Desktop before testing.",
        )
    if ahead:
        return (
            True,
            f"Development branch verified. This checkout has {ahead} local commit(s).",
        )
    return True, "Development branch verified and up to date."


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_safe_bootstrap(project_root: Path) -> None:
    if os.name != "nt" or not (project_root / ".git").exists():
        return
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return
    app_root = Path(local_app_data) / "DeltaruneAgent"
    app_root.mkdir(parents=True, exist_ok=True)
    installed_script = app_root / "safe_bootstrap.py"
    source = Path(__file__).resolve()
    try:
        if source != installed_script.resolve():
            temporary_script = installed_script.with_name(
                f".{installed_script.name}.{uuid4().hex}.tmp"
            )
            try:
                shutil.copy2(source, temporary_script)
                os.replace(temporary_script, installed_script)
            finally:
                temporary_script.unlink(missing_ok=True)
        command_path = project_root / SAFE_LAUNCHER_NAME
        command = (
            "@echo off\r\n"
            "cd /d \"%~dp0\"\r\n"
            "if exist \".venv\\Scripts\\python.exe\" (\r\n"
            f"  \".venv\\Scripts\\python.exe\" \"{installed_script}\" \"%~dp0\"\r\n"
            ") else (\r\n"
            f"  py \"{installed_script}\" \"%~dp0\"\r\n"
            ")\r\n"
            "if errorlevel 1 pause\r\n"
        )
        _atomic_write_text(command_path, command)
        exclude = project_root / ".git" / "info" / "exclude"
        # A linked worktree has a .git file, so its exclude file is owned by the
        # common repository and cannot be derived safely here. The launcher is
        # still installed; only the convenience ignore entry is skipped.
        if exclude.is_file():
            current = exclude.read_text(encoding="utf-8", errors="replace")
            entry = f"/{SAFE_LAUNCHER_NAME}"
            if entry not in current.splitlines():
                with exclude.open("a", encoding="utf-8") as stream:
                    if current and not current.endswith("\n"):
                        stream.write("\n")
                    stream.write(entry + "\n")
    except OSError:
        return


def main() -> int:
    if len(sys.argv) != 2:
        _message("Deltarune Agent", "The project folder was not supplied.", error=True)
        return 2
    project_root = Path(sys.argv[1]).resolve()
    safe, detail = verify_checkout(project_root)
    if not safe:
        _message("Deltarune Agent testing blocked", detail, error=True)
        return 1
    try:
        os.chdir(project_root)
        command = [sys.executable, "-m", "deltarune_agent", "gui"]
        return subprocess.call(command)
    except OSError as exc:
        _message(
            "Deltarune Agent launch failed",
            f"The verified checkout could not start the GUI.\n\n{exc}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
