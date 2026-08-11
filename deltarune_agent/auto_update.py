from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .build_status import find_git_executable


EXPECTED_REPOSITORY = "gladiatorgaming789-byte/Ai-plays-deltarune"
DISABLE_ENV = "DELTARUNE_AI_DISABLE_AUTO_UPDATE"
RESTARTED_ENV = "DELTARUNE_AI_UPDATE_RESTARTED"
UPDATED_FROM_ENV = "DELTARUNE_AI_UPDATED_FROM"
UPDATED_TO_ENV = "DELTARUNE_AI_UPDATED_TO"


@dataclass(frozen=True)
class UpdateResult:
    status: str
    branch: str | None
    current_sha: str | None = None
    remote_sha: str | None = None
    detail: str = ""
    changed_files: tuple[str, ...] = ()

    @property
    def updated(self) -> bool:
        return self.status == "updated"

    @property
    def update_available(self) -> bool:
        return self.status == "available"


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _run_git(
    git: str,
    project_root: Path,
    *args: str,
    timeout: float = 12.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [git, "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_creation_flags(),
        env=_git_env(),
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        raise OSError(detail)
    return result


def _origin_slug(url: str) -> str | None:
    value = url.strip().replace("\\", "/")
    lower = value.casefold()
    path = ""
    if lower.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    elif lower.startswith("ssh://git@github.com/"):
        path = value[len("ssh://git@github.com/") :]
    elif lower.startswith("https://github.com/"):
        path = value[len("https://github.com/") :]
    elif lower.startswith("http://github.com/"):
        path = value[len("http://github.com/") :]
    else:
        return None
    path = path.strip("/")
    if path.casefold().endswith(".git"):
        path = path[:-4]
    pieces = [piece for piece in path.split("/") if piece]
    if len(pieces) != 2:
        return None
    return "/".join(pieces).casefold()


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "unknown"


def _requirements_changed(changed_files: tuple[str, ...]) -> bool:
    return "requirements.txt" in {item.replace("\\", "/") for item in changed_files}


def _install_remote_requirements(
    git: str,
    project_root: Path,
    remote_ref: str,
) -> None:
    shown = _run_git(
        git,
        project_root,
        "show",
        f"{remote_ref}:requirements.txt",
        timeout=12.0,
    )
    with tempfile.TemporaryDirectory(prefix="deltarune-ai-update-") as temporary:
        requirements = Path(temporary) / "requirements.txt"
        requirements.write_text(
            shown.stdout.replace("\r\n", "\n").replace("\r", "\n"),
            encoding="utf-8",
            newline="\n",
        )
        env = os.environ.copy()
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True,
            text=True,
            timeout=600.0,
            creationflags=_creation_flags(),
            env=env,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "pip install failed").strip()
            raise OSError(
                "The update changes requirements.txt, but dependency installation "
                f"failed before any project files were updated: {detail}"
            )


def check_for_update(
    project_root: Path,
    *,
    expected_repository: str | None = EXPECTED_REPOSITORY,
) -> UpdateResult:
    project_root = project_root.resolve()
    git = find_git_executable()
    if git is None:
        return UpdateResult(
            "skipped",
            None,
            detail="Git was not found, so automatic updating was skipped.",
        )
    if not (project_root / ".git").exists():
        return UpdateResult(
            "skipped",
            None,
            detail="This copy is not a Git checkout, so automatic updating was skipped.",
        )

    try:
        branch = _run_git(git, project_root, "branch", "--show-current").stdout.strip()
        if not branch:
            return UpdateResult(
                "skipped",
                None,
                detail="The checkout is detached; automatic updating was skipped.",
            )

        origin = _run_git(git, project_root, "remote", "get-url", "origin").stdout.strip()
        if expected_repository is not None:
            slug = _origin_slug(origin)
            if slug != expected_repository.casefold():
                return UpdateResult(
                    "skipped",
                    branch,
                    detail=(
                        "Automatic updating was skipped because origin does not point "
                        f"to {expected_repository}."
                    ),
                )

        dirty = _run_git(
            git,
            project_root,
            "status",
            "--porcelain",
            "--untracked-files=no",
        ).stdout.strip()
        if dirty:
            return UpdateResult(
                "skipped",
                branch,
                detail=(
                    "Tracked local edits are present. They were left untouched; "
                    "commit, stash, or discard them before automatic updating."
                ),
            )

        _run_git(
            git,
            project_root,
            "fetch",
            "--quiet",
            "origin",
            branch,
            timeout=20.0,
        )
        remote_ref = f"refs/remotes/origin/{branch}"
        current_sha = _run_git(git, project_root, "rev-parse", "HEAD").stdout.strip()
        remote_sha = _run_git(git, project_root, "rev-parse", remote_ref).stdout.strip()

        if current_sha == remote_sha:
            return UpdateResult(
                "current",
                branch,
                current_sha,
                remote_sha,
                f"{branch} is already up to date.",
            )

        ancestor = _run_git(
            git,
            project_root,
            "merge-base",
            "--is-ancestor",
            current_sha,
            remote_sha,
            check=False,
        )
        if ancestor.returncode != 0:
            return UpdateResult(
                "skipped",
                branch,
                current_sha,
                remote_sha,
                detail=(
                    "The local and remote branch histories are not a clean fast-forward. "
                    "Nothing was changed automatically."
                ),
            )

        changed = tuple(
            line.strip()
            for line in _run_git(
                git,
                project_root,
                "diff",
                "--name-only",
                f"{current_sha}..{remote_sha}",
            ).stdout.splitlines()
            if line.strip()
        )
        return UpdateResult(
            "available",
            branch,
            current_sha,
            remote_sha,
            detail=(
                f"Update available on {branch}: {_short(current_sha)} -> "
                f"{_short(remote_sha)} ({len(changed)} changed file(s))."
            ),
            changed_files=changed,
        )
    except subprocess.TimeoutExpired:
        return UpdateResult(
            "error",
            None,
            detail="The update check timed out; the installed copy was left unchanged.",
        )
    except OSError as exc:
        return UpdateResult(
            "error",
            None,
            detail=f"Could not check for updates: {exc}",
        )


def apply_update(
    project_root: Path,
    *,
    expected_repository: str | None = EXPECTED_REPOSITORY,
) -> UpdateResult:
    project_root = project_root.resolve()
    checked = check_for_update(
        project_root,
        expected_repository=expected_repository,
    )
    if not checked.update_available:
        return checked

    git = find_git_executable()
    if git is None or checked.branch is None or checked.remote_sha is None:
        return UpdateResult(
            "error",
            checked.branch,
            checked.current_sha,
            checked.remote_sha,
            detail="Git became unavailable before the update could be applied.",
            changed_files=checked.changed_files,
        )

    remote_ref = f"refs/remotes/origin/{checked.branch}"
    try:
        if _requirements_changed(checked.changed_files):
            _install_remote_requirements(git, project_root, remote_ref)

        _run_git(
            git,
            project_root,
            "merge",
            "--ff-only",
            remote_ref,
            timeout=45.0,
        )
        new_sha = _run_git(git, project_root, "rev-parse", "HEAD").stdout.strip()
        if new_sha != checked.remote_sha:
            raise OSError(
                "Git reported success but HEAD does not match the fetched remote commit."
            )
        return UpdateResult(
            "updated",
            checked.branch,
            checked.current_sha,
            new_sha,
            detail=(
                f"Automatically updated {checked.branch} from "
                f"{_short(checked.current_sha)} to {_short(new_sha)}."
            ),
            changed_files=checked.changed_files,
        )
    except subprocess.TimeoutExpired:
        return UpdateResult(
            "error",
            checked.branch,
            checked.current_sha,
            checked.remote_sha,
            detail=(
                "The update operation timed out. Git was not force-reset; inspect the "
                "checkout before retrying."
            ),
            changed_files=checked.changed_files,
        )
    except OSError as exc:
        return UpdateResult(
            "error",
            checked.branch,
            checked.current_sha,
            checked.remote_sha,
            detail=f"Automatic update was not applied: {exc}",
            changed_files=checked.changed_files,
        )


def maybe_auto_update(project_root: Path, relaunch_args: list[str]) -> UpdateResult:
    if os.environ.get(DISABLE_ENV, "").strip().casefold() in {"1", "true", "yes", "on"}:
        result = UpdateResult(
            "skipped",
            None,
            detail=f"Automatic updates are disabled by {DISABLE_ENV}.",
        )
        print(f"[Updater] {result.detail}")
        return result

    if os.environ.get(RESTARTED_ENV) == "1":
        old_sha = os.environ.get(UPDATED_FROM_ENV)
        new_sha = os.environ.get(UPDATED_TO_ENV)
        result = UpdateResult(
            "updated",
            None,
            old_sha,
            new_sha,
            detail=f"Automatic update completed: {_short(old_sha)} -> {_short(new_sha)}.",
        )
        print(f"[Updater] {result.detail}")
        return result

    result = apply_update(project_root)
    print(f"[Updater] {result.detail}")
    if result.updated:
        os.environ[RESTARTED_ENV] = "1"
        os.environ[UPDATED_FROM_ENV] = result.current_sha or ""
        os.environ[UPDATED_TO_ENV] = result.remote_sha or ""
        os.execv(
            sys.executable,
            [sys.executable, "-m", "deltarune_agent", *relaunch_args],
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely check or fast-forward this Deltarune AI checkout."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply a clean fast-forward update instead of only checking",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git checkout to inspect (default: this project)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = (
        apply_update(args.project_root)
        if args.apply
        else check_for_update(args.project_root)
    )
    print(result.detail)
    return 1 if result.status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
