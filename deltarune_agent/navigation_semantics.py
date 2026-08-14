from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from typing import Literal, TypeAlias


Warp: TypeAlias = tuple[str, int, int, str, str, int, int]
WarpRole: TypeAlias = Literal[
    "unknown",
    "new_area",
    "progression",
    "likely_optional",  # legacy persisted value; classifier v2 never emits it
    "return/backtrack",  # legacy persisted value; classifier v2 never emits it
    "loop_suppressed",
]
WarpSemanticRole: TypeAlias = Literal[
    "unknown",
    "new_area",
    "progression",
]
WarpBehaviorTag: TypeAlias = Literal[
    "observed_return_leg",
    "quick_return",
    "return_prone",
    "loop_risk",
]

WARP_PORTAL_CLUSTER_RADIUS = 2
WARP_CLASSIFICATION_VERSION = 2
LOOP_SUPPRESSION_HARD_THRESHOLD = 2


@dataclass(frozen=True)
class PortalCluster:
    """Nearby observations of one directed, observed room portal.

    A cluster deliberately requires the same source room, destination room, and
    action.  That keeps a portal learned from movement separate from a nearby
    event-triggered transition, while retaining every raw observation as a
    variant for later auditing.
    """

    source_room: str
    target_room: str
    action: str
    variants: tuple[tuple[Warp, int], ...]
    source_bounds: tuple[int, int, int, int]
    arrival_bounds: tuple[int, int, int, int]
    source_center: tuple[int, int]
    arrival_center: tuple[int, int]
    crossings: int

    @property
    def aperture(self) -> dict[str, object]:
        min_x, min_y, max_x, max_y = self.source_bounds
        if self.action in {"up", "down"}:
            axis = "horizontal"
            span = max_x - min_x + 1
        elif self.action in {"left", "right"}:
            axis = "vertical"
            span = max_y - min_y + 1
        else:
            axis = "point"
            span = max(max_x - min_x, max_y - min_y) + 1
        return {
            "axis": axis,
            "span_cells": max(1, span),
            "bounds": [min_x, min_y, max_x, max_y],
        }


def canonicalize_warp_observations(
    warps: Mapping[Warp, int],
    *,
    radius: int = WARP_PORTAL_CLUSTER_RADIUS,
) -> list[PortalCluster]:
    """Group same-direction nearby warp samples without discarding variants.

    Clustering uses connected components rather than a single mutable centroid,
    so the result is deterministic and independent of insertion order.
    """

    radius = max(0, int(radius))
    grouped: dict[tuple[str, str, str], list[tuple[Warp, int]]] = {}
    for raw_warp, raw_count in warps.items():
        if len(raw_warp) != 7:
            continue
        count = int(raw_count)
        if count <= 0:
            continue
        warp: Warp = (
            str(raw_warp[0]),
            int(raw_warp[1]),
            int(raw_warp[2]),
            str(raw_warp[3]),
            str(raw_warp[4]),
            int(raw_warp[5]),
            int(raw_warp[6]),
        )
        grouped.setdefault((warp[0], warp[4], warp[3]), []).append((warp, count))

    clusters: list[PortalCluster] = []
    for (source_room, target_room, action), observations in sorted(grouped.items()):
        remaining = set(range(len(observations)))
        while remaining:
            seed = min(remaining)
            component = {seed}
            frontier = [seed]
            remaining.remove(seed)
            while frontier:
                current_index = frontier.pop()
                current = observations[current_index][0]
                neighbors = [
                    candidate_index
                    for candidate_index in sorted(remaining)
                    if _nearby_sources(
                        current,
                        observations[candidate_index][0],
                        radius,
                    )
                ]
                for candidate_index in neighbors:
                    remaining.remove(candidate_index)
                    component.add(candidate_index)
                    frontier.append(candidate_index)

            variants = tuple(
                sorted(
                    (observations[index] for index in component),
                    key=lambda item: item[0],
                )
            )
            crossings = sum(count for _warp, count in variants)
            source_points = [
                (warp[1], warp[2], count) for warp, count in variants
            ]
            arrival_points = [
                (warp[5], warp[6], count) for warp, count in variants
            ]
            clusters.append(
                PortalCluster(
                    source_room=source_room,
                    target_room=target_room,
                    action=action,
                    variants=variants,
                    source_bounds=_bounds(source_points),
                    arrival_bounds=_bounds(arrival_points),
                    source_center=_weighted_center(source_points),
                    arrival_center=_weighted_center(arrival_points),
                    crossings=crossings,
                )
            )
    return sorted(
        clusters,
        key=lambda cluster: (
            cluster.source_room,
            cluster.target_room,
            cluster.action,
            cluster.source_bounds,
        ),
    )


def stable_portal_id(cluster: PortalCluster) -> str:
    """Return the deterministic initial ID for a newly learned portal.

    Persisted IDs remain authoritative once created.  The geometry is included
    only to distinguish multiple same-direction portals joining the same rooms.
    """

    seed = "|".join(
        (
            cluster.source_room,
            cluster.target_room,
            cluster.action,
            str(cluster.source_bounds[0]),
            str(cluster.source_bounds[1]),
        )
    )
    return f"portal_{sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def semantic_portal_role(record: Mapping[str, object]) -> WarpSemanticRole:
    """Return the strongest *meaning* supported by observed outcomes.

    Return/backtrack and loop behavior are deliberately excluded here. They are
    traversal observations, not proof that a doorway is optional or incapable
    of becoming useful for progression later.
    """

    if _nonnegative_int(record.get("non_discovery_progress_outcomes")):
        return "progression"
    if str(record.get("first_novel_destination") or ""):
        return "new_area"
    return "unknown"


def portal_behavior_evidence(
    record: Mapping[str, object],
) -> tuple[list[WarpBehaviorTag], dict[str, float | int]]:
    """Summarize reversible traversal behavior independently of semantic role."""

    backtracks = _nonnegative_int(record.get("return_backtracks"))
    immediate_returns = _nonnegative_int(record.get("immediate_returns"))
    suppressions = _nonnegative_int(record.get("loop_suppressions"))
    crossings = max(1, _nonnegative_int(record.get("crossings")))
    dwell_samples = _nonnegative_int(record.get("dwell_samples"))
    dwell_total = _nonnegative_int(record.get("dwell_steps_total"))
    average_dwell = dwell_total / dwell_samples if dwell_samples else 0.0

    tags: list[WarpBehaviorTag] = []
    if backtracks:
        tags.append("observed_return_leg")
    if immediate_returns:
        tags.append("quick_return")
    return_observations = backtracks + immediate_returns
    return_tendency = min(1.0, return_observations / crossings)
    if return_observations >= 2 and return_tendency >= 0.50:
        tags.append("return_prone")
    loop_risk = min(1.0, suppressions / crossings)
    if suppressions:
        tags.append("loop_risk")

    return tags, {
        "return_backtracks": backtracks,
        "immediate_returns": immediate_returns,
        "return_tendency": round(return_tendency, 3),
        "loop_suppressions": suppressions,
        "loop_risk": round(loop_risk, 3),
        "mean_return_dwell_steps": round(average_dwell, 3),
    }


def classify_portal(record: Mapping[str, object]) -> tuple[WarpRole, float, list[str]]:
    """Classify a portal strictly from outcomes the agent has observed.

    Classification v2 separates semantic meaning from traversal behavior. A
    quick return or explicit backtrack can lower certainty, but it is never
    enough to call a warp ``likely_optional`` or ``return/backtrack``. Those old
    labels were too strong and could prevent a real progression route from ever
    being tested again.

    Reaching a previously unseen room remains useful evidence for ``new_area``
    but is intentionally never treated as story progression by itself.
    """

    progress = _nonnegative_int(record.get("non_discovery_progress_outcomes"))
    suppressions = _nonnegative_int(record.get("loop_suppressions"))
    backtracks = _nonnegative_int(record.get("return_backtracks"))
    immediate_returns = _nonnegative_int(record.get("immediate_returns"))
    crossings = max(1, _nonnegative_int(record.get("crossings")))
    first_novel = str(record.get("first_novel_destination") or "")
    behavior_tags, metrics = portal_behavior_evidence(record)

    if progress:
        confidence = min(0.99, 0.88 + 0.04 * (progress - 1))
        basis = [
            f"{progress} non-discovery story-progress outcome"
            + ("s" if progress != 1 else ""),
            "room discovery alone was excluded from this label",
        ]
        if behavior_tags:
            basis.append(
                "return/loop behavior remains recorded separately and cannot demote observed progression"
            )
        return "progression", confidence, basis

    # Repeated explicit loop suppression remains a navigation safety state, but
    # one loop event is not enough to turn a route into a hard-negative label.
    if suppressions >= LOOP_SUPPRESSION_HARD_THRESHOLD:
        confidence = min(0.94, 0.64 + 0.07 * min(4, suppressions))
        return (
            "loop_suppressed",
            confidence,
            [
                f"temporarily suppressed after {suppressions} observed navigation-loop events",
                "no independent story-progress outcome has been observed",
                "loop suppression is reversible if later positive evidence appears",
            ],
        )

    # Return evidence is intentionally *not* a semantic role. If it conflicts
    # with novelty evidence, keep the route eligible as unknown instead of
    # prematurely declaring it optional/return-only.
    if backtracks or immediate_returns:
        basis = [
            "return behavior was observed, but return behavior does not prove the portal is optional",
            f"observed return legs: {backtracks}",
            f"quick returns: {immediate_returns}",
            f"return tendency: {float(metrics['return_tendency']):.2f}",
        ]
        if first_novel:
            basis.append(
                f"the portal also reached previously unseen {first_novel}; semantic meaning remains unresolved"
            )
        if suppressions:
            basis.append(
                "one loop-suppression event is retained as caution rather than a hard role"
            )
        return "unknown", 0.42 if crossings > 1 else 0.34, basis

    if first_novel:
        return (
            "new_area",
            0.70,
            [
                f"first observed crossing reached previously unseen {first_novel}",
                "no independent story-progress outcome has been observed",
            ],
        )

    if suppressions:
        return (
            "unknown",
            0.30,
            [
                "one navigation-loop suppression was observed",
                "one suppression is insufficient for a hard negative classification",
            ],
        )

    return (
        "unknown",
        0.25 if crossings == 1 else min(0.49, 0.25 + 0.04 * (crossings - 1)),
        ["observed crossing has not yet produced enough outcome evidence"],
    )


def refresh_portal_classification(record: dict[str, object]) -> None:
    role, confidence, basis = classify_portal(record)
    semantic_role = semantic_portal_role(record)
    behavior_tags, metrics = portal_behavior_evidence(record)

    record["classification_version"] = WARP_CLASSIFICATION_VERSION
    record["role"] = role
    record["semantic_role"] = semantic_role
    record["confidence"] = round(confidence, 3)
    record["basis"] = basis
    record["behavior_tags"] = list(behavior_tags)
    record["return_tendency"] = metrics["return_tendency"]
    record["loop_risk"] = metrics["loop_risk"]
    record["mean_return_dwell_steps"] = metrics["mean_return_dwell_steps"]
    record["classification_state"] = (
        "confirmed"
        if semantic_role == "progression"
        else "observed"
        if semantic_role == "new_area" and not behavior_tags
        else "safety_hold"
        if role == "loop_suppressed"
        else "provisional"
    )


def _nearby_sources(left: Warp, right: Warp, radius: int) -> bool:
    return max(abs(left[1] - right[1]), abs(left[2] - right[2])) <= radius


def _bounds(points: list[tuple[int, int, int]]) -> tuple[int, int, int, int]:
    xs = [x for x, _y, _count in points]
    ys = [y for _x, y, _count in points]
    return min(xs), min(ys), max(xs), max(ys)


def _weighted_center(points: list[tuple[int, int, int]]) -> tuple[int, int]:
    total = max(1, sum(count for _x, _y, count in points))
    return (
        round(sum(x * count for x, _y, count in points) / total),
        round(sum(y * count for _x, y, count in points) / total),
    )


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
