from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


Cell = tuple[str, int, int]
Edge = tuple[str, int, int, str]
OpenEdge = tuple[str, int, int, str, int, int]
Warp = tuple[str, int, int, str, str, int, int]
InteractableKey = tuple[str, int, int]
CELL_SIZE = 8


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
        self.warps: Counter[Warp] = Counter()
        self.interactables: dict[InteractableKey, dict[str, object]] = {}
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
            "interactables": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "name": record.get("name") or "interaction",
                    "instance_id": record.get("instance_id"),
                    "confirmations": int(record.get("confirmations", 1)),
                    "approaches": list(record.get("approaches", [])),
                }
                for (room, x, y), record in sorted(self.interactables.items())
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
