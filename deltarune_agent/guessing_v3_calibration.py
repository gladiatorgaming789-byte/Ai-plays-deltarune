"""Evidence-purity calibration for Guessing v3.

The first v3 draft treated the currently exposed legacy ``hypothesis`` as a
small prior on the next belief update. That risks a self-reinforcing loop: a
classification made for routing can become evidence for itself on the next
frame. This calibration removes that feedback. Only observed geometry,
interaction history, visual consistency, and other independent record fields
contribute to beliefs or to the decision that a feature is structurally worth
investigating.
"""

from __future__ import annotations

from typing import Mapping

from . import guessing_v3 as v3


CALIBRATION_VERSION = 1
_INSTALLED = False
RAW_VISUAL_INTEREST_MIN = 0.18


def _valid_feature_box(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        left, top, right, bottom = (float(component) for component in value)
    except (TypeError, ValueError):
        return False
    return right > left and bottom > top


def _raw_visual_structure(record: Mapping[str, object]) -> bool:
    """Keep a salient observed feature without assigning it a semantic type."""

    if v3._safe_float(record.get("interest")) < RAW_VISUAL_INTEREST_MIN:
        return False
    has_extent = any(
        _valid_feature_box(record.get(field))
        for field in (
            "feature_box_world",
            "visual_box_world",
            "passage_box_world",
            "obstruction_box_world",
        )
    )
    if not has_extent:
        return False
    return (
        v3._safe_float(record.get("contrast")) >= 0.08
        or v3._safe_float(record.get("edge_density")) >= 0.08
        or v3._safe_float(record.get("colorfulness")) >= 0.08
    )


def _evidence_only_structural_evidence(record: Mapping[str, object]) -> bool:
    """Return whether independent observations justify retaining a visual lead."""

    return bool(
        record.get("path_continuation")
        or v3._safe_float(record.get("edge_opening_score")) >= 0.30
        or v3._safe_int(record.get("entity_approach_directions")) > 0
        or v3._safe_int(record.get("obstruction_target_cells")) > 0
        or record.get("choice_retry")
        or _raw_visual_structure(record)
    )


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


def _guess_region(record: Mapping[str, object]) -> tuple[int, int] | None:
    guess_id = str(record.get("guess_id") or "")
    if "@" not in guess_id:
        return None
    coordinates = guess_id.rsplit("@", 1)[-1]
    if "," not in coordinates:
        return None
    left, right = coordinates.split(",", 1)
    try:
        return int(left), int(right)
    except (TypeError, ValueError):
        return None


def _raw_anchor(raw: Mapping[str, object]) -> tuple[float, float] | None:
    point = v3._point(raw.get("focus_world"))
    if point is not None:
        return point
    for field in ("passage_box_world", "feature_box_world"):
        point = v3._box_center(raw.get(field))
        if point is not None:
            return point
    return None


def _append_raw_world_sample(record: dict[str, object]) -> None:
    """Sample the current raw anchor, falling back to stable legacy memory."""

    sequence = max(0, v3._safe_int(record.get("last_seen_sequence")))
    if sequence <= 0:
        return
    samples = record.get("guess_world_samples")
    if not isinstance(samples, list):
        samples = []
        record["guess_world_samples"] = samples
    if any(
        isinstance(sample, dict) and v3._safe_int(sample.get("sequence")) == sequence
        for sample in samples
    ):
        return

    raw = None
    region = _guess_region(record)
    if region is not None:
        try:
            from .guessing_v3_screen import latest_raw_observation

            raw = latest_raw_observation(region)
        except (ImportError, TypeError, ValueError):
            raw = None
    anchor = _raw_anchor(raw) if isinstance(raw, Mapping) else None
    if anchor is None:
        anchor = v3._world_anchor(record)
    if anchor is None:
        return

    viewpoint = v3._latest_viewpoint(record)
    sample: dict[str, object] = {
        "sequence": sequence,
        "step": max(0, v3._safe_int(record.get("last_seen_step"))),
        "anchor_world": [round(anchor[0], 2), round(anchor[1], 2)],
        "anchor_source": "raw_observation" if isinstance(raw, Mapping) else "stable_memory_fallback",
    }
    if viewpoint is not None:
        sample["viewpoint"] = [viewpoint[0], viewpoint[1]]
    if isinstance(raw, Mapping):
        if raw.get("signature"):
            sample["signature"] = str(raw["signature"])
        if raw.get("interest") is not None:
            sample["raw_interest"] = round(v3._safe_float(raw.get("interest")), 4)
    samples.append(sample)
    del samples[:-v3.MAX_WORLD_SAMPLES]


def install_guessing_v3_calibration() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    # With the self-prior removed, strong compact one-side object evidence lands
    # a little above 0.40 while a broader four-cell one-side obstruction remains
    # below it. This preserves the useful ambiguity boundary intentionally.
    v3.SEMANTIC_COMMIT_THRESHOLD = 0.40
    v3._structural_evidence = _evidence_only_structural_evidence
    v3._belief_scores = _evidence_only_belief_scores
    v3._append_world_sample = _append_raw_world_sample
    v3.GUESSING_V3_CALIBRATION_VERSION = CALIBRATION_VERSION
    _INSTALLED = True


__all__ = [
    "CALIBRATION_VERSION",
    "RAW_VISUAL_INTEREST_MIN",
    "install_guessing_v3_calibration",
]
