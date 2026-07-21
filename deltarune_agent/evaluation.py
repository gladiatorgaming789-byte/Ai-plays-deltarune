from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RunMetrics:
    steps: int = 0
    rooms_seen: int = 0
    unique_cells: int = 0
    room_transitions: int = 0
    room_bounces: int = 0
    story_progress_events: int = 0
    interactions_attempted: int = 0
    repeated_actions: int = 0
    unknown_steps: int = 0
    low_confidence_steps: int = 0
    battle_steps: int = 0
    menu_steps: int = 0
    invalid_visual_steps: int = 0
    telemetry_coverage: float = 0.0
    visual_coverage: float = 0.0
    transition_rate: float = 0.0
    exploration_efficiency: float = 0.0

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def load_events(run_directory: Path) -> list[dict[str, object]]:
    path = run_directory / "events.jsonl"
    events: list[dict[str, object]] = []
    if not path.exists():
        raise FileNotFoundError(f"Run log not found: {path}")
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on {path}:{line_number}: {exc}"
            ) from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def calculate_metrics(
    events: Iterable[dict[str, object]],
) -> RunMetrics:
    rows = list(events)
    rooms: set[str] = set()
    cells: set[tuple[str, int, int]] = set()
    room_trace: list[str] = []
    transitions = 0
    story_progress = 0
    interactions = 0
    repeated_actions = 0
    unknown_steps = 0
    low_confidence = 0
    battle_steps = 0
    menu_steps = 0
    telemetry_steps = 0
    invalid_visual_steps = 0
    previous_room: str | None = None
    previous_action: str | None = None
    action_streak = 0

    for event in rows:
        state = str(event.get("state") or "unknown")
        confidence = float(event.get("confidence") or 0.0)
        action = str(event.get("action") or "wait")
        reason = str(event.get("reason") or "").casefold()
        telemetry = event.get("telemetry")

        if state == "unknown":
            unknown_steps += 1
        if confidence < 0.60:
            low_confidence += 1
        if state == "battle":
            battle_steps += 1
        if state == "menu":
            menu_steps += 1
        if event.get("visual_valid") is False:
            invalid_visual_steps += 1
        if (
            "try interaction" in reason
            or ("interact" in reason and action == "confirm")
        ):
            interactions += 1
        if (
            "story progress" in reason
            or "scripted sequence" in reason
        ):
            story_progress += 1

        if action == previous_action:
            action_streak += 1
            if action_streak >= 3:
                repeated_actions += 1
        else:
            action_streak = 0
            previous_action = action

        if isinstance(telemetry, dict):
            telemetry_steps += 1
            room = str(
                telemetry.get("room_name")
                or telemetry.get("room_id")
                or "unknown"
            )
            rooms.add(room)
            x = telemetry.get("player_x", telemetry.get("x"))
            y = telemetry.get("player_y", telemetry.get("y"))
            try:
                cells.add(
                    (
                        room,
                        int(float(x) // 8),
                        int(float(y) // 8),
                    )
                )
            except (TypeError, ValueError):
                pass
            if previous_room is not None and room != previous_room:
                transitions += 1
                room_trace.append(room)
            elif not room_trace:
                room_trace.append(room)
            previous_room = room

    bounces = sum(
        room_trace[index] == room_trace[index - 2]
        for index in range(2, len(room_trace))
    )
    steps = len(rows)
    telemetry_coverage = (
        telemetry_steps / steps if steps else 0.0
    )
    visual_coverage = (
        (steps - invalid_visual_steps) / steps
        if steps
        else 0.0
    )
    transition_rate = transitions / steps if steps else 0.0
    efficiency = len(cells) / steps if steps else 0.0
    return RunMetrics(
        steps=steps,
        rooms_seen=len(rooms),
        unique_cells=len(cells),
        room_transitions=transitions,
        room_bounces=bounces,
        story_progress_events=story_progress,
        interactions_attempted=interactions,
        repeated_actions=repeated_actions,
        unknown_steps=unknown_steps,
        low_confidence_steps=low_confidence,
        battle_steps=battle_steps,
        menu_steps=menu_steps,
        invalid_visual_steps=invalid_visual_steps,
        telemetry_coverage=round(telemetry_coverage, 4),
        visual_coverage=round(visual_coverage, 4),
        transition_rate=round(transition_rate, 4),
        exploration_efficiency=round(efficiency, 4),
    )


def compare_metrics(
    baseline: RunMetrics,
    candidate: RunMetrics,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, baseline_value in baseline.as_dict().items():
        candidate_value = candidate.as_dict()[key]
        if isinstance(baseline_value, (int, float)) and isinstance(
            candidate_value,
            (int, float),
        ):
            result[key] = round(
                float(candidate_value) - float(baseline_value),
                4,
            )
    return result


def write_metrics(
    run_directory: Path,
    metrics: RunMetrics,
) -> Path:
    path = run_directory / "metrics.json"
    path.write_text(
        json.dumps(metrics.as_dict(), indent=2),
        encoding="utf-8",
    )
    return path
