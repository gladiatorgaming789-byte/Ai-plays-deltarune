from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Mapping, Protocol

from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


REGION_WORLD_SIZE = CELL_SIZE * EXPLORATION_REGION_CELLS
ACTIVE_GUESS_KINDS = {
    "possible_exit",
    "possible_character",
    "possible_interactable",
}
FINAL_GUESS_STATES = {"confirmed", "rejected", "retired"}
MAX_COMPLETED_TESTS = 3


class GuessRoom(Protocol):
    screen_regions: Mapping[tuple[int, int], dict[str, object]]
    interactables: Mapping[tuple[int, int], dict[str, object]]
    warps: Mapping[tuple[int, int, str], dict[str, object]]


@dataclass(frozen=True)
class VisualGuessEntry:
    """One player-observed hypothesis with separate visual and routing geometry."""

    marker: str
    stable_id: str
    hypothesis: str
    label: str
    regions: tuple[tuple[int, int], ...]
    anchor_cell: tuple[float, float]
    anchor_world: tuple[float, float]
    confidence: float
    evidence: str
    status: str
    edge_hint: str | None
    feature_box_world: tuple[float, float, float, float] | None
    evidence_kind: str


def parse_world_box(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(component) for component in value)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def union_world_boxes(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def obstruction_world_box(record: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    cells = record.get("obstruction_cells")
    if not isinstance(cells, (list, tuple)):
        return None
    parsed: list[tuple[int, int]] = []
    for cell in cells:
        if not isinstance(cell, (list, tuple)) or len(cell) != 2:
            continue
        try:
            parsed.append((int(cell[0]), int(cell[1])))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    return (
        min(cell[0] for cell in parsed) * CELL_SIZE,
        min(cell[1] for cell in parsed) * CELL_SIZE,
        (max(cell[0] for cell in parsed) + 1) * CELL_SIZE,
        (max(cell[1] for cell in parsed) + 1) * CELL_SIZE,
    )


def record_extent(record: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    """Prefer learned object/passage extent and retain raw visual geometry as fallback."""
    candidates = (
        parse_world_box(record.get("passage_box_world")),
        parse_world_box(record.get("obstruction_box_world")),
        obstruction_world_box(record),
        parse_world_box(record.get("feature_box_world")),
    )
    return next((box for box in candidates if box is not None), None)


def _box_gap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float]:
    horizontal = max(0.0, max(first[0], second[0]) - min(first[2], second[2]))
    vertical = max(0.0, max(first[1], second[1]) - min(first[3], second[3]))
    return horizontal, vertical


def _same_visible_feature(
    first_region: tuple[int, int],
    first: Mapping[str, object],
    second_region: tuple[int, int],
    second: Mapping[str, object],
) -> bool:
    if first.get("hypothesis") != second.get("hypothesis"):
        return False
    if max(
        abs(first_region[0] - second_region[0]),
        abs(first_region[1] - second_region[1]),
    ) > 1:
        return False
    hypothesis = str(first.get("hypothesis") or "")
    if hypothesis == "possible_exit" and first.get("edge_hint") != second.get("edge_hint"):
        return False
    first_box = record_extent(first)
    second_box = record_extent(second)
    if first_box is None or second_box is None:
        return bool(first.get("path_continuation") and second.get("path_continuation"))
    horizontal_gap, vertical_gap = _box_gap(first_box, second_box)
    if hypothesis == "possible_exit":
        edge = str(first.get("edge_hint") or "")
        along_gap = horizontal_gap if edge in {"top", "bottom"} else vertical_gap
        across_gap = vertical_gap if edge in {"top", "bottom"} else horizontal_gap
        return along_gap <= REGION_WORLD_SIZE * 0.75 and across_gap <= CELL_SIZE
    return horizontal_gap <= CELL_SIZE and vertical_gap <= CELL_SIZE


def _confirmed_by_map(
    region: tuple[int, int],
    record: Mapping[str, object],
    room: GuessRoom,
) -> bool:
    if record.get("choice_retry"):
        return False
    hypothesis = str(record.get("hypothesis") or "")
    if hypothesis in {"possible_character", "possible_interactable"}:
        return any(
            (
                cell[0] // EXPLORATION_REGION_CELLS,
                cell[1] // EXPLORATION_REGION_CELLS,
            )
            == region
            for cell in room.interactables
        )
    if hypothesis == "possible_exit":
        return any(
            (
                key[0] // EXPLORATION_REGION_CELLS,
                key[1] // EXPLORATION_REGION_CELLS,
            )
            == region
            for key in room.warps
        )
    return False


def _record_active(
    region: tuple[int, int],
    record: Mapping[str, object],
    room: GuessRoom,
) -> bool:
    if record.get("hypothesis") not in ACTIVE_GUESS_KINDS:
        return False
    state = str(record.get("guess_state") or "proposed")
    if state in FINAL_GUESS_STATES:
        return False
    completed_tests = int(
        record.get("completed_tests", record.get("inspections", 0)) or 0
    )
    if completed_tests >= MAX_COMPLETED_TESTS and not record.get("choice_retry"):
        return False
    return not _confirmed_by_map(region, record, room)


def _record_anchor_cell(
    region: tuple[int, int],
    record: Mapping[str, object],
) -> tuple[float, float]:
    value = record.get("anchor_cell")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            pass
    return (
        region[0] * EXPLORATION_REGION_CELLS + EXPLORATION_REGION_CELLS / 2,
        region[1] * EXPLORATION_REGION_CELLS + EXPLORATION_REGION_CELLS / 2,
    )


def _record_anchor_world(
    region: tuple[int, int],
    record: Mapping[str, object],
    extent: tuple[float, float, float, float] | None,
) -> tuple[float, float]:
    for field in ("anchor_world", "focus_world"):
        value = record.get(field)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return float(value[0]), float(value[1])
            except (TypeError, ValueError):
                pass
    if extent is not None:
        return (extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2
    anchor_cell = _record_anchor_cell(region, record)
    return anchor_cell[0] * CELL_SIZE, anchor_cell[1] * CELL_SIZE


def _fallback_label(hypothesis: str, record: Mapping[str, object]) -> str:
    if hypothesis == "possible_character":
        targets = int(record.get("obstruction_target_cells", 0) or 0)
        return (
            f"Possible character-sized {targets}-cell obstacle"
            if targets
            else "Possible character-sized obstacle"
        )
    if hypothesis == "possible_interactable":
        return "Possible object to inspect"
    edge = str(record.get("edge_hint") or "room edge")
    return (
        f"Possible opening along {edge} edge"
        if edge != "room edge"
        else "Possible room-boundary opening"
    )


def _guess_status(
    records: list[Mapping[str, object]],
    visible_now: bool,
) -> str:
    state_order = {
        "approaching": 0,
        "cooldown": 1,
        "reached": 2,
        "probed": 3,
        "proposed": 4,
    }
    state = min(
        (str(record.get("guess_state") or "proposed") for record in records),
        key=lambda value: state_order.get(value, 9),
    )
    attempts = sum(int(record.get("approach_attempts", 0) or 0) for record in records)
    completed = sum(
        int(record.get("completed_tests", record.get("inspections", 0)) or 0)
        for record in records
    )
    failures = sum(int(record.get("failed_approaches", 0) or 0) for record in records)
    parts = [state.replace("_", " ")]
    if attempts:
        parts.append(f"{attempts} approach{'es' if attempts != 1 else ''}")
    if completed:
        parts.append(f"{completed} completed test{'s' if completed != 1 else ''}")
    if failures:
        parts.append(f"{failures} route failure{'s' if failures != 1 else ''}")
    if visible_now:
        parts.append("visible now")
    return "; ".join(parts)


def visual_guess_entries(
    room_name: str,
    room: GuessRoom,
    current_visible_regions: set[tuple[str, int, int]] | None = None,
) -> list[VisualGuessEntry]:
    """Create stable, feature-sized guesses without exposing storage buckets."""
    visible = current_visible_regions or set()
    active = {
        region: record
        for region, record in room.screen_regions.items()
        if _record_active(region, record, room)
    }

    groups: list[list[tuple[int, int]]] = []
    remaining = set(active)
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group = [seed]
        changed = True
        while changed:
            changed = False
            for candidate in sorted(remaining):
                if any(
                    _same_visible_feature(candidate, active[candidate], member, active[member])
                    for member in group
                ):
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
                    break
        groups.append(sorted(group))

    kind_order = {
        "possible_character": 0,
        "possible_interactable": 1,
        "possible_exit": 2,
    }
    edge_order = {"top": 0, "right": 1, "bottom": 2, "left": 3, "": 4}

    def group_key(group: list[tuple[int, int]]) -> tuple[object, ...]:
        records = [active[region] for region in group]
        hypothesis = str(records[0].get("hypothesis") or "")
        boxes = [box for record in records if (box := record_extent(record)) is not None]
        extent = union_world_boxes(boxes)
        return (
            kind_order.get(hypothesis, 9),
            edge_order.get(str(records[0].get("edge_hint") or ""), 9),
            extent[1] if extent else group[0][1] * REGION_WORLD_SIZE,
            extent[0] if extent else group[0][0] * REGION_WORLD_SIZE,
            tuple(group),
        )

    groups.sort(key=group_key)
    counters = {"C": 0, "O": 0, "E": 0}
    entries: list[VisualGuessEntry] = []
    for group in groups:
        records = [active[region] for region in group]
        representative_region = max(
            group,
            key=lambda region: (
                float(active[region].get("guess_confidence", 0.0) or 0.0),
                float(active[region].get("interest", 0.0) or 0.0),
                -region[1],
                -region[0],
            ),
        )
        representative = active[representative_region]
        hypothesis = str(representative.get("hypothesis") or "")
        prefix = {
            "possible_character": "C",
            "possible_interactable": "O",
            "possible_exit": "E",
        }.get(hypothesis, "G")
        counters[prefix] = counters.get(prefix, 0) + 1

        boxes = [box for record in records if (box := record_extent(record)) is not None]
        extent = union_world_boxes(boxes)
        route_anchor = _record_anchor_cell(representative_region, representative)
        if extent is not None:
            anchor_world = ((extent[0] + extent[2]) / 2, (extent[1] + extent[3]) / 2)
        else:
            anchor_world = _record_anchor_world(
                representative_region,
                representative,
                extent,
            )

        confidence = min(
            0.95,
            max(
                float(record.get("guess_confidence", record.get("interest", 0.0)) or 0.0)
                for record in records
            )
            + min(0.08, 0.02 * (len(records) - 1)),
        )
        evidence_parts: list[str] = []
        for record in records:
            text = str(record.get("evidence_summary") or "").strip()
            if text and text not in evidence_parts:
                evidence_parts.append(text)
        evidence = "; ".join(evidence_parts[:2]) or "limited player-observed evidence"
        if len(evidence_parts) > 2:
            evidence += f"; {len(evidence_parts) - 2} related observations"
        visible_now = any((room_name, *region) in visible for region in group)
        stable_seed = (
            f"{room_name}|{hypothesis}|{representative.get('guess_id', '')}|"
            + ";".join(f"{x},{y}" for x, y in group)
        )
        stable_id = str(
            representative.get("guess_id")
            or f"g-{sha1(stable_seed.encode('utf-8')).hexdigest()[:10]}"
        )
        entries.append(
            VisualGuessEntry(
                marker=f"{prefix}{counters[prefix]}",
                stable_id=stable_id,
                hypothesis=hypothesis,
                label=str(
                    representative.get("guess_label")
                    or _fallback_label(hypothesis, representative)
                ),
                regions=tuple(group),
                anchor_cell=route_anchor,
                anchor_world=anchor_world,
                confidence=confidence,
                evidence=evidence,
                status=_guess_status(records, visible_now),
                edge_hint=(
                    str(representative["edge_hint"])
                    if representative.get("edge_hint")
                    else None
                ),
                feature_box_world=extent,
                evidence_kind=str(
                    representative.get("evidence_kind") or "player_observation"
                ),
            )
        )
    return entries
