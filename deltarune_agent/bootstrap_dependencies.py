from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


MIN_PYTHON = (3, 11)
MARKER_NAME = ".deltarune-ai-dependencies.json"
REQUIREMENT_NAME_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)")
IMPORT_BY_DISTRIBUTION = {
    "pillow": "PIL",
    "pyautogui": "pyautogui",
    "pyside6": "PySide6",
}


def requirements_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def marker_path(project_root: Path) -> Path:
    return project_root / ".venv" / MARKER_NAME


def declared_distributions(requirements: Path) -> tuple[str, ...]:
    result: list[str] = []
    for raw_line in requirements.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http:", "https:", "git+")):
            continue
        match = REQUIREMENT_NAME_PATTERN.match(line)
        if match is not None:
            result.append(match.group(1))
    return tuple(result)


def missing_required_packages(requirements: Path) -> list[str]:
    missing: list[str] = []
    importlib.invalidate_caches()
    for distribution in declared_distributions(requirements):
        try:
            importlib.metadata.distribution(distribution)
        except importlib.metadata.PackageNotFoundError:
            missing.append(distribution)
            continue
        normalized = distribution.casefold().replace("_", "-").replace(".", "-")
        import_name = IMPORT_BY_DISTRIBUTION.get(normalized)
        if import_name and importlib.util.find_spec(import_name) is None:
            missing.append(distribution)
    return missing


def _read_marker(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_marker(path: Path, fingerprint: str) -> None:
    payload = {
        "requirements_sha256": fingerprint,
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_executable": sys.executable,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def dependencies_current(project_root: Path) -> tuple[bool, str]:
    requirements = project_root / "requirements.txt"
    if not requirements.is_file():
        return False, "requirements.txt is missing"
    marker = _read_marker(marker_path(project_root))
    if marker is None:
        return False, "dependency marker is missing"
    expected = requirements_fingerprint(requirements)
    if marker.get("requirements_sha256") != expected:
        return False, "requirements.txt changed"
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if marker.get("python_major_minor") != current_python:
        return False, "Python version changed"
    missing = missing_required_packages(requirements)
    if missing:
        return False, "missing project packages: " + ", ".join(missing)
    return True, "dependencies are current"


def _run(command: list[str], *, cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {result.returncode}: "
            + " ".join(command)
        )


def _ensure_pip(project_root: Path) -> None:
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        return
    print("[Setup] pip is missing; enabling it in the project environment...")
    _run([sys.executable, "-m", "ensurepip", "--upgrade"], cwd=project_root)


def ensure_dependencies(
    project_root: Path,
    *,
    require_virtual_environment: bool = True,
) -> bool:
    project_root = project_root.resolve()
    if sys.version_info < MIN_PYTHON:
        raise RuntimeError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required; "
            f"this interpreter is {sys.version_info.major}.{sys.version_info.minor}."
        )
    if require_virtual_environment and sys.prefix == sys.base_prefix:
        raise RuntimeError(
            "Dependency bootstrap must run inside the project .venv. "
            "Use Start AI GUI.bat so the environment is created safely."
        )

    current, reason = dependencies_current(project_root)
    if current:
        print("[Setup] Project dependencies are ready.")
        return False

    requirements = project_root / "requirements.txt"
    if not requirements.is_file():
        raise RuntimeError(f"Missing dependency list: {requirements}")

    print(f"[Setup] Preparing project dependencies ({reason})...")
    _ensure_pip(project_root)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ],
        cwd=project_root,
    )
    _run([sys.executable, "-m", "pip", "check"], cwd=project_root)

    missing = missing_required_packages(requirements)
    if missing:
        raise RuntimeError(
            "Dependency installation completed but required packages are still "
            "unavailable: " + ", ".join(missing)
        )

    fingerprint = requirements_fingerprint(requirements)
    _write_marker(marker_path(project_root), fingerprint)
    print("[Setup] Project dependencies installed successfully.")
    return True


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or verify the AI Plays Deltarune project dependencies."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether the current project environment is ready",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = _project_root()
    if args.check:
        current, reason = dependencies_current(root)
        print(reason)
        return 0 if current else 1
    try:
        ensure_dependencies(root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[Setup] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
