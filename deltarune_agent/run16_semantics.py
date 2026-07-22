from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
from typing import Any

from . import navigation_semantics as semantics_module
from . import world_model as world_model_module
from .navigation_semantics import WARP_PORTAL_CLUSTER_RADIUS
from .world_model import CELL_SIZE, WorldModel


CARDINAL_DIRECTIONS = {"up", "down", "left", "right"}
SCREEN_EXTENSION_FIELDS = (
    "animated_bonus_applied",
    "animated_sprite_evidence",
    "doorway_failed_story_epoch",
    "doorway_story_retry_epoch",
    "story_sensitive_doorway",
    "doorway_facade",
    "doorway_box_world",
    "path_probe",
    "path_continuation_revivals",
    "path_continuation_locked",
    "doorway_probe_attempts",
    "motion_sprite_candidate",
    "motion_sprite_tested",
    "lifecycle_locked",
    "source_evidence_kind",
)
PORTAL_EXTENSION_FIELDS = (
    "round_trip_returns",
    "transition_kind",
    "action_aliases",
    "canonicalized_action_variants",
)
_INSTALLED = False
_ORIGINAL_LOAD = WorldModel.load.__func__
_ORIGINAL_SAVE = WorldModel.save
_ORIGINAL_RECORD_RETURN = WorldModel.record_warp_return
_ORIGINAL_CLASSIFY = semantics_module.classify_portal


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def classify_portal(record: dict[str, object] | Any):
    """Classify automatic scene transitions separately from navigable doors.

    Chapter-one data contains dedicated scene-controller objects and generic
    ``room_goto`` transitions in addition to cardinal door objects. Runtime
    telemetry exposes that same distinction: a transition with no cardinal
    action is an automatic sequence, not a doorway the planner can walk toward.
    """
    action = str(record.get("action") or "event")
    progress = _safe_int(record.get("non_discovery_progress_outcomes"))
    if action not in CARDINAL_DIRECTIONS:
        basis = [
            "room change occurred without an observed cardinal crossing",
            "treat as a scripted/automatic transition, not a navigable doorway",
        ]
        if progress:
            basis.insert(
                0,
                f"{progress} observed non-discovery story-progress outcome"
                + ("s" if progress != 1 else ""),
            )
        return "automatic_sequence", 0.97 if progress else 0.93, basis

    role, confidence, basis = _ORIGINAL_CLASSIFY(record)
    round_trips = _safe_int(record.get("round_trip_returns"))
    if role in {"unknown", "new_area"} and round_trips > 0 and progress == 0:
        confidence = min(0.90, 0.62 + 0.08 * min(3, round_trips - 1))
        return (
            "likely_optional",
            confidence,
            [
                f"entered and later returned through the paired portal {round_trips} time"
                + ("s" if round_trips != 1 else ""),
                "no independent story-progress outcome was observed during the visit",
            ],
        )
    return role, confidence, basis


def refresh_portal_classification(record: dict[str, object]) -> None:
    role, confidence, basis = classify_portal(record)
    action = str(record.get("action") or "event")
    record["transition_kind"] = (
        "manual_crossing" if action in CARDINAL_DIRECTIONS else "automatic_sequence"
    )
    record["role"] = role
    record["confidence"] = round(float(confidence), 3)
    record["basis"] = list(basis)


def _load_with_extensions(cls, path: Path | None):
    model = _ORIGINAL_LOAD(cls, path)
    model.room_dimensions = {}
    if path is None or not path.exists():
        return model
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return model

    raw_dimensions = data.get("room_dimensions")
    if isinstance(raw_dimensions, dict):
        for room, value in raw_dimensions.items():
            if not (isinstance(value, (list, tuple)) and len(value) == 2):
                continue
            try:
                width, height = (float(component) for component in value)
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                model.room_dimensions[str(room)] = (width, height)

    for item in data.get("screen_regions", []):
        if not isinstance(item, dict):
            continue
        try:
            key = (
                str(item["room"]),
                int(item["region_x"]),
                int(item["region_y"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        record = model.screen_regions.get(key)
        if record is None:
            continue
        for field in SCREEN_EXTENSION_FIELDS:
            if item.get(field) is not None:
                value = item[field]
                record[field] = list(value) if isinstance(value, tuple) else value

    portal_items = data.get("warp_portals", [])
    if isinstance(portal_items, list):
        raw_by_id = {
            str(item.get("id")): item
            for item in portal_items
            if isinstance(item, dict) and item.get("id")
        }
        for portal_id, record in model.warp_portals.items():
            item = raw_by_id.get(portal_id)
            if not item:
                continue
            for field in PORTAL_EXTENSION_FIELDS:
                if item.get(field) is not None:
                    value = item[field]
                    record[field] = list(value) if isinstance(value, tuple) else value
            refresh_portal_classification(record)
    return model


def _save_with_extensions(self: WorldModel) -> None:
    _ORIGINAL_SAVE(self)
    if self.path is None or not self.path.exists():
        return
    try:
        data = json.loads(self.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    dimensions = getattr(self, "room_dimensions", {})
    if isinstance(dimensions, dict):
        data["room_dimensions"] = {
            str(room): [float(value[0]), float(value[1])]
            for room, value in sorted(dimensions.items())
            if isinstance(value, (list, tuple))
            and len(value) == 2
            and float(value[0]) > 0
            and float(value[1]) > 0
        }

    records = {
        (
            str(item.get("room")),
            int(item.get("region_x", 0)),
            int(item.get("region_y", 0)),
        ): item
        for item in data.get("screen_regions", [])
        if isinstance(item, dict)
    }
    for key, record in self.screen_regions.items():
        item = records.get(key)
        if item is None:
            continue
        for field in SCREEN_EXTENSION_FIELDS:
            if record.get(field) is not None:
                item[field] = record[field]

    portal_items = {
        str(item.get("id")): item
        for item in data.get("warp_portals", [])
        if isinstance(item, dict) and item.get("id")
    }
    for portal_id, record in self.warp_portals.items():
        item = portal_items.get(portal_id)
        if item is None:
            continue
        for field in PORTAL_EXTENSION_FIELDS:
            if record.get(field) is not None:
                item[field] = record[field]

    temporary = self.path.with_suffix(self.path.suffix + ".run16.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(self.path)


def _record_warp_return_with_round_trip(
    self: WorldModel,
    outbound_portal,
    *,
    dwell_steps: int,
    returned_via=None,
    immediate_threshold: int = 20,
    step: int | None = None,
) -> None:
    _ORIGINAL_RECORD_RETURN(
        self,
        outbound_portal,
        dwell_steps=dwell_steps,
        returned_via=returned_via,
        immediate_threshold=immediate_threshold,
        step=step,
    )
    outbound = self.portal_metadata(outbound_portal)
    if outbound is not None:
        outbound["round_trip_returns"] = _safe_int(
            outbound.get("round_trip_returns")
        ) + 1
        refresh_portal_classification(outbound)


def _center(record: dict[str, object], field: str) -> tuple[int, int] | None:
    footprint = record.get(field)
    if not isinstance(footprint, dict):
        return None
    value = footprint.get("center")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None


def _boundary_direction(
    room: str,
    source: tuple[int, int],
    dimensions: dict[str, tuple[float, float]],
) -> str | None:
    value = dimensions.get(room)
    if not value:
        return None
    width, height = value
    max_x = max(0, int((float(width) - 1) // CELL_SIZE))
    max_y = max(0, int((float(height) - 1) // CELL_SIZE))
    distances = {
        "left": source[0],
        "right": max_x - source[0],
        "up": source[1],
        "down": max_y - source[1],
    }
    nearest = min(distances.values())
    choices = [name for name, distance in distances.items() if distance == nearest]
    if nearest <= 2 and len(choices) == 1:
        return choices[0]
    return None


def _merge_count_maps(
    target: dict[str, object],
    source: dict[str, object],
    field: str,
) -> None:
    left = target.setdefault(field, {})
    right = source.get(field)
    if not isinstance(left, dict) or not isinstance(right, dict):
        return
    for name, value in right.items():
        left[str(name)] = max(_safe_int(left.get(str(name))), _safe_int(value))


def _merge_samples(
    target: dict[str, object],
    source: dict[str, object],
    field: str,
) -> None:
    output = target.setdefault(field, [])
    incoming = source.get(field)
    if not isinstance(output, list) or not isinstance(incoming, list):
        return
    keyed = {
        (int(item.get("x", -9999)), int(item.get("y", -9999))): item
        for item in output
        if isinstance(item, dict)
    }
    for item in incoming:
        if not isinstance(item, dict):
            continue
        try:
            key = int(item["x"]), int(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        current = keyed.get(key)
        if current is None:
            current = {
                "x": key[0],
                "y": key[1],
                "count": _safe_int(item.get("count")) or 1,
            }
            output.append(current)
            keyed[key] = current
        else:
            current["count"] = max(
                _safe_int(current.get("count")),
                _safe_int(item.get("count")),
            )


def repair_portal_action_conflicts(world: WorldModel) -> int:
    """Merge contradictory cardinal actions for one physical observed portal."""
    dimensions = getattr(world, "room_dimensions", {})
    if not isinstance(dimensions, dict):
        dimensions = {}

    warps = list(world.warps.items())
    remaining = set(range(len(warps)))
    components: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = [seed]
        while queue:
            index = queue.pop()
            warp, _count = warps[index]
            if warp[3] not in CARDINAL_DIRECTIONS:
                continue
            neighbors = []
            for candidate in sorted(remaining):
                other, _other_count = warps[candidate]
                if (
                    other[3] in CARDINAL_DIRECTIONS
                    and other[0] == warp[0]
                    and other[4] == warp[4]
                    and max(abs(other[1] - warp[1]), abs(other[2] - warp[2]))
                    <= WARP_PORTAL_CLUSTER_RADIUS
                    and max(abs(other[5] - warp[5]), abs(other[6] - warp[6]))
                    <= WARP_PORTAL_CLUSTER_RADIUS
                ):
                    neighbors.append(candidate)
            for candidate in neighbors:
                remaining.remove(candidate)
                component.add(candidate)
                queue.append(candidate)
        components.append(sorted(component))

    repaired = 0
    for indices in components:
        actions = {warps[index][0][3] for index in indices}
        if len(actions) <= 1:
            continue
        total = sum(max(1, int(warps[index][1])) for index in indices)
        source_x = round(
            sum(
                warps[index][0][1] * max(1, int(warps[index][1]))
                for index in indices
            )
            / total
        )
        source_y = round(
            sum(
                warps[index][0][2] * max(1, int(warps[index][1]))
                for index in indices
            )
            / total
        )
        representative = max(
            (warps[index] for index in indices),
            key=lambda item: (int(item[1]), item[0][3]),
        )[0]
        canonical = _boundary_direction(
            representative[0],
            (source_x, source_y),
            dimensions,
        )
        if canonical is None:
            counts = Counter()
            for index in indices:
                warp, count = warps[index]
                counts[warp[3]] += int(count)
            canonical = max(counts, key=lambda name: (counts[name], name))
        canonical_warp = (
            representative[0],
            source_x,
            source_y,
            canonical,
            representative[4],
            representative[5],
            representative[6],
        )
        for index in indices:
            world.warps.pop(warps[index][0], None)
        world.warps[canonical_warp] += total
        repaired += len(indices) - 1

    if repaired:
        world.reconcile_warp_portals()

    portal_ids = list(world.warp_portals)
    used: set[str] = set()
    for portal_id in portal_ids:
        if portal_id in used or portal_id not in world.warp_portals:
            continue
        record = world.warp_portals[portal_id]
        action = str(record.get("action") or "")
        if action not in CARDINAL_DIRECTIONS:
            continue
        source = _center(record, "source_footprint")
        arrival = _center(record, "arrival_footprint")
        if source is None or arrival is None:
            continue
        group = [portal_id]
        for other_id in portal_ids:
            if (
                other_id == portal_id
                or other_id in used
                or other_id not in world.warp_portals
            ):
                continue
            other = world.warp_portals[other_id]
            if (
                str(other.get("action") or "") in CARDINAL_DIRECTIONS
                and other.get("from_room") == record.get("from_room")
                and other.get("to_room") == record.get("to_room")
            ):
                other_source = _center(other, "source_footprint")
                other_arrival = _center(other, "arrival_footprint")
                if (
                    other_source is not None
                    and other_arrival is not None
                    and max(
                        abs(other_source[0] - source[0]),
                        abs(other_source[1] - source[1]),
                    )
                    <= WARP_PORTAL_CLUSTER_RADIUS
                    and max(
                        abs(other_arrival[0] - arrival[0]),
                        abs(other_arrival[1] - arrival[1]),
                    )
                    <= WARP_PORTAL_CLUSTER_RADIUS
                ):
                    group.append(other_id)
        if len(group) <= 1:
            continue

        canonical = _boundary_direction(
            str(record.get("from_room") or ""),
            source,
            dimensions,
        )
        if canonical is None:
            canonical_id = max(
                group,
                key=lambda item: (
                    _safe_int(world.warp_portals[item].get("crossings")),
                    str(world.warp_portals[item].get("action") or ""),
                ),
            )
            canonical = str(
                world.warp_portals[canonical_id].get("action") or action
            )
        primary_id = next(
            (
                item
                for item in group
                if str(world.warp_portals[item].get("action") or "") == canonical
            ),
            max(
                group,
                key=lambda item: _safe_int(
                    world.warp_portals[item].get("crossings")
                ),
            ),
        )
        primary = world.warp_portals[primary_id]
        aliases = {
            str(world.warp_portals[item].get("action") or "") for item in group
        }
        for other_id in group:
            if other_id == primary_id:
                continue
            other = world.warp_portals.pop(other_id)
            for field in (
                "crossings",
                "novel_destination_crossings",
                "discovery_only_outcomes",
                "non_discovery_progress_outcomes",
                "immediate_returns",
                "return_backtracks",
                "round_trip_returns",
                "dwell_samples",
                "dwell_steps_total",
                "dwell_steps_max",
                "loop_suppressions",
            ):
                primary[field] = max(
                    _safe_int(primary.get(field)),
                    _safe_int(other.get(field)),
                )
            for field in ("progress_outcomes", "suppression_reasons"):
                _merge_count_maps(primary, other, field)
            for field in ("source_samples", "arrival_samples"):
                _merge_samples(primary, other, field)
            used.add(other_id)
            repaired += 1
        primary["action"] = canonical
        primary["action_aliases"] = sorted(aliases)
        primary["canonicalized_action_variants"] = max(1, len(aliases) - 1)
        world._refresh_portal(primary)
        used.add(primary_id)

    # Older memories only marked the reverse portal as a return leg. Transfer
    # that observation to the outbound direction as round-trip evidence so a
    # visited side room can become likely optional without assuming its purpose.
    portals = list(world.warp_portals.values())
    for outbound in portals:
        if str(outbound.get("action") or "") not in CARDINAL_DIRECTIONS:
            refresh_portal_classification(outbound)
            continue
        reverse_returns = max(
            (
                _safe_int(reverse.get("return_backtracks"))
                for reverse in portals
                if reverse.get("from_room") == outbound.get("to_room")
                and reverse.get("to_room") == outbound.get("from_room")
                and str(reverse.get("action") or "") in CARDINAL_DIRECTIONS
            ),
            default=0,
        )
        if reverse_returns:
            outbound["round_trip_returns"] = max(
                _safe_int(outbound.get("round_trip_returns")),
                reverse_returns,
            )
        refresh_portal_classification(outbound)
    return repaired


def install_run16_semantics() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    semantics_module.classify_portal = classify_portal
    semantics_module.refresh_portal_classification = refresh_portal_classification
    world_model_module.refresh_portal_classification = refresh_portal_classification
    WorldModel.load = classmethod(_load_with_extensions)
    WorldModel.save = _save_with_extensions
    WorldModel.record_warp_return = _record_warp_return_with_round_trip
