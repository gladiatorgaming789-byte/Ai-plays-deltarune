from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from deltarune_agent import safe_bootstrap


def test_bootstrap_blocks_wrong_branch_before_importing_controller():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        with patch.object(safe_bootstrap, "_find_git", return_value="git"), patch.object(
            safe_bootstrap,
            "_git",
            return_value="main",
        ):
            safe, detail = safe_bootstrap.verify_checkout(root)

    assert safe is False
    assert "Current branch: main" in detail
    assert safe_bootstrap.DEVELOPMENT_BRANCH in detail


def test_bootstrap_blocks_checkout_behind_origin():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        outputs = iter(
            [
                safe_bootstrap.DEVELOPMENT_BRANCH,
                "",
                "0 4",
            ]
        )
        with patch.object(safe_bootstrap, "_find_git", return_value="git"), patch.object(
            safe_bootstrap,
            "_git",
            side_effect=lambda *_args, **_kwargs: next(outputs),
        ):
            safe, detail = safe_bootstrap.verify_checkout(root)

    assert safe is False
    assert "4 commit(s) behind" in detail


def test_bootstrap_accepts_current_development_checkout():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        outputs = iter(
            [
                safe_bootstrap.DEVELOPMENT_BRANCH,
                "",
                "0 0",
            ]
        )
        with patch.object(safe_bootstrap, "_find_git", return_value="git"), patch.object(
            safe_bootstrap,
            "_git",
            side_effect=lambda *_args, **_kwargs: next(outputs),
        ):
            safe, detail = safe_bootstrap.verify_checkout(root)

    assert safe is True
    assert "up to date" in detail


def test_bootstrap_accepts_git_worktree_file():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        outputs = iter(
            [
                safe_bootstrap.DEVELOPMENT_BRANCH,
                "",
                "0 0",
            ]
        )
        with patch.object(safe_bootstrap, "_find_git", return_value="git"), patch.object(
            safe_bootstrap,
            "_git",
            side_effect=lambda *_args, **_kwargs: next(outputs),
        ):
            safe, detail = safe_bootstrap.verify_checkout(root)

    assert safe is True
    assert "up to date" in detail


def test_main_reports_gui_launch_failure():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        messages = []
        with patch.object(safe_bootstrap.sys, "argv", ["safe_bootstrap.py", str(root)]), patch.object(
            safe_bootstrap,
            "verify_checkout",
            return_value=(True, "verified"),
        ), patch.object(
            safe_bootstrap.subprocess,
            "call",
            side_effect=OSError("python failed"),
        ), patch.object(
            safe_bootstrap,
            "_message",
            side_effect=lambda title, body, **kwargs: messages.append(
                (title, body, kwargs)
            ),
        ):
            result = safe_bootstrap.main()

    assert result == 1
    assert messages
    assert messages[0][0] == "Deltarune Agent launch failed"
    assert "python failed" in messages[0][1]
    assert messages[0][2]["error"] is True
