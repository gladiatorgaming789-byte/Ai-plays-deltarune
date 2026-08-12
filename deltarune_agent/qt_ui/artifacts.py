"""Small, defensive readers for potentially large run artifact folders."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Iterator, Mapping


SMALL_JSON_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class RunSummary:
    directory: Path
    name: str
    status: str
    started_at: str
    ended_at: str
    duration_seconds: float | None
    stop_reason: str
    last_step: int | None
    events: int
    predictions: int
    navigation_updates: int
    rooms: int | None
    story_progress: int | None
    total_reward: float | None
    warning_count: int
    error: str = ""

    @property
    def timestamp(self) -> float:
        try:
            return datetime.fromisoformat(self.started_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return self.directory.stat().st_mtime
            except OSError:
                return 0.0


def _small_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    if path.stat().st_size > SMALL_JSON_LIMIT:
        raise ValueError(f"{path.name} is unexpectedly large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nested(mapping: Mapping[str, object], *keys: str) -> object:
    current: object = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def load_run_summary(directory: Path) -> RunSummary:
    try:
        manifest = _small_json(directory / "run.json")
        summary = _small_json(directory / "summary.json")
        report = _small_json(directory / "run_report.json")
        recording = manifest.get("recording")
        if not isinstance(recording, Mapping):
            recording = {}
        warnings = manifest.get("warnings")
        warning_count = len(warnings) if isinstance(warnings, list) else 0

        def first(*values: object) -> object:
            return next((value for value in values if value is not None), None)

        rooms = first(
            summary.get("rooms_discovered"),
            summary.get("unique_rooms"),
            _nested(report, "progress", "rooms_discovered"),
            _nested(report, "navigation", "rooms"),
        )
        story = first(
            summary.get("story_progress_events"),
            _nested(summary, "progress", "story_progress_events"),
            _nested(report, "progress", "story_progress_events"),
        )
        reward = first(
            summary.get("reinforcement_total_reward"),
            summary.get("total_reward"),
            _nested(summary, "reinforcement", "total_reward"),
            _nested(report, "reinforcement", "total_reward"),
        )
        return RunSummary(
            directory=directory,
            name=directory.name,
            status=str(manifest.get("status") or summary.get("status") or "unknown"),
            started_at=str(manifest.get("started_at") or summary.get("started_at") or ""),
            ended_at=str(manifest.get("ended_at") or summary.get("ended_at") or ""),
            duration_seconds=_number(
                first(manifest.get("duration_seconds"), summary.get("duration_seconds"))
            ),
            stop_reason=str(manifest.get("stop_reason") or summary.get("stop_reason") or ""),
            last_step=_integer(first(recording.get("last_step"), summary.get("last_step"))),
            events=_integer(recording.get("events")) or 0,
            predictions=_integer(recording.get("predictions")) or 0,
            navigation_updates=_integer(recording.get("navigation_updates")) or 0,
            rooms=_integer(rooms),
            story_progress=_integer(story),
            total_reward=_number(reward),
            warning_count=warning_count,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return RunSummary(
            directory=directory,
            name=directory.name,
            status="unreadable",
            started_at="",
            ended_at="",
            duration_seconds=None,
            stop_reason="",
            last_step=None,
            events=0,
            predictions=0,
            navigation_updates=0,
            rooms=None,
            story_progress=None,
            total_reward=None,
            warning_count=1,
            error=str(exc),
        )


def scan_runs(root: Path, *, limit: int | None = None) -> list[RunSummary]:
    if not root.is_dir():
        return []
    values = [load_run_summary(path) for path in root.iterdir() if path.is_dir()]
    values.sort(key=lambda run: (run.timestamp, run.name), reverse=True)
    return values[:limit] if limit is not None else values


def iter_jsonl(
    path: Path,
    *,
    start: int = 0,
    limit: int = 200,
) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield a page without ever loading the whole JSONL file."""

    if start < 0 or limit < 0:
        raise ValueError("start and limit must be non-negative")
    if not path.is_file() or limit == 0:
        return
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream):
            if line_number < start:
                continue
            if line_number >= start + limit:
                break
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield line_number, value


def tail_jsonl(path: Path, *, limit: int = 200) -> list[tuple[int, dict[str, object]]]:
    """Read a bounded tail with original line numbers."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    if not path.is_file() or limit == 0:
        return []
    lines: deque[tuple[int, str]] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream):
            lines.append((line_number, line))
    result: list[tuple[int, dict[str, object]]] = []
    for line_number, line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append((line_number, value))
    return result
