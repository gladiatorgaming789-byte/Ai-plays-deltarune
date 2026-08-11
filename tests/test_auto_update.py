from __future__ import annotations

from pathlib import Path
import subprocess

from deltarune_agent.auto_update import (
    _origin_slug,
    apply_update,
    check_for_update,
)


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> None:
    _git("add", "-A", cwd=repository)
    _git("commit", "-m", message, cwd=repository)


def _repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    checkout = tmp_path / "checkout"

    _git("init", "--bare", str(remote))
    _git("init", "-b", "development", str(publisher))
    _git("config", "user.email", "tests@example.invalid", cwd=publisher)
    _git("config", "user.name", "Updater Tests", cwd=publisher)
    (publisher / "tracked.txt").write_text("base\n", encoding="utf-8")
    _commit(publisher, "base")
    _git("remote", "add", "origin", str(remote), cwd=publisher)
    _git("push", "-u", "origin", "development", cwd=publisher)

    _git("clone", "--branch", "development", str(remote), str(checkout))
    _git("config", "user.email", "tests@example.invalid", cwd=checkout)
    _git("config", "user.name", "Updater Tests", cwd=checkout)
    return remote, publisher, checkout


def test_origin_slug_accepts_expected_github_urls() -> None:
    expected = "gladiatorgaming789-byte/ai-plays-deltarune"
    assert (
        _origin_slug(
            "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune.git"
        )
        == expected
    )
    assert (
        _origin_slug(
            "git@github.com:gladiatorgaming789-byte/Ai-plays-deltarune.git"
        )
        == expected
    )
    assert _origin_slug("https://example.com/not-this-repo.git") is None


def test_auto_update_fast_forwards_and_preserves_untracked_memory(
    tmp_path: Path,
) -> None:
    _remote, publisher, checkout = _repositories(tmp_path)
    memory = checkout / "memory" / "navigation.json"
    memory.parent.mkdir()
    memory.write_text('{"learned": true}\n', encoding="utf-8")

    (publisher / "tracked.txt").write_text("updated\n", encoding="utf-8")
    _commit(publisher, "remote update")
    _git("push", cwd=publisher)

    checked = check_for_update(checkout, expected_repository=None)
    assert checked.status == "available"
    assert checked.changed_files == ("tracked.txt",)

    result = apply_update(checkout, expected_repository=None)
    assert result.status == "updated"
    assert (checkout / "tracked.txt").read_text(encoding="utf-8") == "updated\n"
    assert memory.read_text(encoding="utf-8") == '{"learned": true}\n'


def test_auto_update_refuses_tracked_local_edits(tmp_path: Path) -> None:
    _remote, publisher, checkout = _repositories(tmp_path)
    (checkout / "tracked.txt").write_text("local edit\n", encoding="utf-8")

    (publisher / "tracked.txt").write_text("remote edit\n", encoding="utf-8")
    _commit(publisher, "remote update")
    _git("push", cwd=publisher)

    result = apply_update(checkout, expected_repository=None)
    assert result.status == "skipped"
    assert "Tracked local edits" in result.detail
    assert (checkout / "tracked.txt").read_text(encoding="utf-8") == "local edit\n"


def test_auto_update_refuses_diverged_history(tmp_path: Path) -> None:
    _remote, publisher, checkout = _repositories(tmp_path)

    (checkout / "tracked.txt").write_text("local commit\n", encoding="utf-8")
    _commit(checkout, "local update")

    (publisher / "remote.txt").write_text("remote commit\n", encoding="utf-8")
    _commit(publisher, "remote update")
    _git("push", cwd=publisher)

    result = apply_update(checkout, expected_repository=None)
    assert result.status == "skipped"
    assert "not a clean fast-forward" in result.detail
    assert not (checkout / "remote.txt").exists()


def test_auto_update_refuses_unexpected_github_origin(tmp_path: Path) -> None:
    _remote, _publisher, checkout = _repositories(tmp_path)
    result = check_for_update(
        checkout,
        expected_repository="someone-else/not-this-project",
    )
    assert result.status == "skipped"
    assert "origin does not point" in result.detail
