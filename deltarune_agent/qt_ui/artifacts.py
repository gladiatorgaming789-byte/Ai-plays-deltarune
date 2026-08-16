"""Small, defensive readers for potentially large run artifact folders."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from ..autonomy_shadow import replay_prediction_snapshots


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
    training_status: str = ""
    recommended_winner: str = ""

    @property
    def timestamp(self) -> float:
        try:
            return datetime.fromisoformat(self.started_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return self.directory.stat().st_mtime
            except OSError:
                return 0.0


@dataclass(frozen=True)
class AutonomyOptionSummary:
    option_id: str
    kind: str
    required_level: str
    score: float | None
    selected: bool
    confidence: float | None
    information_value: float | None
    novelty: float | None
    distance: float | None
    loop_risk: float | None
    failure_cost: float | None
    budget_spent: int
    budget_limit: int
    budget_remaining: int
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class AutonomyWorkbenchSummary:
    available: bool
    latest_step: int | None
    latest_room: str
    version: int | None
    recovery_level: str
    recovery_reason: str
    recovery_level_age: int
    story_stall_steps: int
    active_goal_id: str
    active_goal_kind: str
    active_goal_age: int
    selected_option_id: str
    commitment_hold: bool
    active_budget: Mapping[str, object]
    coherence: Mapping[str, object]
    options: tuple[AutonomyOptionSummary, ...]
    shadow: Mapping[str, object]


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
        training_status = ""
        recommended_winner = ""
        training_path = directory / "training_manifest.json"
        if training_path.is_file():
            try:
                training = _small_json(training_path)
                training_status = str(training.get("status") or "unknown")
                eligibility = training.get("eligibility")
                if isinstance(eligibility, Mapping):
                    recommended_winner = str(
                        eligibility.get("recommended_winner") or ""
                    )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                training_status = "unreadable"
                warning_count += 1

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
            training_status=training_status,
            recommended_winner=recommended_winner,
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


def summarize_autonomy_predictions(
    predictions: Iterable[Mapping[str, object]],
) -> AutonomyWorkbenchSummary:
    """Build a bounded, read-only Autonomy view from loaded prediction records."""

    records = [record for record in predictions if isinstance(record, Mapping)]
    shadow = replay_prediction_snapshots(records)
    latest_record: Mapping[str, object] | None = None
    latest_snapshot: Mapping[str, object] | None = None
    latest_autonomy: Mapping[str, object] | None = None
    for record in reversed(records):
        snapshot = record.get("prediction_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        autonomy = snapshot.get("autonomy")
        if not isinstance(autonomy, Mapping):
            continue
        latest_record = record
        latest_snapshot = snapshot
        latest_autonomy = autonomy
        break

    if latest_autonomy is None or latest_record is None or latest_snapshot is None:
        return AutonomyWorkbenchSummary(
            available=False,
            latest_step=None,
            latest_room="",
            version=None,
            recovery_level="",
            recovery_reason="",
            recovery_level_age=0,
            story_stall_steps=0,
            active_goal_id="",
            active_goal_kind="",
            active_goal_age=0,
            selected_option_id="",
            commitment_hold=False,
            active_budget={},
            coherence={},
            options=(),
            shadow=shadow,
        )

    selected_id = str(latest_autonomy.get("selected_option_id") or "")
    raw_options = latest_autonomy.get("ranked_options")
    options: list[AutonomyOptionSummary] = []
    if isinstance(raw_options, list):
        for raw in raw_options:
            if not isinstance(raw, Mapping):
                continue
            option_id = str(raw.get("id") or "")
            metadata = raw.get("metadata")
            options.append(
                AutonomyOptionSummary(
                    option_id=option_id,
                    kind=str(raw.get("kind") or "unknown"),
                    required_level=str(raw.get("required_level") or "unknown"),
                    score=_number(raw.get("score")),
                    selected=bool(raw.get("selected")) or option_id == selected_id,
                    confidence=_number(raw.get("confidence")),
                    information_value=_number(raw.get("information_value")),
                    novelty=_number(raw.get("novelty")),
                    distance=_number(raw.get("distance")),
                    loop_risk=_number(raw.get("loop_risk")),
                    failure_cost=_number(raw.get("failure_cost")),
                    budget_spent=_integer(raw.get("budget_spent")) or 0,
                    budget_limit=_integer(raw.get("budget_limit")) or 0,
                    budget_remaining=_integer(raw.get("budget_remaining")) or 0,
                    metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
                )
            )
    active_budget = latest_autonomy.get("active_budget")
    coherence = latest_autonomy.get("coherence")
    return AutonomyWorkbenchSummary(
        available=True,
        latest_step=_integer(latest_record.get("step")),
        latest_room=str(latest_snapshot.get("room") or latest_record.get("room") or ""),
        version=_integer(latest_autonomy.get("version")),
        recovery_level=str(latest_autonomy.get("recovery_level") or "unknown"),
        recovery_reason=str(latest_autonomy.get("recovery_reason") or ""),
        recovery_level_age=_integer(latest_autonomy.get("recovery_level_age")) or 0,
        story_stall_steps=_integer(latest_autonomy.get("story_stall_steps")) or 0,
        active_goal_id=str(latest_autonomy.get("active_goal_id") or ""),
        active_goal_kind=str(latest_autonomy.get("active_goal_kind") or ""),
        active_goal_age=_integer(latest_autonomy.get("active_goal_age")) or 0,
        selected_option_id=selected_id,
        commitment_hold=bool(latest_autonomy.get("commitment_hold")),
        active_budget=(
            dict(active_budget) if isinstance(active_budget, Mapping) else {}
        ),
        coherence=(dict(coherence) if isinstance(coherence, Mapping) else {}),
        options=tuple(options),
        shadow=shadow,
    )
