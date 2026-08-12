from __future__ import annotations

import json
from pathlib import Path

import pytest

import deltarune_agent.bootstrap_dependencies as bootstrap


def _write_requirements(
    root: Path,
    text: str = "Pillow>=10\nPyAutoGUI>=0.9.54\n",
) -> Path:
    path = root / "requirements.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    (root / ".venv").mkdir(exist_ok=True)
    return path


def test_requirements_fingerprint_changes_with_dependency_list(tmp_path: Path) -> None:
    path = _write_requirements(tmp_path, "A==1\n")
    first = bootstrap.requirements_fingerprint(path)
    path.write_text("A==2\n", encoding="utf-8", newline="\n")
    second = bootstrap.requirements_fingerprint(path)
    assert first != second


def test_declared_distributions_follow_branch_requirements(tmp_path: Path) -> None:
    path = _write_requirements(
        tmp_path,
        "# UI dependencies\nPillow>=10,<12\nPyAutoGUI>=0.9.54,<1\nPySide6>=6.8.1,<7\n",
    )
    assert bootstrap.declared_distributions(path) == (
        "Pillow",
        "PyAutoGUI",
        "PySide6",
    )


def test_dependencies_current_accepts_matching_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = _write_requirements(tmp_path)
    fingerprint = bootstrap.requirements_fingerprint(requirements)
    bootstrap._write_marker(bootstrap.marker_path(tmp_path), fingerprint)
    monkeypatch.setattr(
        bootstrap,
        "missing_required_packages",
        lambda _requirements: [],
    )

    current, reason = bootstrap.dependencies_current(tmp_path)

    assert current is True
    assert reason == "dependencies are current"


def test_dependencies_current_rejects_missing_required_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = _write_requirements(tmp_path, "PySide6>=6.8.1,<7\n")
    fingerprint = bootstrap.requirements_fingerprint(requirements)
    bootstrap._write_marker(bootstrap.marker_path(tmp_path), fingerprint)
    monkeypatch.setattr(
        bootstrap,
        "missing_required_packages",
        lambda _requirements: ["PySide6"],
    )

    current, reason = bootstrap.dependencies_current(tmp_path)

    assert current is False
    assert "PySide6" in reason


def test_ensure_dependencies_installs_checks_and_records_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = _write_requirements(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bootstrap,
        "dependencies_current",
        lambda _root: (False, "dependency marker is missing"),
    )
    monkeypatch.setattr(bootstrap, "_ensure_pip", lambda _root: None)
    monkeypatch.setattr(
        bootstrap,
        "_run",
        lambda command, *, cwd: calls.append(command),
    )
    monkeypatch.setattr(
        bootstrap,
        "missing_required_packages",
        lambda _requirements: [],
    )

    changed = bootstrap.ensure_dependencies(
        tmp_path,
        require_virtual_environment=False,
    )

    assert changed is True
    assert any("install" in command and "-r" in command for command in calls)
    assert any(command[-2:] == ["pip", "check"] for command in calls)
    marker = json.loads(
        bootstrap.marker_path(tmp_path).read_text(encoding="utf-8")
    )
    assert marker["requirements_sha256"] == bootstrap.requirements_fingerprint(
        requirements
    )


def test_failed_install_never_marks_environment_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_requirements(tmp_path)
    monkeypatch.setattr(
        bootstrap,
        "dependencies_current",
        lambda _root: (False, "dependency marker is missing"),
    )
    monkeypatch.setattr(bootstrap, "_ensure_pip", lambda _root: None)

    def fail(_command: list[str], *, cwd: Path) -> None:
        raise RuntimeError("simulated install failure")

    monkeypatch.setattr(bootstrap, "_run", fail)

    with pytest.raises(RuntimeError, match="simulated install failure"):
        bootstrap.ensure_dependencies(
            tmp_path,
            require_virtual_environment=False,
        )

    assert not bootstrap.marker_path(tmp_path).exists()


def test_windows_launcher_bootstraps_before_gui_without_importing_package() -> None:
    launcher = (
        Path(__file__).resolve().parents[1] / "Start AI GUI.bat"
    ).read_text(encoding="utf-8")
    assert "-m venv .venv" in launcher
    assert '"deltarune_agent\\bootstrap_dependencies.py"' in launcher
    assert "-m deltarune_agent.bootstrap_dependencies" not in launcher
    assert launcher.index("bootstrap_dependencies.py") < launcher.index(
        "-m deltarune_agent gui"
    )
    assert "if %errorlevel% equ" not in launcher.casefold()
