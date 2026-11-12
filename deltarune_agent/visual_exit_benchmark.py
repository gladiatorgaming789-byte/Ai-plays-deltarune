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
    state_history: tuple[tuple[int, str], ...] = ()


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
            try:
                candidate_step = int(update.get("last_seen_step", step))
            except (TypeError, ValueError, OverflowError):
                candidate_step = step
            candidate = VisualExitCandidate(
                candidate_id=candidate_id,
                room=room,
                region=region_key,
                step=candidate_step,
                state=state,
                source=str(update.get("exit_candidate_source")) if update.get("exit_candidate_source") is not None else None,
                score=score,
                anchor_x=anchor[0],
                anchor_y=anchor[1],
            )
            previous = earliest.get(candidate_id)
            if previous is None:
                earliest[candidate_id] = VisualExitCandidate(
                    candidate_id=candidate.candidate_id,
                    room=candidate.room,
                    region=candidate.region,
                    step=candidate.step,
                    state=candidate.state,
                    source=candidate.source,
                    score=candidate.score,
                    anchor_x=candidate.anchor_x,
                    anchor_y=candidate.anchor_y,
                    state_history=((candidate_step, state),),
                )
            else:
                history = list(previous.state_history)
                if not history or history[-1] != (candidate_step, state):
                    history.append((candidate_step, state))
                earliest[candidate_id] = VisualExitCandidate(
                    candidate_id=previous.candidate_id,
                    room=previous.room,
                    region=previous.region,
                    step=previous.step,
                    state=previous.state,
                    source=previous.source,
                    score=previous.score,
                    anchor_x=previous.anchor_x,
                    anchor_y=previous.anchor_y,
                    state_history=tuple(history),
                )
    return sorted(earliest.values(), key=lambda item: (item.step, item.room, item.candidate_id))


def evaluate_visual_exits(
    candidates: Sequence[VisualExitCandidate],
    transitions: Sequence[TransitionTruth],
    *,
    spatial_match_radius: float = 16.0,
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
            if lead <= 0 or lead > lead_window:
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
        state_at_crossing = candidate.state
        if transition.step is not None:
            eligible_states = [
                state for observed_step, state in candidate.state_history
                if observed_step < transition.step
            ]
            if eligible_states:
                state_at_crossing = eligible_states[-1]
        matches.append(VisualExitMatch(
            candidate_id=candidate.candidate_id,
            transition_sequence=transition.sequence,
            room=transition.source_room,
            candidate_state=state_at_crossing,
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
            if any(
                state in {"semantic_ready", "confirmed"}
                for _step, state in candidate.state_history
            )
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



def calibrate_visual_exit_candidates(
    candidates: Sequence[VisualExitCandidate],
    transitions: Sequence[TransitionTruth],
    *,
    spatial_match_radius: float = 16.0,
    max_lead_steps: int = 180,
    max_thresholds: int = 25,
) -> dict[str, object]:
    """Produce an offline score-threshold sweep without changing agent behavior.

    Thresholds are selected from observed candidate scores, not hand-picked
    constants. Because untested candidates have no negative labels, this report
    deliberately does not claim precision, specificity, or an optimal cutoff.
    """
    scored = sorted(
        score for score in (candidate.score for candidate in candidates)
        if score is not None
    )
    unique_scores = sorted({round(score, 4) for score in scored})
    if len(unique_scores) > max(2, int(max_thresholds)):
        count = max(2, int(max_thresholds))
        indices = {
            round(index * (len(unique_scores) - 1) / (count - 1))
            for index in range(count)
        }
        thresholds = [unique_scores[index] for index in sorted(indices)]
    else:
        thresholds = unique_scores

    sweep: list[dict[str, object]] = []
    for threshold in thresholds:
        selected = [
            candidate for candidate in candidates
            if candidate.score is not None and candidate.score >= threshold
        ]
        result = evaluate_visual_exits(
            selected,
            transitions,
            spatial_match_radius=spatial_match_radius,
            max_lead_steps=max_lead_steps,
        )
        sweep.append({
            "score_threshold": threshold,
            "candidate_count": len(selected),
            "matched_transitions": result.matched_transitions,
            "missed_transitions": result.missed_transitions,
            "detection_recall": result.detection_recall,
            "semantic_ready_matches": result.semantic_ready_matches,
            "semantic_ready_recall": result.semantic_ready_recall,
            "mean_lead_steps": result.mean_lead_steps,
            "unmatched_candidates_unvalidated": result.unmatched_candidates_unvalidated,
        })

    source_breakdown: list[dict[str, object]] = []
    sources = sorted({candidate.source or "unknown" for candidate in candidates})
    for source in sources:
        source_candidates = [
            candidate for candidate in candidates
            if (candidate.source or "unknown") == source
        ]
        result = evaluate_visual_exits(
            source_candidates,
            transitions,
            spatial_match_radius=spatial_match_radius,
            max_lead_steps=max_lead_steps,
        )
        source_breakdown.append({
            "source": source,
            "candidate_count": len(source_candidates),
            "matched_transitions": result.matched_transitions,
            "detection_recall": result.detection_recall,
            "semantic_ready_recall": result.semantic_ready_recall,
            "mean_lead_steps": result.mean_lead_steps,
        })

    return {
        "calibration_version": 1,
        "purpose": "offline_visual_exit_score_threshold_sweep",
        "agent_thresholds_modified": False,
        "candidate_count": len(candidates),
        "scored_candidate_count": len(scored),
        "unscored_candidate_count": len(candidates) - len(scored),
        "score_min": min(scored) if scored else None,
        "score_median": (
            float(scored[len(scored) // 2]) if scored else None
        ),
        "score_max": max(scored) if scored else None,
        "transition_count": len(transitions),
        "spatial_match_radius_world_units": max(0.0, float(spatial_match_radius)),
        "max_lead_steps": max(0, int(max_lead_steps)),
        "threshold_sweep": sweep,
        "source_breakdown": source_breakdown,
        "limitations": [
            "Unmatched candidates are unvalidated, not confirmed negatives.",
            "Precision, specificity, and an optimal threshold cannot be estimated without labeled tested-negative candidates.",
            "This report does not modify live detector thresholds.",
        ],
    }


def write_visual_exit_calibration(path: Path, report: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark_version": BENCHMARK_VERSION,
        **dict(report),
    }
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


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
