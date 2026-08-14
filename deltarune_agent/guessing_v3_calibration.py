"""Evidence-purity calibration for Guessing v3.

The first v3 draft treated the currently exposed legacy ``hypothesis`` as a
small prior on the next belief update.  That risks a self-reinforcing loop: a
classification made for routing can become evidence for itself on the next
frame.  This calibration removes that feedback.  Only observed geometry,
interaction history, visual consistency, and other independent record fields
contribute to the new belief scores.
"""

from __future__ import annotations

from typing import Mapping

from . import guessing_v3 as v3


CALIBRATION_VERSION = 1
_INSTALLED = False


def _evidence_only_belief_scores(
    record: Mapping[str, object],
    consistency: float,
    sample_count: int,
) -> dict[str, float]:
    scores = {
        "possible_exit": 0.70,
        "possible_character": 0.70,
        "possible_interactable": 0.70,
        "scenery": 1.00,
    }

    interest = v3._clamp(v3._safe_float(record.get("interest")))
    salience_boost = min(0.30, interest * 0.30)
    for kind in v3.SEMANTIC_KINDS:
        scores[kind] += salience_boost

    path_continuation = bool(record.get("path_continuation"))
    opening_score = v3._clamp(v3._safe_float(record.get("edge_opening_score")))
    opening_width = v3._clamp(v3._safe_float(record.get("edge_width_ratio")))
    dark_ratio = v3._clamp(v3._safe_float(record.get("dark_ratio")))
    if path_continuation:
        scores["possible_exit"] += 2.70
    if opening_score > 0:
        scores["possible_exit"] += opening_score * 2.00
    if opening_width >= 0.66 and not path_continuation:
        scores["possible_exit"] *= 0.62
        scores["scenery"] += 1.15
    if dark_ratio >= 0.72 and not path_continuation:
        scores["scenery"] += 0.35

    approaches = max(0, v3._safe_int(record.get("entity_approach_directions")))
    targets = max(0, v3._safe_int(record.get("obstruction_target_cells")))
    if approaches >= 2 and 1 <= targets <= 4:
        scores["possible_character"] += 1.55 + min(
            0.45,
            (approaches - 2) * 0.20,
        )
        scores["possible_interactable"] += 0.45
    elif approaches == 1 and 1 <= targets <= 2:
        scores["possible_interactable"] += 1.20
        scores["possible_character"] += 0.25
    elif approaches == 1 and 1 <= targets <= 4:
        scores["possible_interactable"] += 0.72
    if targets > 4:
        scores["scenery"] += min(1.20, (targets - 4) * 0.16 + 0.30)

    if record.get("choice_retry"):
        scores["possible_character"] += 0.90
        scores["possible_interactable"] += 0.80

    misses = max(0, v3._safe_int(record.get("guess_misses")))
    failures = max(0, v3._safe_int(record.get("failed_approaches")))
    completed = max(
        0,
        v3._safe_int(record.get("completed_tests", record.get("inspections", 0))),
    )
    scores["scenery"] += min(1.35, misses * 0.35 + failures * 0.34)
    if str(record.get("guess_state") or "") in {"cooldown", "rejected"}:
        scores["scenery"] += min(0.70, completed * 0.22 + failures * 0.18)

    if sample_count >= 2:
        semantic_multiplier = 0.90 + 0.24 * consistency
        for kind in v3.SEMANTIC_KINDS:
            scores[kind] *= semantic_multiplier
        if consistency < v3.MULTI_VIEW_POOR:
            scores["scenery"] += (
                (v3.MULTI_VIEW_POOR - consistency) * 2.30 + 0.35
            )
    return scores


def install_guessing_v3_calibration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # With the self-prior removed, strong compact one-side object evidence lands
    # a little above 0.40 while a broader four-cell one-side obstruction remains
    # below it.  This preserves the useful ambiguity boundary intentionally.
    v3.SEMANTIC_COMMIT_THRESHOLD = 0.40
    v3._belief_scores = _evidence_only_belief_scores
    v3.GUESSING_V3_CALIBRATION_VERSION = CALIBRATION_VERSION
    _INSTALLED = True


__all__ = ["CALIBRATION_VERSION", "install_guessing_v3_calibration"]
