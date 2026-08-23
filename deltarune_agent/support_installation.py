"""Detect and explain the installed Deltarune AI Support package state.

This module is intentionally stdlib-only so it can run during project setup,
before the main GUI dependencies are installed.  It never modifies data.win;
it only inspects the installed Chapter 1-5 files and produces actionable
repair guidance.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re


REQUIRED_MARKERS = (
    (b"AI_MULTI_INSTANCE|1|", "multi-instance training"),
    (b"DRTEL|9|", "telemetry v9"),
    (b"AI_SPEED_MOD|1|", "AI Speed"),
    (b"AI_BACKGROUND_AUTOSAVE_V2", "training-only autosave safety"),
)

EXPECTED_PACKAGE = "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip"


@dataclass(frozen=True)
class ChapterSupportStatus:
    chapter: int
    data_file: Path
    exists: bool
    missing_markers: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return self.exists and not self.missing_markers


@dataclass(frozen=True)
class SupportInstallationStatus:
    game_root: Path | None
    chapters: tuple[ChapterSupportStatus, ...]
    package_path: Path | None

    @property
    def healthy(self) -> bool:
        return bool(self.chapters) and all(chapter.healthy for chapter in self.chapters)

    @property
    def missing_chapters(self) -> tuple[ChapterSupportStatus, ...]:
        return tuple(chapter for chapter in self.chapters if not chapter.healthy)


def _file_contains(path: Path, marker: bytes) -> bool:
    overlap = max(0, len(marker) - 1)
    previous = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            combined = previous + block
            if marker in combined:
                return True
            previous = combined[-overlap:] if overlap else b""
    return False


def discover_game_root(explicit: Path | None = None) -> Path | None:
    """Find the installed DELTARUNE root without assuming one Steam library."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    env_root = os.environ.get("DELTARUNE_GAME_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())

    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    steam_root = program_files_x86 / "Steam"
    candidates.append(steam_root / "steamapps" / "common" / "DELTARUNE")

    library_file = steam_root / "config" / "libraryfolders.vdf"
    if library_file.is_file():
        try:
            text = library_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for value in re.findall(r'"path"\s*"([^"]+)"', text):
            candidates.append(
                Path(value.replace("\\\\", "\\"))
                / "steamapps"
                / "common"
                / "DELTARUNE"
            )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if (resolved / "DELTARUNE.exe").is_file():
            return resolved
    return None


def inspect_installation(
    game_root: Path | None = None,
    *,
    package_path: Path | None = None,
    chapters: range = range(1, 6),
) -> SupportInstallationStatus:
    root = discover_game_root(game_root) if game_root is None else Path(game_root).expanduser().resolve()
    statuses: list[ChapterSupportStatus] = []
    if root is not None:
        for chapter in chapters:
            data_file = root / f"chapter{chapter}_windows" / "data.win"
            if not data_file.is_file():
                statuses.append(ChapterSupportStatus(chapter, data_file, False, ("data.win",)))
                continue
            missing: list[str] = []
            for marker, description in REQUIRED_MARKERS:
                try:
                    present = _file_contains(data_file, marker)
                except OSError:
                    present = False
                if not present:
                    missing.append(description)
            statuses.append(ChapterSupportStatus(chapter, data_file, True, tuple(missing)))

    if package_path is None:
        package_path = Path(__file__).resolve().parents[1] / "mods" / "support" / "deltamod" / EXPECTED_PACKAGE
    package_path = package_path.resolve()
    return SupportInstallationStatus(root, tuple(statuses), package_path if package_path.is_file() else None)


def format_guidance(status: SupportInstallationStatus) -> str:
    if status.game_root is None:
        return (
            "Deltarune could not be located automatically. Set DELTARUNE_GAME_ROOT "
            "to the folder containing DELTARUNE.exe, then run Setup again."
        )
    if status.healthy:
        return f"AI Support 2.0.1 is installed correctly in {status.game_root}."

    lines = [
        "The installed Deltarune data.win files are not ready for Population Training.",
        f"Game root: {status.game_root}",
    ]
    for chapter in status.missing_chapters:
        if not chapter.exists:
            detail = "data.win is missing"
        else:
            detail = "missing: " + ", ".join(chapter.missing_markers)
        lines.append(f"  Chapter {chapter.chapter}: {detail}")

    lines.extend(
        [
            "",
            "Repair:",
            "  1. Close Deltarune and DeltaMod.",
            "  2. Re-import the current AI Support package into the Deltarune installation.",
            f"  3. Use: {status.package_path if status.package_path else EXPECTED_PACKAGE}",
            "  4. Re-run Setup/Population Training so the installation is checked again.",
            "",
            "Do not bypass this check: the current package includes training-only autosave protection.",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    status = inspect_installation()
    print(format_guidance(status))
    return 0 if status.healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
