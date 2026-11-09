"""Developer-only spatial benchmark for visual exit predictions.

This is post-run evaluation code. It must never be imported by gameplay policy,
perception, memory, prompt, or action-selection modules.

Matching uses the candidate's observed world-space anchor and the source-side
player foot position recorded by explicit transition telemetry. Unmatched
candidates are reported as unvalidated, not false positives: a run may simply
end before the agent tests that candidate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .transition_oracle import TransitionTruth


BENCHMARK_VERSION = 1


@dataclass(frozen=True)
class VisualExitCandidate:
    candidate_id: str
    room: str
    region: tuple[int, int]
    step: int
    state: str
    source: str | None
    score: float | None
    anchor_x: float
    anchor_y: float


@dataclass(frozen=True)
class VisualExitMatch:
    candidate_id: str
    transition_sequence: int
    room: str
    candidate_state: str
    candidate_step: int
    transition_step: int | None
    lead_steps: int | None
    distance_world_units: float


@dataclass(frozen=True)
class VisualExitEvaluation:
    candidate_count: int
    semantic_ready_candidate_count: int
    transition_count: int
    matched_transitions: int
    missed_transitions: int
    unmatched_candidates_unvalidated: int
    detection_recall: float
    semantic_ready_matches: int
    semantic_ready_recall: float
    mean_lead_steps: float | None
    median_lead_steps: float | None
    mean_match_distance_world_units: float | None
    spatial_match_radius_world_units: float
    max_lead_steps: int
    matches: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x, y = _number(value[0]), _number(value[1])
    if x is None or y is None:
        return None
    return x, y


def collect_visual_exit_candidates(
    events: Iterable[Mapping[str, object]],
) -> list[VisualExitCandidate]:
    """Collect the earliest observed v2 candidate state for each screen region."""
    earliest: dict[str, VisualExitCandidate] = {}
    for event in events:
        try:
            step = int(event.get("step", -1))
        except (TypeError, ValueError, OverflowError):
            continue
        updates = event.get("map_updates")
        if not isinstance(updates, list):
            continue
        for update in updates:
            if not isinstance(update, Mapping):
                continue
            if update.get("type") != "screen_region":
                continue
            if int(update.get("exit_detection_version", 0) or 0) != 2:
                continue
            room = str(update.get("room") or "").strip()
            region = update.get("region")
            anchor = _point(update.get("anchor_world"))
            if not room or not isinstance(region, (list, tuple)) or len(region) != 2 or anchor is None:
                continue
            try:
                region_key = (int(region[0]), int(region[1]))
            except (TypeError, ValueError, OverflowError):
                continue
            candidate_id = str(update.get("guess_id") or f"{room}@{region_key[0]},{region_key[1]}")
            state = str(update.get("exit_candidate_state") or "unknown")
            score = _number(update.get("exit_candidate_visual_score"))
            candidate = VisualExitCandidate(
                candidate_id=candidate_id,
                room=room,
                region=region_key,
                step=step,
                state=state,
                source=str(update.get("exit_candidate_source")) if update.get("exit_candidate_source") is not None else None,
                score=score,
                anchor_x=anchor[0],
                anchor_y=anchor[1],
            )
            previous = earliest.get(candidate_id)
            if previous is None or candidate.step < previous.step:
                earliest[candidate_id] = candidate
    return sorted(earliest.values(), key=lambda item: (item.step, item.room, item.candidate_id))


def evaluate_visual_exits(
    candidates: Sequence[VisualExitCandidate],
    transitions: Sequence[TransitionTruth],
    *,
    spatial_match_radius: float = 64.0,
    max_lead_steps: int = 180,
) -> VisualExitEvaluation:
    """Spatially associate pre-crossing candidates with real room transitions.

    A candidate matches only a transition from the same source room, observed
    after the candidate, within the configured lead window and world-space
    radius. Matching is one-to-one. No false-positive count is inferred from
    candidates that were never tested.
    """
    radius = max(0.0, float(spatial_match_radius))
    lead_window = max(0, int(max_lead_steps))
    pairs: list[tuple[float, int, int, int]] = []
    for candidate_index, candidate in enumerate(candidates):
        for transition_index, transition in enumerate(transitions):
            if candidate.room != transition.source_room:
                continue
            if transition.source_x is None or transition.source_y is None or transition.step is None:
                continue
            lead = transition.step - candidate.step
            if lead < 0 or lead > lead_window:
                continue
            distance = math.hypot(
                candidate.anchor_x - transition.source_x,
                candidate.anchor_y - transition.source_y,
            )
            if distance <= radius:
                pairs.append((distance, lead, candidate_index, transition_index))

    used_candidates: set[int] = set()
    used_transitions: set[int] = set()
    matches: list[VisualExitMatch] = []
    for distance, lead, candidate_index, transition_index in sorted(pairs):
        if candidate_index in used_candidates or transition_index in used_transitions:
            continue
        candidate = candidates[candidate_index]
        transition = transitions[transition_index]
        used_candidates.add(candidate_index)
        used_transitions.add(transition_index)
        matches.append(VisualExitMatch(
            candidate_id=candidate.candidate_id,
            transition_sequence=transition.sequence,
            room=transition.source_room,
            candidate_state=candidate.state,
            candidate_step=candidate.step,
            transition_step=transition.step,
            lead_steps=lead,
            distance_world_units=round(distance, 3),
        ))

    transition_count = len(transitions)
    matched = len(matches)
    semantic_matches = sum(
        1 for match in matches
        if match.candidate_state in {"semantic_ready", "confirmed"}
    )
    leads = [match.lead_steps for match in matches if match.lead_steps is not None]
    distances = [match.distance_world_units for match in matches]
    ordered_leads = sorted(leads)
    median = None
    if ordered_leads:
        middle = len(ordered_leads) // 2
        median = (
            float(ordered_leads[middle])
            if len(ordered_leads) % 2
            else (ordered_leads[middle - 1] + ordered_leads[middle]) / 2
        )

    return VisualExitEvaluation(
        candidate_count=len(candidates),
        semantic_ready_candidate_count=sum(
            1 for candidate in candidates
            if candidate.state in {"semantic_ready", "confirmed"}
        ),
        transition_count=transition_count,
        matched_transitions=matched,
        missed_transitions=transition_count - matched,
        unmatched_candidates_unvalidated=len(candidates) - len(used_candidates),
        detection_recall=round(matched / transition_count, 4) if transition_count else 1.0,
        semantic_ready_matches=semantic_matches,
        semantic_ready_recall=round(semantic_matches / transition_count, 4) if transition_count else 1.0,
        mean_lead_steps=round(sum(leads) / len(leads), 3) if leads else None,
        median_lead_steps=round(median, 3) if median is not None else None,
        mean_match_distance_world_units=round(sum(distances) / len(distances), 3) if distances else None,
        spatial_match_radius_world_units=radius,
        max_lead_steps=lead_window,
        matches=tuple(asdict(item) for item in matches),
    )


def write_visual_exit_evaluation(path: Path, evaluation: VisualExitEvaluation) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark_version": BENCHMARK_VERSION,
        "purpose": "developer_only_visual_exit_evaluation",
        **evaluation.as_dict(),
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
