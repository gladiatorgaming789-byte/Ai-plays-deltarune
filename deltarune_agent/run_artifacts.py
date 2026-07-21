"""Pure helpers for producing portable, inspectable run artifacts.

This module deliberately has no dependency on the live controller.  It can be
used by tests, replay tooling, and the GUI without importing Windows input or
capture code.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from PIL import Image, ImageDraw

from .map_guesses import visual_guess_entries


RUN_SCHEMA_VERSION = 2
_DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
_OPPOSITES = {
    "up": "down",
    "down": "up",
    "left": "right",
    "right": "left",
}


def utc_now_iso() -> str:
    """Return a stable, JSON-friendly UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def json_safe(value: Any) -> Any:
    """Convert common project values to deterministic JSON-compatible data.

    Optional diagnostic data should never terminate a live game run merely
    because it contains a ``Path``, tuple, enum, dataclass, set, or non-finite
    float. Unknown values fall back to their string representation.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_safe(item) for item in sorted(value, key=repr)]
    namespace = getattr(value, "__dict__", None)
    if isinstance(namespace, dict):
        return json_safe(
            {
                key: item
                for key, item in namespace.items()
                if not key.startswith("_")
            }
        )
    return repr(value)


def write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    """Atomically write a formatted JSON object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(json_safe(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def copy_run_snapshots(
    run_directory: Path,
    *,
    navigation_path: Path | None = None,
    room_views_path: Path | None = None,
    extra_files: Mapping[str, Path] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Copy supplied learned-memory files into a run directory.

    Missing or unreadable optional sources are reported as warnings rather
    than breaking ``finish()``. Destination names for ``extra_files`` must be
    simple relative paths and may not escape the run directory.
    """
    copied: dict[str, str] = {}
    warnings: list[str] = []

    def copy_file(source: Path, relative: Path, label: str) -> None:
        destination = run_directory / relative
        try:
            if not source.is_file():
                warnings.append(f"{label} snapshot not found: {source}")
                return
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            copied[label] = relative.as_posix()
        except OSError as exc:
            warnings.append(f"Could not copy {label} snapshot: {exc}")

    if navigation_path is not None:
        copy_file(Path(navigation_path), Path("navigation.json"), "navigation")

    if room_views_path is not None:
        source = Path(room_views_path)
        destination = run_directory / "room_views"
        try:
            if source.is_dir():
                if source.resolve() != destination.resolve():
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                copied["room_views"] = "room_views/index.json"
            elif source.is_file() and source.name.casefold() == "index.json":
                if source.parent.resolve() != destination.resolve():
                    shutil.copytree(
                        source.parent,
                        destination,
                        dirs_exist_ok=True,
                    )
                copied["room_views"] = "room_views/index.json"
            else:
                warnings.append(f"Room-view snapshot not found: {source}")
        except OSError as exc:
            warnings.append(f"Could not copy room-view snapshot: {exc}")

    for relative_name, source in (extra_files or {}).items():
        relative = Path(relative_name)
        if relative.is_absolute() or ".." in relative.parts:
            warnings.append(
                f"Ignored unsafe extra artifact destination: {relative_name}"
            )
            continue
        copy_file(Path(source), relative, f"extra:{relative.as_posix()}")
    return copied, warnings


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _room_name(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate)
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _safe_filename(room: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", room).strip("._")
    return (cleaned or "room")[:120]


def export_navigation_maps(
    navigation_path: Path,
    room_views_path: Path,
    destination: Path,
) -> list[Path]:
    """Render self-contained remembered-scene/navigation PNGs with Pillow.

    The renderer consumes the persisted formats, not live policy objects. This
    keeps exported evidence reproducible and makes it useful after the game or
    GUI has closed. It intentionally renders only already-learned data.
    """
    navigation = _read_object(Path(navigation_path))
    room_views_source = Path(room_views_path)
    index_path = (
        room_views_source / "index.json"
        if room_views_source.is_dir()
        else room_views_source
    )
    room_views = _read_object(index_path)
    room_root = index_path.parent.resolve()
    rooms_value = room_views.get("rooms")
    if not isinstance(rooms_value, dict):
        return []

    region_world = max(1.0, _number(room_views.get("region_pixels")) or 32.0)
    pixels_per_world = max(
        0.25,
        _number(room_views.get("pixels_per_world")) or 1.0,
    )
    tile_pixels = max(
        1,
        int(
            _number(room_views.get("tile_pixels"))
            or round(region_world * pixels_per_world)
        ),
    )
    cell_world = max(1.0, _number(navigation.get("cell_size")) or 8.0)

    collections: dict[str, list[dict[str, Any]]] = {}
    for key in (
        "cells",
        "open_edges",
        "blocked_edges",
        "warps",
        "warp_portals",
        "interactables",
        "screen_regions",
    ):
        value = navigation.get(key)
        collections[key] = [
            item for item in value or [] if isinstance(item, dict)
        ] if isinstance(value, list) else []

    destination.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for room, raw_room_record in sorted(rooms_value.items()):
        if not isinstance(raw_room_record, dict):
            continue
        raw_tiles = raw_room_record.get("tiles")
        if not isinstance(raw_tiles, dict) or not raw_tiles:
            continue
        tiles = [item for item in raw_tiles.values() if isinstance(item, dict)]
        tile_positions = [
            (
                _number(item.get("region_x")),
                _number(item.get("region_y")),
                item,
            )
            for item in tiles
        ]
        tile_positions = [
            (x, y, item)
            for x, y, item in tile_positions
            if x is not None and y is not None
        ]
        if not tile_positions:
            continue

        min_world_x = min(x * region_world for x, _y, _item in tile_positions)
        min_world_y = min(y * region_world for _x, y, _item in tile_positions)
        max_world_x = max(
            (x + 1.0) * region_world for x, _y, _item in tile_positions
        )
        max_world_y = max(
            (y + 1.0) * region_world for _x, y, _item in tile_positions
        )
        width = max(1, int(math.ceil((max_world_x - min_world_x) * pixels_per_world)))
        height = max(1, int(math.ceil((max_world_y - min_world_y) * pixels_per_world)))
        # Corrupt memory must not allocate an unbounded bitmap during finish.
        if width * height > 64_000_000:
            raise ValueError(
                f"Remembered view for {room!r} is too large to export: "
                f"{width}x{height}"
            )
        image = Image.new("RGB", (width, height), (7, 11, 18))

        for region_x, region_y, tile in tile_positions:
            relative_path = tile.get("path")
            if not isinstance(relative_path, str):
                continue
            tile_path = (room_root / relative_path).resolve()
            try:
                tile_path.relative_to(room_root)
            except ValueError:
                continue
            try:
                with Image.open(tile_path) as opened:
                    tile_image = opened.convert("RGB")
                    if tile_image.size != (tile_pixels, tile_pixels):
                        tile_image = tile_image.resize(
                            (tile_pixels, tile_pixels),
                            Image.Resampling.NEAREST,
                        )
                    left = round(
                        (region_x * region_world - min_world_x)
                        * pixels_per_world
                    )
                    top = round(
                        (region_y * region_world - min_world_y)
                        * pixels_per_world
                    )
                    image.paste(tile_image, (left, top))
            except (OSError, ValueError):
                continue

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        def point(cell_x: float, cell_y: float) -> tuple[float, float]:
            return (
                (cell_x * cell_world + cell_world / 2 - min_world_x)
                * pixels_per_world,
                (cell_y * cell_world + cell_world / 2 - min_world_y)
                * pixels_per_world,
            )

        def world_point(world_x: float, world_y: float) -> tuple[float, float]:
            return (
                (world_x - min_world_x) * pixels_per_world,
                (world_y - min_world_y) * pixels_per_world,
            )

        def world_box(
            value: object,
        ) -> tuple[float, float, float, float] | None:
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                return None
            coordinates = [_number(component) for component in value]
            if any(component is None for component in coordinates):
                return None
            left, top = world_point(coordinates[0], coordinates[1])
            right, bottom = world_point(coordinates[2], coordinates[3])
            if right <= left or bottom <= top:
                return None
            return left, top, right, bottom

        room_cells = [
            item
            for item in collections["cells"]
            if _room_name(item, "room") == room
        ]
        for item in room_cells:
            x, y = _number(item.get("x")), _number(item.get("y"))
            if x is None or y is None:
                continue
            cx, cy = point(x, y)
            radius = max(1.0, cell_world * pixels_per_world * 0.12)
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=(71, 181, 255, 145),
            )

        for item in collections["open_edges"]:
            if _room_name(item, "room") != room:
                continue
            x, y = _number(item.get("from_x")), _number(item.get("from_y"))
            target_x = _number(item.get("to_x"))
            target_y = _number(item.get("to_y"))
            if None in (x, y, target_x, target_y):
                continue
            draw.line(
                (point(x, y), point(target_x, target_y)),
                fill=(43, 220, 135, 175),
                width=max(1, round(pixels_per_world)),
            )

        for item in collections["blocked_edges"]:
            if _room_name(item, "room") != room:
                continue
            x, y = _number(item.get("x")), _number(item.get("y"))
            direction = str(item.get("direction") or "")
            delta = _DIRECTIONS.get(direction)
            if x is None or y is None or delta is None:
                continue
            cx, cy = point(x, y)
            half = cell_world * pixels_per_world / 2
            if direction in {"left", "right"}:
                edge_x = cx + delta[0] * half
                line = (edge_x, cy - half, edge_x, cy + half)
            else:
                edge_y = cy + delta[1] * half
                line = (cx - half, edge_y, cx + half, edge_y)
            draw.line(line, fill=(255, 82, 111, 230), width=max(2, round(pixels_per_world)))

        for item in collections["interactables"]:
            if _room_name(item, "room") != room:
                continue
            x, y = _number(item.get("x")), _number(item.get("y"))
            if x is None or y is None:
                continue
            cx, cy = point(x, y)
            radius = max(4, round(cell_world * pixels_per_world * 0.32))
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=(155, 75, 255, 120),
                outline=(224, 198, 255, 255),
                width=2,
            )
            draw.text((cx + radius + 2, cy - radius), "I", fill=(240, 225, 255, 255))

        # Render active hypotheses through the same geometry/grouping code as
        # the GUI. Their boxes therefore refer to the remembered world pixels,
        # not the 32x32 storage buckets that happened to observe them.
        screen_region_map = {
            (int(item["region_x"]), int(item["region_y"])): item
            for item in collections["screen_regions"]
            if _room_name(item, "room") == room
            and item.get("region_x") is not None
            and item.get("region_y") is not None
        }
        interactable_map = {
            (int(item["x"]), int(item["y"])): item
            for item in collections["interactables"]
            if _room_name(item, "room") == room
            and item.get("x") is not None
            and item.get("y") is not None
        }
        warp_map = {
            (
                int(item.get("from_x", item.get("x", 0))),
                int(item.get("from_y", item.get("y", 0))),
                str(item.get("to_room") or "?"),
            ): item
            for item in collections["warps"]
            if _room_name(item, "from_room", "room") == room
        }
        guess_colors = {
            "possible_exit": (255, 201, 51, 245),
            "possible_character": (224, 92, 255, 245),
            "possible_interactable": (87, 189, 255, 245),
        }
        guess_room = SimpleNamespace(
            screen_regions=screen_region_map,
            interactables=interactable_map,
            warps=warp_map,
        )
        for guess in visual_guess_entries(room, guess_room):
            color = guess_colors.get(guess.hypothesis, (230, 230, 230, 235))
            extent = world_box(guess.feature_box_world)
            if extent is not None:
                draw.rectangle(extent, outline=color, width=2)
            anchor_x, anchor_y = world_point(*guess.anchor_world)
            radius = 7
            draw.ellipse(
                (
                    anchor_x - radius,
                    anchor_y - radius,
                    anchor_x + radius,
                    anchor_y + radius,
                ),
                fill=(8, 12, 20, 230),
                outline=color,
                width=2,
            )
            draw.text(
                (anchor_x - 5, anchor_y - 5),
                guess.marker,
                fill=color,
            )

        role_colors = {
            "progression": (69, 224, 143, 245),
            "new_area": (78, 169, 255, 245),
            "likely_optional": (192, 129, 255, 245),
            "return/backtrack": (158, 171, 192, 245),
            "loop_suppressed": (255, 92, 116, 245),
            "unknown": (255, 196, 42, 245),
        }
        role_badges = {
            "progression": "P",
            "new_area": "N",
            "likely_optional": "O",
            "return/backtrack": "R",
            "loop_suppressed": "L",
            "unknown": "?",
        }
        portals = [
            item
            for item in collections["warp_portals"]
            if _room_name(item, "from_room", "room") == room
        ]
        if portals:
            portal_records = []
            for item in portals:
                footprint = item.get("source_footprint")
                center = footprint.get("center") if isinstance(footprint, dict) else None
                bounds = footprint.get("bounds") if isinstance(footprint, dict) else None
                if not isinstance(center, (list, tuple)) or len(center) != 2:
                    continue
                x, y = _number(center[0]), _number(center[1])
                if x is None or y is None:
                    continue
                portal_records.append((item, x, y, bounds))
        else:
            portal_records = [
                (
                    item,
                    _number(item.get("from_x", item.get("x"))),
                    _number(item.get("from_y", item.get("y"))),
                    None,
                )
                for item in collections["warps"]
                if _room_name(item, "from_room", "room") == room
            ]

        for item, x, y, bounds in portal_records:
            if x is None or y is None:
                continue
            role = str(item.get("role") or "unknown")
            color = role_colors.get(role, role_colors["unknown"])
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                values = [_number(value) for value in bounds]
                if all(value is not None for value in values):
                    aperture = world_box(
                        [
                            values[0] * cell_world,
                            values[1] * cell_world,
                            (values[2] + 1) * cell_world,
                            (values[3] + 1) * cell_world,
                        ]
                    )
                    if aperture is not None:
                        draw.rectangle(aperture, outline=color, width=2)
            cx, cy = point(x, y)
            radius = max(5, round(cell_world * pixels_per_world * 0.38))
            diamond = (
                (cx, cy - radius),
                (cx + radius, cy),
                (cx, cy + radius),
                (cx - radius, cy),
            )
            draw.polygon(diamond, fill=color, outline=(255, 247, 205, 255))
            draw.text(
                (cx - 3, cy - 5),
                role_badges.get(role, "?"),
                fill=(7, 11, 18, 255),
            )

        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        output = destination / f"{_safe_filename(room)}.png"
        image.save(output, compress_level=1)
        exported.append(output)
    return exported


def build_run_diagnostics(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize behavior patterns that are useful when auditing a run."""
    action_counts: Counter[str] = Counter()
    state_counts: Counter[str] = Counter()
    room_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    loop_recovery_steps = 0
    direction_reversals = 0
    interaction_steps = 0
    previous_action: str | None = None
    event_count = 0

    for event in events:
        event_count += 1
        action = str(event.get("action") or "wait")
        state = str(event.get("state") or "unknown")
        reason = str(event.get("reason") or "")
        reason_key = reason.split(":", 1)[0].strip() or "unspecified"
        action_counts[action] += 1
        state_counts[state] += 1
        reason_counts[reason_key] += 1
        if "loop recovery" in reason.casefold():
            loop_recovery_steps += 1
        if action == "confirm":
            interaction_steps += 1
        if _OPPOSITES.get(action) == previous_action:
            direction_reversals += 1
        previous_action = action
        telemetry = event.get("telemetry")
        if isinstance(telemetry, Mapping):
            room = _room_name(telemetry, "room_name", "room_id")
            if room:
                room_counts[room] += 1

    return {
        "event_count": event_count,
        "action_counts": dict(sorted(action_counts.items())),
        "state_counts": dict(sorted(state_counts.items())),
        "room_step_counts": dict(sorted(room_counts.items())),
        "top_reason_categories": [
            {"reason": reason, "steps": count}
            for reason, count in reason_counts.most_common(12)
        ],
        "loop_recovery_steps": loop_recovery_steps,
        "direction_reversals": direction_reversals,
        "interaction_steps": interaction_steps,
    }
