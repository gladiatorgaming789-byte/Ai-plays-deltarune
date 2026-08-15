"""Precision-first entity/interactable belief calibration.

One collision side proves that a compact obstruction exists. It does not by
itself prove that the obstruction is a character or an interactable. This
calibration keeps that evidence useful while preventing it from becoming a
semantic routing target until stronger evidence is observed.
"""

from __future__ import annotations

from typing import Mapping

from . import guessing_v3 as v3


ENTITY_DETECTION_VERSION = 2
_INSTALLED = False
_ORIGINAL_BELIEF_SCORES = None

SINGLE_SIDE_MAX_TARGETS = 4
CORROBORATED_VIEW_COUNT = 2
CORROBORATED_VIEW_CONSISTENCY = 0.72


def _safe_int(value: object, default: int = 0) -> int:
    return v3._safe_int(value, default)


def _safe_float(value: object, default: float = 0.0) -> float:
    return v3._safe_float(value, default)


def response_evidence(record: Mapping[str, object]) -> bool:
    return bool(
        record.get("choice_retry")
        or record.get("confirmed_interactable_cell")
        or (
            str(record.get("guess_state") or "") == "confirmed"
            and str(record.get("hypothesis") or "")
            in {"possible_character", "possible_interactable"}
        )
    )


def single_side_entity_candidate(record: Mapping[str, object]) -> bool:
    approaches = max(0, _safe_int(record.get("entity_approach_directions")))
    targets = max(0, _safe_int(record.get("obstruction_target_cells")))
    return (
        approaches == 1
        and 1 <= targets <= SINGLE_SIDE_MAX_TARGETS
        and not response_evidence(record)
    )


def entity_candidate_state(record: Mapping[str, object]) -> str:
    if response_evidence(record):
        return "confirmed_response"
    approaches = max(0, _safe_int(record.get("entity_approach_directions")))
    targets = max(0, _safe_int(record.get("obstruction_target_cells")))
    if approaches >= 2 and 1 <= targets <= 4:
        return "multi_side_geometry"
    if not single_side_entity_candidate(record):
        return "not_single_side_candidate"
    failures = max(0, _safe_int(record.get("failed_approaches")))
    tests = max(
        0,
        _safe_int(record.get("completed_tests", record.get("inspections", 0))),
    )
    if failures >= 1 or tests >= 1:
        return "single_side_tested"
    samples = max(0, _safe_int(record.get("multi_view_sample_count")))
    consistency = _safe_float(record.get("multi_view_consistency"), 0.5)
    if samples >= CORROBORATED_VIEW_COUNT and consistency >= CORROBORATED_VIEW_CONSISTENCY:
        return "single_side_stable"
    return "single_side_unresolved"


def _belief_scores_entity_v2(
    record: Mapping[str, object],
    consistency: float,
    sample_count: int,
) -> dict[str, float]:
    assert _ORIGINAL_BELIEF_SCORES is not None
    scores = dict(_ORIGINAL_BELIEF_SCORES(record, consistency, sample_count))
    if not single_side_entity_candidate(record):
        return scores

    targets = max(0, _safe_int(record.get("obstruction_target_cells")))
    # Guessing-v3's evidence-only calibration awards a large semantic object
    # bonus to one collision side. The eight-run calibration showed that this
    # frequently describes walls, pits, desks and other scenery. Remove the
    # semantic-sized bonus and retain only a small "worth testing" clue.
    if targets <= 2:
        scores["possible_interactable"] = max(
            0.05,
            float(scores.get("possible_interactable", 0.0)) - 1.20,
        )
        scores["possible_character"] = max(
            0.05,
            float(scores.get("possible_character", 0.0)) - 0.25,
        )
    else:
        scores["possible_interactable"] = max(
            0.05,
            float(scores.get("possible_interactable", 0.0)) - 0.72,
        )

    stable = (
        sample_count >= CORROBORATED_VIEW_COUNT
        and consistency >= CORROBORATED_VIEW_CONSISTENCY
    )
    scores["possible_interactable"] += 0.26 if stable else 0.14
    scores["possible_character"] += 0.04 if stable else 0.0
    scores["scenery"] = float(scores.get("scenery", 0.0)) + (
        0.18 if stable else 0.32
    )

    failures = max(0, _safe_int(record.get("failed_approaches")))
    tests = max(
        0,
        _safe_int(record.get("completed_tests", record.get("inspections", 0))),
    )
    if failures or tests:
        scores["possible_interactable"] *= 0.62
        scores["possible_character"] *= 0.55
        scores["scenery"] += min(0.75, failures * 0.30 + tests * 0.24)
    return scores


def install_entity_detection_v2() -> None:
    global _INSTALLED, _ORIGINAL_BELIEF_SCORES
    if _INSTALLED:
        return
    _ORIGINAL_BELIEF_SCORES = v3._belief_scores
    v3._belief_scores = _belief_scores_entity_v2
    v3.ENTITY_DETECTION_VERSION = ENTITY_DETECTION_VERSION
    _INSTALLED = True


__all__ = [
    "ENTITY_DETECTION_VERSION",
    "entity_candidate_state",
    "install_entity_detection_v2",
    "response_evidence",
    "single_side_entity_candidate",
]
