from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


Cell = tuple[str, int, int]
Edge = tuple[str, int, int, str]
OpenEdge = tuple[str, int, int, str, int, int]
Warp = tuple[str, int, int, str, str, int, int]
InteractableKey = tuple[str, int, int]
ScreenRegionKey = tuple[str, int, int]
CELL_SIZE = 8
EXPLORATION_REGION_CELLS = 4


class WorldModel:
    """Persistent knowledge learned only from observed movement and room changes."""

    VERSION = 2

    def __init__(self, path: Path | None = None):
        self.path = path
        self.visits: Counter[Cell] = Counter()
        self.blocked: Counter[Edge] = Counter()
        self.tried: set[Edge] = set()
        self.open_edges: set[OpenEdge] = set()
        self.seen_cells: set[Cell] = set()
        self.transitions: Counter[tuple[str, str]] = Counter()
        self.room_entry_from: dict[str, str] = {}
        self.suppressed_room_links: set[frozenset[str]] = set()
        self.warps: Counter[Warp] = Counter()
        self.exit_probes: Counter[Edge] = Counter()
        self.character_probes: Counter[Edge] = Counter()
        self.interactables: dict[InteractableKey, dict[str, object]] = {}
        self.screen_regions: dict[ScreenRegionKey, dict[str, object]] = {}
        self.choice_trials: list[dict[str, object]] = []
        self.load_warning: str | None = None

    @classmethod
    def load(cls, path: Path | None) -> WorldModel:
        model = cls(path)
        if path is None or not path.exists():
            return model
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = int(data.get("version", 1))
            if version not in {1, cls.VERSION}:
                raise ValueError(f"unsupported world-model version {version!r}")
            stored_cell_size = int(
                data.get("cell_size", 16 if version == 1 else CELL_SIZE)
            )
            if stored_cell_size < CELL_SIZE or stored_cell_size % CELL_SIZE:
                raise ValueError(f"unsupported map cell size {stored_cell_size!r}")
            scale = stored_cell_size // CELL_SIZE

            def coordinate(value: object) -> int:
                return int(value) * scale

            for item in data.get("cells", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                )
                model.visits[key] = int(item["visits"])
                model.seen_cells.add(key)
            for item in data.get("tried_edges", []):
                model.tried.add(
                    (
                        str(item["room"]),
                        coordinate(item["x"]),
                        coordinate(item["y"]),
                        str(item["direction"]),
                    )
                )
            for item in data.get("blocked_edges", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.blocked[key] = int(item["failures"])
            for item in data.get("exit_probes", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.exit_probes[key] = int(item.get("attempts", 1))
            for item in data.get("character_probes", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.character_probes[key] = int(item.get("attempts", 1))
            for item in data.get("open_edges", []):
                model._load_open_path(
                    str(item["room"]),
                    (coordinate(item["from_x"]), coordinate(item["from_y"])),
                    str(item["direction"]),
                    (coordinate(item["to_x"]), coordinate(item["to_y"])),
                )
            for item in data.get("warps", []):
                key = (
                    str(item["from_room"]),
                    coordinate(item["from_x"]),
                    coordinate(item["from_y"]),
                    str(item["action"]),
                    str(item["to_room"]),
                    coordinate(item["to_x"]),
                    coordinate(item["to_y"]),
                )
                count = int(item["count"])
                model.warps[key] = count
                model.transitions[(key[0], key[4])] += count
            entries = data.get("room_entry_from", {})
            if isinstance(entries, dict):
                model.room_entry_from = {
                    str(room): str(source)
                    for room, source in entries.items()
                    if str(room) and str(source)
                }
            for item in data.get("suppressed_room_links", []):
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    source, target = (str(value) for value in item)
                    if source and target and source != target:
                        model.suppressed_room_links.add(frozenset((source, target)))
            for item in data.get("interactables", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                )
                model.interactables[key] = {
                    "name": str(item.get("name") or "interaction"),
                    "instance_id": (
                        int(item["instance_id"])
                        if item.get("instance_id") is not None
                        else None
                    ),
                    "confirmations": int(item.get("confirmations", 1)),
                    "attempts": int(item.get("attempts", item.get("confirmations", 1))),
                    "dialogue_steps": int(item.get("dialogue_steps", 0)),
                    "cutscene_steps": int(item.get("cutscene_steps", 0)),
                    "progressions": int(item.get("progressions", 0)),
                    "last_story_epoch": int(item.get("last_story_epoch", 0)),
                    "choice_menus": int(item.get("choice_menus", 0)),
                    "classification": str(item.get("classification") or "unknown"),
                    "usefulness": str(item.get("usefulness") or "unknown"),
                    "last_outcome": str(item.get("last_outcome") or "unknown"),
                    "outcome_counts": {
                        str(name): int(count)
                        for name, count in (
                            item.get("outcome_counts") or {}
                        ).items()
                    }
                    if isinstance(item.get("outcome_counts"), dict)
                    else {},
                    "approaches": [
                        {
                            "x": coordinate(approach["x"]),
                            "y": coordinate(approach["y"]),
                            "direction": str(approach["direction"]),
                        }
                        for approach in item.get("approaches", [])
                        if isinstance(approach, dict)
                        and str(approach.get("direction"))
                        in {"up", "down", "left", "right"}
                    ],
                }
            for item in data.get("screen_regions", []):
                key = (
                    str(item["room"]),
                    int(item["region_x"]),
                    int(item["region_y"]),
                )
                model.screen_regions[key] = {
                    "views": int(item.get("views", 1)),
                    "interest": float(item.get("interest", 0.0)),
                    "hypothesis": item.get("hypothesis"),
                    "inspections": int(item.get("inspections", 0)),
                    "motion": float(item.get("motion", 0.0)),
                    "last_interest": float(
                        item.get("last_interest", item.get("interest", 0.0))
                    ),
                    "last_signature": str(item.get("last_signature") or ""),
                    "walkable_evidence": bool(item.get("walkable_evidence", False)),
                    "entity_approach_directions": int(
                        item.get("entity_approach_directions", 0)
                    ),
                    "obstruction_target_cells": int(
                        item.get("obstruction_target_cells", 0)
                    ),
                    "character_probe_version": int(
                        item.get("character_probe_version", 0)
                    ),
                    "path_continuation": bool(item.get("path_continuation", False)),
                    "guess_misses": int(item.get("guess_misses", 0)),
                }
            for item in data.get("choice_trials", []):
                if not isinstance(item, dict):
                    continue
                model.choice_trials.append(
                    {
                        "room": str(item.get("room") or "unknown"),
                        "context_x": int(item.get("context_x", -1)),
                        "context_y": int(item.get("context_y", -1)),
                        "signature": str(item.get("signature") or ""),
                        "attempts": [int(value) for value in item.get("attempts", [])],
                        "failures": [int(value) for value in item.get("failures", [])],
                        "successes": [int(value) for value in item.get("successes", [])],
                        "successful_pattern": (
                            int(item["successful_pattern"])
                            if item.get("successful_pattern") is not None
                            else None
                        ),
                    }
                )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            model = cls(path)
            model.load_warning = f"Could not load {path}: {exc}. Starting with empty memory."
        return model

    def _load_open_path(
        self,
        room: str,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        """Migrate only observed cardinal paths into adjacent routing edges."""
        vectors = {
            "up": (0, -1),
            "down": (0, 1),
            "left": (-1, 0),
            "right": (1, 0),
        }
        opposites = {
            "up": "down",
            "down": "up",
            "left": "right",
            "right": "left",
        }
        vector = vectors.get(direction)
        if vector is None:
            return
        dx = target[0] - source[0]
        dy = target[1] - source[1]
        forward = dx * vector[0] + dy * vector[1]
        lateral = abs(dx * vector[1] - dy * vector[0])
        if forward <= 0 or lateral != 0:
            # Old maps could contain diagonal links inferred across packet gaps.
            # Dropping those is safer than teaching the planner a route it never took.
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector[0], current[1] + vector[1])
            self.open_edges.add((room, *current, direction, *following))
            self.open_edges.add((room, *following, opposites[direction], *current))
            current = following

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "version": self.VERSION,
            "cell_size": CELL_SIZE,
            "cells": [
                {"room": room, "x": x, "y": y, "visits": count}
                for (room, x, y), count in sorted(self.visits.items())
            ],
            "tried_edges": [
                {"room": room, "x": x, "y": y, "direction": direction}
                for room, x, y, direction in sorted(self.tried)
            ],
            "blocked_edges": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "failures": count,
                }
                for (room, x, y, direction), count in sorted(self.blocked.items())
            ],
            "exit_probes": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "attempts": count,
                }
                for (room, x, y, direction), count in sorted(
                    self.exit_probes.items()
                )
            ],
            "character_probes": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "attempts": count,
                }
                for (room, x, y, direction), count in sorted(
                    self.character_probes.items()
                )
            ],
            "open_edges": [
                {
                    "room": room,
                    "from_x": source_x,
                    "from_y": source_y,
                    "direction": direction,
                    "to_x": target_x,
                    "to_y": target_y,
                }
                for room, source_x, source_y, direction, target_x, target_y in sorted(
                    self.open_edges
                )
            ],
            "warps": [
                {
                    "from_room": source,
                    "from_x": source_x,
                    "from_y": source_y,
                    "action": action,
                    "to_room": target,
                    "to_x": target_x,
                    "to_y": target_y,
                    "count": count,
                }
                for (
                    source,
                    source_x,
                    source_y,
                    action,
                    target,
                    target_x,
                    target_y,
                ), count in sorted(self.warps.items())
            ],
            "room_entry_from": dict(sorted(self.room_entry_from.items())),
            "suppressed_room_links": [
                list(sorted(link))
                for link in sorted(
                    self.suppressed_room_links,
                    key=lambda value: tuple(sorted(value)),
                )
            ],
            "interactables": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "name": record.get("name") or "interaction",
                    "instance_id": record.get("instance_id"),
                    "confirmations": int(record.get("confirmations", 1)),
                    "attempts": int(record.get("attempts", record.get("confirmations", 1))),
                    "dialogue_steps": int(record.get("dialogue_steps", 0)),
                    "cutscene_steps": int(record.get("cutscene_steps", 0)),
                    "progressions": int(record.get("progressions", 0)),
                    "last_story_epoch": int(record.get("last_story_epoch", 0)),
                    "choice_menus": int(record.get("choice_menus", 0)),
                    "classification": str(
                        record.get("classification") or "unknown"
                    ),
                    "usefulness": str(record.get("usefulness") or "unknown"),
                    "last_outcome": str(record.get("last_outcome") or "unknown"),
                    "outcome_counts": dict(record.get("outcome_counts", {}))
                    if isinstance(record.get("outcome_counts"), dict)
                    else {},
                    "approaches": list(record.get("approaches", [])),
                }
                for (room, x, y), record in sorted(self.interactables.items())
            ],
            "screen_regions": [
                {
                    "room": room,
                    "region_x": region_x,
                    "region_y": region_y,
                    "views": int(record.get("views", 1)),
                    "interest": float(record.get("interest", 0.0)),
                    "hypothesis": record.get("hypothesis"),
                    "inspections": int(record.get("inspections", 0)),
                    "motion": float(record.get("motion", 0.0)),
                    "last_interest": float(
                        record.get("last_interest", record.get("interest", 0.0))
                    ),
                    "last_signature": str(record.get("last_signature") or ""),
                    "walkable_evidence": bool(
                        record.get("walkable_evidence", False)
                    ),
                    "entity_approach_directions": int(
                        record.get("entity_approach_directions", 0)
                    ),
                    "obstruction_target_cells": int(
                        record.get("obstruction_target_cells", 0)
                    ),
                    "character_probe_version": int(
                        record.get("character_probe_version", 0)
                    ),
                    "path_continuation": bool(
                        record.get("path_continuation", False)
                    ),
                    "guess_misses": int(record.get("guess_misses", 0)),
                }
                for (room, region_x, region_y), record in sorted(
                    self.screen_regions.items()
                )
            ],
            "choice_trials": [dict(record) for record in self.choice_trials],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
