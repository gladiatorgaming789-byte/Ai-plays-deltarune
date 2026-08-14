"""Guessing v3: evidence-led, multi-hypothesis visual reasoning.

This layer deliberately separates *what the agent observed* from *what the
agent currently believes that observation means*.  It never contains room,
NPC, item, dialogue, or progression answers.

The legacy visual analyzer still provides geometry, salience, collision, and
opening evidence.  Guessing v3 turns that player-observed evidence into:

* a bounded evidence ledger;
* simultaneous beliefs for exit / character / interactable / scenery;
* an ``unknown_but_interesting`` state when evidence is real but ambiguous;
* world-space multi-view consistency measurements; and
* bounded information-gain viewpoint probes before blind exploration.

The installer patches the existing explorer/persistence APIs so old navigation
memory remains usable.  Extra v3 metadata is injected into the existing
navigation JSON after the normal WorldModel save and restored after the normal
load, avoiding a destructive memory-version migration.
"""

from __future__ import annotations

from copy import deepcopy
from math import hypot, log
import json
from pathlib import Path
from typing import Any, Mapping

from .policy import DIRECTION_VECTORS, StarterPolicy
from .run4_explorer import Run4Explorer
from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS, WorldModel


GUESSING_V3_VERSION = 3
BELIEF_KINDS = (
    "possible_exit",
    "possible_character",
    "possible_interactable",
    "scenery",
)
SEMANTIC_KINDS = BELIEF_KINDS[:3]
UNKNOWN_BUT_INTERESTING = "unknown_but_interesting"
LIKELY_SCENERY = "likely_scenery"
UNRESOLVED = "unresolved"
FINAL_GUESS_STATES = {"confirmed", "rejected", "retired"}

MAX_EVIDENCE_LEDGER = 24
MAX_WORLD_SAMPLES = 10
MAX_INFORMATION_PROBES = 2
INFORMATION_PROBE_COOLDOWN_STEPS = 12
SEMANTIC_COMMIT_THRESHOLD = 0.44
SEMANTIC_COMMIT_MARGIN = 0.07
UNKNOWN_INTEREST_MIN = 0.08
MULTI_VIEW_GOOD = 0.70
MULTI_VIEW_POOR = 0.35

# Extra per-region fields persisted by the installer.  Keep this list explicit
# so corrupted/unexpected objects cannot silently become arbitrary memory data.
V3_PERSISTED_FIELDS = (
    "guessing_version",
    "guess_beliefs",
    "guess_semantic_state",
    "guess_belief_margin",
    "guess_belief_summary",
    "guess_evidence_ledger",
    "guess_world_samples",
    "multi_view_consistency",
    "multi_view_sample_count",
    "world_anchor_spread_cells",
    "information_probe_attempts",
    "information_probe_cooldown_until",
    "information_probe_last_step",
    "last_belief_update_sequence",
)

_INSTALLED = False
_ORIGINAL_REFRESH = StarterPolicy._refresh_visual_guess_metadata
_ORIGINAL_MAP_UPDATE = StarterPolicy._screen_region_map_update
_ORIGINAL_RUN4_PLAN = Run4Explorer._plan_exploration
_ORIGINAL_RUN4_SUMMARY = Run4Explorer.summary
_ORIGINAL_WORLD_SAVE = WorldModel.save
_ORIGINAL_WORLD_LOAD = WorldModel.load.__func__


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if result != result or result in {float("inf"), float("-inf")}:
        return default
    return result


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, value))


def _normalized(scores: Mapping[str, float]) -> dict[str, float]:
    cleaned = {kind: max(0.01, _safe_float(scores.get(kind), 0.01)) for kind in BELIEF_KINDS}
    total = sum(cleaned.values()) or 1.0
    return {kind: cleaned[kind] / total for kind in BELIEF_KINDS}


def _point(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _box_center(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(component) for component in value)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return (left + right) / 2.0, (top + bottom) / 2.0


def _world_anchor(record: Mapping[str, object]) -> tuple[float, float] | None:
    for field in ("anchor_world", "focus_world"):
        parsed = _point(record.get(field))
        if parsed is not None:
            return parsed
    for field in (
        "passage_box_world",
        "obstruction_box_world",
        "feature_box_world",
        "visual_box_world",
    ):
        parsed = _box_center(record.get(field))
        if parsed is not None:
            return parsed
    return None


def _latest_viewpoint(record: Mapping[str, object]) -> tuple[int, int] | None:
    viewpoints = record.get("evidence_viewpoints")
    if not isinstance(viewpoints, list) or not viewpoints:
        return None
    value = viewpoints[-1]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None


def _append_world_sample(record: dict[str, object]) -> None:
    sequence = max(0, _safe_int(record.get("last_seen_sequence")))
    if sequence <= 0:
        return
    anchor = _world_anchor(record)
    if anchor is None:
        return
    samples = record.get("guess_world_samples")
    if not isinstance(samples, list):
        samples = []
        record["guess_world_samples"] = samples
    if any(
        isinstance(sample, dict) and _safe_int(sample.get("sequence")) == sequence
        for sample in samples
    ):
        return
    viewpoint = _latest_viewpoint(record)
    sample: dict[str, object] = {
        "sequence": sequence,
        "step": max(0, _safe_int(record.get("last_seen_step"))),
        "anchor_world": [round(anchor[0], 2), round(anchor[1], 2)],
    }
    if viewpoint is not None:
        sample["viewpoint"] = [viewpoint[0], viewpoint[1]]
    samples.append(sample)
    del samples[:-MAX_WORLD_SAMPLES]


def _multi_view_measurements(record: dict[str, object]) -> tuple[float, float, int]:
    samples = record.get("guess_world_samples")
    if not isinstance(samples, list):
        return 0.5, 0.0, 0
    usable: list[tuple[tuple[int, int] | None, tuple[float, float]]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        anchor = _point(sample.get("anchor_world"))
        if anchor is None:
            continue
        viewpoint_value = sample.get("viewpoint")
        viewpoint = None
        if isinstance(viewpoint_value, (list, tuple)) and len(viewpoint_value) == 2:
            try:
                viewpoint = (int(viewpoint_value[0]), int(viewpoint_value[1]))
            except (TypeError, ValueError):
                viewpoint = None
        usable.append((viewpoint, anchor))
    if not usable:
        return 0.5, 0.0, 0

    # One observation cannot establish cross-view consistency.  Prefer samples
    # from distinct camera viewpoints; if old memory lacks viewpoint metadata,
    # sequence-distinct anchors still provide a conservative fallback.
    distinct: list[tuple[tuple[int, int] | None, tuple[float, float]]] = []
    seen_viewpoints: set[tuple[int, int]] = set()
    for viewpoint, anchor in reversed(usable):
        if viewpoint is not None:
            if viewpoint in seen_viewpoints:
                continue
            seen_viewpoints.add(viewpoint)
        distinct.append((viewpoint, anchor))
    distinct.reverse()
    if len(distinct) < 2:
        return 0.5, 0.0, len(distinct)

    anchors = [anchor for _viewpoint, anchor in distinct]
    mean_x = sum(point[0] for point in anchors) / len(anchors)
    mean_y = sum(point[1] for point in anchors) / len(anchors)
    spread_pixels = max(hypot(point[0] - mean_x, point[1] - mean_y) for point in anchors)
    spread_cells = spread_pixels / max(1.0, float(CELL_SIZE))
    # A stable world-space feature should remain close to the same location as
    # the camera moves.  Four cells of centroid drift is treated as fully
    # inconsistent; this is deliberately generous because the low-level
    # salience box may move within a larger object.
    consistency = _clamp(1.0 - spread_cells / 4.0)
    return consistency, spread_cells, len(distinct)


def _structural_evidence(record: Mapping[str, object]) -> bool:
    return bool(
        record.get("path_continuation")
        or str(record.get("hypothesis") or "") in SEMANTIC_KINDS
        or _safe_float(record.get("edge_opening_score")) >= 0.30
        or _safe_int(record.get("entity_approach_directions")) > 0
        or _safe_int(record.get("obstruction_target_cells")) > 0
        or record.get("choice_retry")
    )


def _belief_scores(record: Mapping[str, object], consistency: float, sample_count: int) -> dict[str, float]:
    scores = {
        "possible_exit": 0.70,
        "possible_character": 0.70,
        "possible_interactable": 0.70,
        "scenery": 1.00,
    }
    hypothesis = str(record.get("hypothesis") or "")
    if hypothesis in SEMANTIC_KINDS:
        scores[hypothesis] += 0.60

    interest = _clamp(_safe_float(record.get("interest")))
    # Salience says "worth looking at", not what the thing is.  Give each
    # semantic interpretation the same small boost so contrast alone cannot
    # decide between a person, object, or opening.
    salience_boost = min(0.30, interest * 0.30)
    for kind in SEMANTIC_KINDS:
        scores[kind] += salience_boost

    path_continuation = bool(record.get("path_continuation"))
    opening_score = _clamp(_safe_float(record.get("edge_opening_score")))
    opening_width = _clamp(_safe_float(record.get("edge_width_ratio")))
    dark_ratio = _clamp(_safe_float(record.get("dark_ratio")))
    if path_continuation:
        scores["possible_exit"] += 2.70
    if opening_score > 0:
        scores["possible_exit"] += opening_score * 2.00
    if opening_width >= 0.66 and not path_continuation:
        scores["possible_exit"] *= 0.62
        scores["scenery"] += 1.15
    if dark_ratio >= 0.72 and not path_continuation:
        scores["scenery"] += 0.35

    approaches = max(0, _safe_int(record.get("entity_approach_directions")))
    targets = max(0, _safe_int(record.get("obstruction_target_cells")))
    if approaches >= 2 and 1 <= targets <= 4:
        scores["possible_character"] += 1.55 + min(0.45, (approaches - 2) * 0.20)
        scores["possible_interactable"] += 0.45
    elif approaches == 1 and 1 <= targets <= 2:
        scores["possible_interactable"] += 1.20
        scores["possible_character"] += 0.25
    elif approaches == 1 and 1 <= targets <= 4:
        scores["possible_interactable"] += 0.72
    if targets > 4:
        scores["scenery"] += min(1.20, (targets - 4) * 0.16 + 0.30)

    if record.get("choice_retry"):
        # The agent has already observed a response-producing interaction here,
        # but not necessarily whether it was a character or a static object.
        scores["possible_character"] += 0.90
        scores["possible_interactable"] += 0.80

    misses = max(0, _safe_int(record.get("guess_misses")))
    failures = max(0, _safe_int(record.get("failed_approaches")))
    completed = max(
        0,
        _safe_int(record.get("completed_tests", record.get("inspections", 0))),
    )
    scores["scenery"] += min(1.35, misses * 0.35 + failures * 0.34)
    if str(record.get("guess_state") or "") in {"cooldown", "rejected"}:
        scores["scenery"] += min(0.70, completed * 0.22 + failures * 0.18)

    if sample_count >= 2:
        # Stable world-space placement supports "there is a coherent feature
        # here" without deciding its semantic category.  Drift instead raises
        # the artifact/scenery explanation.
        semantic_multiplier = 0.90 + 0.24 * consistency
        for kind in SEMANTIC_KINDS:
            scores[kind] *= semantic_multiplier
        if consistency < MULTI_VIEW_POOR:
            scores["scenery"] += (MULTI_VIEW_POOR - consistency) * 2.30 + 0.35

    return scores


def _belief_summary(beliefs: Mapping[str, float]) -> str:
    return ", ".join(
        f"{kind.replace('possible_', '')} {beliefs.get(kind, 0.0):.0%}"
        for kind in BELIEF_KINDS
    )


def _ledger_evidence(
    record: Mapping[str, object],
    consistency: float,
    sample_count: int,
) -> tuple[list[str], list[str]]:
    supports: list[str] = []
    contradicts: list[str] = []
    if record.get("path_continuation"):
        supports.append("mapped path continuation reaches this feature")
    opening = _clamp(_safe_float(record.get("edge_opening_score")))
    width = _clamp(_safe_float(record.get("edge_width_ratio")))
    if opening >= 0.30:
        supports.append(f"localized edge-opening evidence {opening:.0%}")
    if width >= 0.66 and not record.get("path_continuation"):
        contradicts.append(f"opening spans a broad {width:.0%} of the edge")
    approaches = max(0, _safe_int(record.get("entity_approach_directions")))
    targets = max(0, _safe_int(record.get("obstruction_target_cells")))
    if approaches:
        supports.append(
            f"compact obstruction has {approaches} learned approach side"
            + ("s" if approaches != 1 else "")
            + (f" across {targets} cells" if targets else "")
        )
    failures = max(0, _safe_int(record.get("failed_approaches")))
    misses = max(0, _safe_int(record.get("guess_misses")))
    if failures:
        contradicts.append(f"{failures} approach failure{'s' if failures != 1 else ''}")
    if misses:
        contradicts.append(f"{misses} visual miss{'es' if misses != 1 else ''}")
    if sample_count >= 2:
        if consistency >= MULTI_VIEW_GOOD:
            supports.append(
                f"world anchor remained stable across {sample_count} viewpoints"
            )
        elif consistency <= MULTI_VIEW_POOR:
            contradicts.append(
                f"world anchor drifted across {sample_count} viewpoints"
            )
    return supports, contradicts


def _append_ledger_entry(
    record: dict[str, object],
    *,
    kind: str,
    summary: str,
    supports: list[str] | None = None,
    contradicts: list[str] | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    ledger = record.get("guess_evidence_ledger")
    if not isinstance(ledger, list):
        ledger = []
        record["guess_evidence_ledger"] = ledger
    entry: dict[str, object] = {
        "kind": str(kind),
        "step": max(0, _safe_int(record.get("last_seen_step"))),
        "sequence": max(0, _safe_int(record.get("last_seen_sequence"))),
        "summary": str(summary),
    }
    if supports:
        entry["supports"] = list(dict.fromkeys(str(value) for value in supports if value))
    if contradicts:
        entry["contradicts"] = list(
            dict.fromkeys(str(value) for value in contradicts if value)
        )
    if extra:
        for key, value in extra.items():
            entry[str(key)] = deepcopy(value)

    # Observation refreshes can happen repeatedly without new evidence.  Do not
    # spend the bounded ledger on duplicate snapshots.
    signature = json.dumps(
        {
            key: entry.get(key)
            for key in (
                "kind",
                "sequence",
                "summary",
                "supports",
                "contradicts",
                "beliefs",
                "semantic_state",
                "direction",
            )
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    if ledger:
        previous = ledger[-1]
        if isinstance(previous, dict) and previous.get("_signature") == signature:
            return
    entry["_signature"] = signature
    ledger.append(entry)
    del ledger[:-MAX_EVIDENCE_LEDGER]


def refresh_guess_record_v3(
    record: dict[str, object],
    *,
    region: tuple[int, int] | None = None,
) -> None:
    """Refresh v3 beliefs from evidence already present in one region record."""

    record["guessing_version"] = GUESSING_V3_VERSION
    record.setdefault("information_probe_attempts", 0)
    record.setdefault("information_probe_cooldown_until", 0)
    _append_world_sample(record)
    consistency, spread_cells, sample_count = _multi_view_measurements(record)
    record["multi_view_consistency"] = round(consistency, 3)
    record["world_anchor_spread_cells"] = round(spread_cells, 3)
    record["multi_view_sample_count"] = sample_count

    beliefs = _normalized(_belief_scores(record, consistency, sample_count))
    record["guess_beliefs"] = {kind: round(value, 4) for kind, value in beliefs.items()}
    ranked_semantics = sorted(
        ((beliefs[kind], kind) for kind in SEMANTIC_KINDS),
        reverse=True,
    )
    top_value, top_kind = ranked_semantics[0]
    second_value = ranked_semantics[1][0]
    margin = max(0.0, top_value - second_value)
    record["guess_belief_margin"] = round(margin, 4)
    record["guess_belief_summary"] = _belief_summary(beliefs)

    state = str(record.get("guess_state") or "proposed")
    final = state in FINAL_GUESS_STATES
    structural = _structural_evidence(record)
    interesting = structural and (
        _safe_float(record.get("interest")) >= UNKNOWN_INTEREST_MIN
        or bool(record.get("path_continuation"))
        or _safe_int(record.get("entity_approach_directions")) > 0
        or _safe_float(record.get("edge_opening_score")) >= 0.30
    )

    if final and state == "confirmed":
        semantic_state = str(record.get("hypothesis") or top_kind or "confirmed")
    elif top_value >= SEMANTIC_COMMIT_THRESHOLD and margin >= SEMANTIC_COMMIT_MARGIN:
        semantic_state = top_kind
    elif interesting:
        semantic_state = UNKNOWN_BUT_INTERESTING
    elif beliefs["scenery"] >= 0.52:
        semantic_state = LIKELY_SCENERY
    else:
        semantic_state = UNRESOLVED
    record["guess_semantic_state"] = semantic_state

    # Keep final lifecycle decisions intact.  For active guesses, only expose a
    # legacy semantic hypothesis to routing once the multi-hypothesis evidence
    # is strong enough.  This is what prevents one early visual guess from
    # immediately controlling navigation.
    if not final:
        if record.get("choice_retry"):
            # A response-producing learned interaction is still a concrete test
            # target, although v3 retains character-vs-object uncertainty.
            if semantic_state not in {"possible_character", "possible_interactable"}:
                record["hypothesis"] = "possible_interactable"
        elif record.get("path_continuation"):
            record["hypothesis"] = "possible_exit"
        elif semantic_state in SEMANTIC_KINDS:
            record["hypothesis"] = semantic_state
        else:
            record["hypothesis"] = None

    old_confidence = _clamp(_safe_float(record.get("guess_confidence"), top_value))
    if semantic_state in SEMANTIC_KINDS:
        record["guess_confidence"] = round(
            _clamp(old_confidence * 0.55 + top_value * 0.45, 0.05, 0.95),
            3,
        )
    else:
        record["guess_confidence"] = round(_clamp(top_value, 0.05, 0.95), 3)

    if semantic_state == UNKNOWN_BUT_INTERESTING:
        record["guess_label"] = "Interesting feature; type unresolved"
        record["evidence_kind"] = "multi_hypothesis_observation"
        record["evidence_summary"] = (
            "Observed structure is worth another viewpoint, but current evidence "
            "does not reliably distinguish exit, character, object, or scenery; "
            + str(record["guess_belief_summary"])
        )
    elif semantic_state == LIKELY_SCENERY:
        record["guess_label"] = "Likely scenery or visual artifact"

    supports, contradicts = _ledger_evidence(record, consistency, sample_count)
    sequence = max(0, _safe_int(record.get("last_seen_sequence")))
    if sequence != _safe_int(record.get("last_belief_update_sequence"), -1):
        _append_ledger_entry(
            record,
            kind="observation",
            summary=(
                f"{semantic_state}: {record['guess_belief_summary']}"
                + (
                    f"; multi-view consistency {consistency:.0%}"
                    if sample_count >= 2
                    else "; one viewpoint so far"
                )
            ),
            supports=supports,
            contradicts=contradicts,
            extra={
                "beliefs": dict(record["guess_beliefs"]),
                "semantic_state": semantic_state,
                "region": list(region) if region is not None else None,
            },
        )
        record["last_belief_update_sequence"] = sequence


def _refresh_visual_guess_metadata_v3(
    self: StarterPolicy,
    region: tuple[int, int],
    record: dict[str, object],
    obstruction_details: dict[str, object] | None = None,
) -> None:
    # Retain all existing geometry/legacy confidence calculations first.
    _ORIGINAL_REFRESH(self, region, record, obstruction_details)
    refresh_guess_record_v3(record, region=region)


def _screen_region_map_update_v3(
    key: tuple[str, int, int],
    record: dict[str, object],
) -> dict[str, object]:
    update = _ORIGINAL_MAP_UPDATE(key, record)
    for field in V3_PERSISTED_FIELDS:
        if record.get(field) is not None:
            update[field] = deepcopy(record[field])
    return update


def _belief_entropy(record: Mapping[str, object]) -> float:
    value = record.get("guess_beliefs")
    if not isinstance(value, Mapping):
        return 0.0
    probabilities = [_clamp(_safe_float(value.get(kind))) for kind in BELIEF_KINDS]
    total = sum(probabilities)
    if total <= 0:
        return 0.0
    probabilities = [probability / total for probability in probabilities]
    entropy = -sum(
        probability * log(probability)
        for probability in probabilities
        if probability > 1e-9
    )
    return entropy / log(len(BELIEF_KINDS))


def _guess_anchor_cell(record: Mapping[str, object], region: tuple[int, int]) -> tuple[int, int]:
    anchor = record.get("anchor_cell")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        try:
            return int(anchor[0]), int(anchor[1])
        except (TypeError, ValueError):
            pass
    return (
        region[0] * EXPLORATION_REGION_CELLS + EXPLORATION_REGION_CELLS // 2,
        region[1] * EXPLORATION_REGION_CELLS + EXPLORATION_REGION_CELLS // 2,
    )


def _information_probe_direction(
    explorer: Run4Explorer,
    room: str,
    cell: tuple[int, int],
    target: tuple[int, int],
) -> str | None:
    delta_x = target[0] - cell[0]
    delta_y = target[1] - cell[1]
    if abs(delta_x) >= abs(delta_y) and delta_x != 0:
        preferred = ("up", "down")
    elif delta_y != 0:
        preferred = ("left", "right")
    else:
        # Already near the anchor: any safe lateral move can provide a second
        # viewpoint, but do not move directly through a known blocked edge.
        preferred = ("left", "right", "up", "down")

    loop_avoid = explorer._loop_avoid_directions(room, cell)
    candidates: list[tuple[tuple[int, int, int, str], str]] = []
    for direction in preferred:
        if direction in loop_avoid:
            continue
        if explorer._blocked_near(room, cell, direction):
            continue
        if explorer._is_entry_warp_direction(room, cell, direction):
            continue
        dx, dy = DIRECTION_VECTORS[direction]
        neighbor = explorer._known_open_neighbor(room, cell, direction)
        destination = neighbor or (cell[0] + dx, cell[1] + dy)
        candidates.append(
            (
                (
                    0 if neighbor is not None else 1,
                    explorer.visits[(room, *destination)],
                    explorer._recent_cell_cost(room, destination),
                    direction,
                ),
                direction,
            )
        )
    return min(candidates, default=None, key=lambda item: item[0])[1] if candidates else None


def information_gain_probe_plan(
    explorer: Run4Explorer,
    room: str,
    cell: tuple[int, int],
) -> tuple[str, int, str] | None:
    """Choose one cheap viewpoint-changing action for an unresolved visible guess."""

    current_visible = getattr(explorer, "current_visible_regions", set())
    candidates: list[
        tuple[
            tuple[float, int, int, int, int],
            tuple[str, int, int],
            dict[str, object],
            str,
        ]
    ] = []
    for key, record in explorer.screen_regions.items():
        if key[0] != room:
            continue
        if str(record.get("guess_semantic_state") or "") != UNKNOWN_BUT_INTERESTING:
            continue
        if str(record.get("guess_state") or "proposed") in FINAL_GUESS_STATES:
            continue
        if (room, key[1], key[2]) not in current_visible:
            continue
        attempts = max(0, _safe_int(record.get("information_probe_attempts")))
        if attempts >= MAX_INFORMATION_PROBES:
            continue
        if explorer.navigation_tick < _safe_int(record.get("information_probe_cooldown_until")):
            continue
        target = _guess_anchor_cell(record, (key[1], key[2]))
        direction = _information_probe_direction(explorer, room, cell, target)
        if direction is None:
            continue
        entropy = _belief_entropy(record)
        interest = _clamp(_safe_float(record.get("interest")))
        sample_count = max(0, _safe_int(record.get("multi_view_sample_count")))
        distance = abs(target[0] - cell[0]) + abs(target[1] - cell[1])
        information_value = entropy * (0.55 + interest * 0.45) * (
            1.0 if sample_count < 2 else max(0.35, 1.0 - sample_count * 0.12)
        )
        candidates.append(
            (
                (
                    -round(information_value, 6),
                    attempts,
                    distance,
                    key[2],
                    key[1],
                ),
                key,
                record,
                direction,
            )
        )
    if not candidates:
        return None

    _score, key, record, direction = min(candidates, key=lambda item: item[0])
    record["information_probe_attempts"] = max(
        0, _safe_int(record.get("information_probe_attempts"))
    ) + 1
    record["information_probe_last_step"] = explorer.navigation_tick
    record["information_probe_cooldown_until"] = (
        explorer.navigation_tick + INFORMATION_PROBE_COOLDOWN_STEPS
    )
    _append_ledger_entry(
        record,
        kind="information_probe",
        summary=(
            f"shift viewpoint {direction} before committing to a semantic interpretation"
        ),
        extra={
            "direction": direction,
            "attempt": int(record["information_probe_attempts"]),
            "semantic_state": UNKNOWN_BUT_INTERESTING,
            "beliefs": deepcopy(record.get("guess_beliefs", {})),
            "region": [key[1], key[2]],
        },
    )
    explorer.map_updates.append(explorer._screen_region_map_update(key, record))
    return (
        direction,
        2,
        "information gain: shift viewpoint "
        f"{direction} to disambiguate unresolved feature near region ({key[1]}, {key[2]})",
    )


def _plan_exploration_v3(
    self: Run4Explorer,
    room: str,
    cell: tuple[int, int],
) -> tuple[str, int, str]:
    plan = _ORIGINAL_RUN4_PLAN(self, room, cell)
    direction, commitment, reason = plan
    # v3 never steals a known warp, frontier, strong exit, interaction, or
    # semantic visual target.  It replaces only the final blind-probe fallback.
    if reason.startswith("no reachable frontier; probe"):
        information_plan = information_gain_probe_plan(self, room, cell)
        if information_plan is not None:
            return information_plan
    return direction, commitment, reason


def _summary_v3(self: Run4Explorer) -> dict[str, object]:
    summary = _ORIGINAL_RUN4_SUMMARY(self)
    records = list(self.screen_regions.values())
    summary.update(
        {
            "guessing_version": GUESSING_V3_VERSION,
            "multi_hypothesis_guess_records": sum(
                isinstance(record.get("guess_beliefs"), Mapping)
                for record in records
            ),
            "unknown_but_interesting_guesses": sum(
                record.get("guess_semantic_state") == UNKNOWN_BUT_INTERESTING
                and str(record.get("guess_state") or "proposed") not in FINAL_GUESS_STATES
                for record in records
            ),
            "guess_information_probes": sum(
                max(0, _safe_int(record.get("information_probe_attempts")))
                for record in records
            ),
            "guess_evidence_ledger_entries": sum(
                len(record.get("guess_evidence_ledger", []))
                if isinstance(record.get("guess_evidence_ledger"), list)
                else 0
                for record in records
            ),
            "low_multiview_consistency_guesses": sum(
                _safe_int(record.get("multi_view_sample_count")) >= 2
                and _safe_float(record.get("multi_view_consistency"), 0.5)
                < MULTI_VIEW_POOR
                for record in records
            ),
        }
    )
    return summary


def _sanitize_v3_value(field: str, value: object) -> object | None:
    if field == "guess_beliefs":
        if not isinstance(value, Mapping):
            return None
        beliefs = {
            kind: round(_clamp(_safe_float(value.get(kind))), 4)
            for kind in BELIEF_KINDS
        }
        return beliefs
    if field in {"guess_evidence_ledger", "guess_world_samples"}:
        if not isinstance(value, list):
            return None
        # JSON round-trip provides a compact deep copy and strips custom types.
        try:
            copied = json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            return None
        limit = MAX_EVIDENCE_LEDGER if field == "guess_evidence_ledger" else MAX_WORLD_SAMPLES
        return copied[-limit:]
    if field in {
        "guess_semantic_state",
        "guess_belief_summary",
    }:
        return str(value)[:500]
    if field in {
        "guessing_version",
        "multi_view_sample_count",
        "information_probe_attempts",
        "information_probe_cooldown_until",
        "information_probe_last_step",
        "last_belief_update_sequence",
    }:
        return max(0, _safe_int(value))
    if field in {
        "guess_belief_margin",
        "multi_view_consistency",
        "world_anchor_spread_cells",
    }:
        return round(max(0.0, _safe_float(value)), 4)
    return None


def _world_save_v3(self: WorldModel) -> None:
    _ORIGINAL_WORLD_SAVE(self)
    if self.path is None or not self.path.is_file():
        return
    try:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        items = data.get("screen_regions")
        if not isinstance(items, list):
            return
        by_key = {
            (str(item.get("room")), _safe_int(item.get("region_x")), _safe_int(item.get("region_y"))): item
            for item in items
            if isinstance(item, dict)
        }
        for key, record in self.screen_regions.items():
            item = by_key.get(key)
            if not isinstance(item, dict):
                continue
            for field in V3_PERSISTED_FIELDS:
                if field not in record:
                    continue
                sanitized = _sanitize_v3_value(field, record[field])
                if sanitized is not None:
                    item[field] = sanitized
        temporary = self.path.with_suffix(self.path.suffix + ".guess-v3.tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        # Core navigation persistence already succeeded.  v3 enrichment is
        # optional and must never turn a healthy save into a gameplay failure.
        return


def _world_load_v3(cls, path: Path | None) -> WorldModel:
    model = _ORIGINAL_WORLD_LOAD(cls, path)
    if path is None or not path.is_file():
        return model
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("screen_regions")
        if not isinstance(items, list):
            return model
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("room") or ""),
                _safe_int(item.get("region_x")),
                _safe_int(item.get("region_y")),
            )
            record = model.screen_regions.get(key)
            if record is None:
                continue
            for field in V3_PERSISTED_FIELDS:
                if field not in item:
                    continue
                sanitized = _sanitize_v3_value(field, item[field])
                if sanitized is not None:
                    record[field] = sanitized
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return model
    return model


def install_guessing_v3() -> None:
    """Install Guessing v3 once before the current explorer is instantiated."""

    global _INSTALLED
    if _INSTALLED:
        return
    StarterPolicy._refresh_visual_guess_metadata = _refresh_visual_guess_metadata_v3  # type: ignore[method-assign]
    StarterPolicy._screen_region_map_update = staticmethod(_screen_region_map_update_v3)  # type: ignore[method-assign]
    Run4Explorer._plan_exploration = _plan_exploration_v3  # type: ignore[method-assign]
    Run4Explorer.summary = _summary_v3  # type: ignore[method-assign]
    WorldModel.save = _world_save_v3  # type: ignore[method-assign]
    WorldModel.load = classmethod(_world_load_v3)  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "BELIEF_KINDS",
    "GUESSING_V3_VERSION",
    "LIKELY_SCENERY",
    "MAX_INFORMATION_PROBES",
    "UNKNOWN_BUT_INTERESTING",
    "information_gain_probe_plan",
    "install_guessing_v3",
    "refresh_guess_record_v3",
]
