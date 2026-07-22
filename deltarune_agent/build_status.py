from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


DEVELOPMENT_BRANCH = "development"


@dataclass(frozen=True)
class BuildStatus:
    branch: str | None
    revision: str
    ahead: int | None
    behind: int | None
    remote_checked: bool
    detail: str = ""

    @property
    def on_development_branch(self) -> bool:
        return self.branch == DEVELOPMENT_BRANCH

    @property
    def outdated(self) -> bool:
        return bool(self.behind and self.behind > 0)

    @property
    def diverged(self) -> bool:
        return bool(self.ahead and self.behind)

    @property
    def safe_for_testing(self) -> bool:
        return self.on_development_branch and self.remote_checked and not self.outdated

    @property
    def label(self) -> str:
        branch = self.branch or "unknown branch"
        if not self.on_development_branch:
            return f"WRONG BRANCH: {branch}"
        if not self.remote_checked:
            return f"DEVELOPMENT • update unverified"
        if self.diverged:
            return f"DEVELOPMENT • diverged ({self.ahead} ahead, {self.behind} behind)"
        if self.outdated:
            return f"DEVELOPMENT • OUTDATED ({self.behind} commit(s) behind)"
        if self.ahead:
            return f"DEVELOPMENT • local commits ({self.ahead} ahead)"
        return "DEVELOPMENT • up to date"


def _run_git(git: str, project_root: Path, *args: str, timeout: float = 12.0) -> str:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [git, "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=creation_flags,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise OSError(detail)
    return result.stdout.strip()


def find_git_executable() -> str | None:
    regular = shutil.which("git")
    if regular:
        return regular
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    desktop_root = Path(local_app_data) / "GitHubDesktop"
    candidates = sorted(
        desktop_root.glob("app-*/resources/app/git/cmd/git.exe"),
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def inspect_build(
    project_root: Path,
    revision: str,
    *,
    fetch_remote: bool = True,
) -> BuildStatus:
    git = find_git_executable()
    if git is None or not (project_root / ".git").exists():
        return BuildStatus(
            branch=None,
            revision=revision,
            ahead=None,
            behind=None,
            remote_checked=False,
            detail="This folder is not a Git clone, or GitHub Desktop's Git was not found.",
        )
    try:
        branch = _run_git(git, project_root, "branch", "--show-current") or None
    except OSError as exc:
        return BuildStatus(None, revision, None, None, False, str(exc))

    remote_checked = False
    fetch_detail = ""
    if fetch_remote:
        try:
            _run_git(
                git,
                project_root,
                "fetch",
                "--quiet",
                "origin",
                DEVELOPMENT_BRANCH,
                timeout=30.0,
            )
            remote_checked = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            fetch_detail = f"Could not verify origin: {exc}"

    ahead: int | None = None
    behind: int | None = None
    try:
        counts = _run_git(
            git,
            project_root,
            "rev-list",
            "--left-right",
            "--count",
            f"HEAD...origin/{DEVELOPMENT_BRANCH}",
        ).split()
        if len(counts) == 2:
            ahead, behind = (int(value) for value in counts)
            remote_checked = remote_checked or not fetch_remote
    except (OSError, ValueError):
        pass

    return BuildStatus(branch, revision, ahead, behind, remote_checked, fetch_detail)
