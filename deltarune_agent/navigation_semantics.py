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
    "likely_optional",
    "return/backtrack",
    "loop_suppressed",
]

WARP_PORTAL_CLUSTER_RADIUS = 2


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


def classify_portal(record: Mapping[str, object]) -> tuple[WarpRole, float, list[str]]:
    """Classify a portal strictly from outcomes the agent has observed.

    Reaching a previously unseen room is useful evidence for ``new_area`` but
    is intentionally never treated as story progression by itself.
    """

    progress = _nonnegative_int(record.get("non_discovery_progress_outcomes"))
    suppressions = _nonnegative_int(record.get("loop_suppressions"))
    backtracks = _nonnegative_int(record.get("return_backtracks"))
    immediate_returns = _nonnegative_int(record.get("immediate_returns"))
    crossings = max(1, _nonnegative_int(record.get("crossings")))
    dwell_samples = _nonnegative_int(record.get("dwell_samples"))
    dwell_total = _nonnegative_int(record.get("dwell_steps_total"))
    first_novel = str(record.get("first_novel_destination") or "")

    if progress:
        confidence = min(0.99, 0.88 + 0.04 * (progress - 1))
        return (
            "progression",
            confidence,
            [
                f"{progress} non-discovery story-progress outcome"
                + ("s" if progress != 1 else ""),
                "room discovery alone was excluded from this label",
            ],
        )
    if suppressions:
        confidence = min(0.96, 0.68 + 0.08 * min(3, suppressions - 1))
        return (
            "loop_suppressed",
            confidence,
            [
                f"suppressed after {suppressions} observed navigation-loop "
                f"event{'s' if suppressions != 1 else ''}",
            ],
        )
    if backtracks:
        confidence = min(0.94, 0.72 + 0.07 * min(3, backtracks - 1))
        return (
            "return/backtrack",
            confidence,
            [
                f"used as the return leg {backtracks} time"
                + ("s" if backtracks != 1 else ""),
            ],
        )
    average_dwell = dwell_total / dwell_samples if dwell_samples else 0.0
    if immediate_returns >= 2 or (
        immediate_returns >= 1 and crossings >= 2 and average_dwell <= 20.0
    ):
        confidence = min(0.88, 0.57 + 0.08 * min(3, immediate_returns))
        return (
            "likely_optional",
            confidence,
            [
                f"returned without independent progress {immediate_returns} time"
                + ("s" if immediate_returns != 1 else ""),
                f"mean observed dwell before return: {average_dwell:.1f} steps",
            ],
        )
    if first_novel:
        return (
            "new_area",
            0.70,
            [
                f"first observed crossing reached previously unseen {first_novel}",
                "no independent story-progress outcome has been observed",
            ],
        )
    return (
        "unknown",
        0.25 if crossings == 1 else min(0.49, 0.25 + 0.04 * (crossings - 1)),
        ["observed crossing has not yet produced enough outcome evidence"],
    )


def refresh_portal_classification(record: dict[str, object]) -> None:
    role, confidence, basis = classify_portal(record)
    record["role"] = role
    record["confidence"] = round(confidence, 3)
    record["basis"] = basis


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
