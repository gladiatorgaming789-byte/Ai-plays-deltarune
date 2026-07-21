from __future__ import annotations

import json
import math
from pathlib import Path
import re

from PIL import Image

from . import run_artifacts


_BASE_EXPORT = run_artifacts.export_navigation_maps


def _safe_filename(room: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", room).strip("._")
    return (cleaned or "room")[:120]


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def crop_navigation_map_to_room_bounds(
    image_path: Path,
    room_record: dict[str, object],
    *,
    region_world: float,
    pixels_per_world: float,
) -> None:
    """Crop a tile-rounded export to the exact telemetry room rectangle."""

    room_width = _number(room_record.get("room_width"))
    room_height = _number(room_record.get("room_height"))
    tiles = room_record.get("tiles")
    if (
        room_width is None
        or room_height is None
        or room_width <= 0
        or room_height <= 0
        or not isinstance(tiles, dict)
        or not tiles
        or not image_path.is_file()
    ):
        return

    positions: list[tuple[float, float]] = []
    for raw in tiles.values():
        if not isinstance(raw, dict):
            continue
        x = _number(raw.get("region_x"))
        y = _number(raw.get("region_y"))
        if x is not None and y is not None:
            positions.append((x, y))
    if not positions:
        return

    min_world_x = min(x * region_world for x, _y in positions)
    min_world_y = min(y * region_world for _x, y in positions)
    origin = room_record.get("origin_world")
    if isinstance(origin, (list, tuple)) and len(origin) == 2:
        origin_x = _number(origin[0]) or 0.0
        origin_y = _number(origin[1]) or 0.0
    else:
        origin_x = 0.0
        origin_y = 0.0

    target_width = max(1, round(room_width * pixels_per_world))
    target_height = max(1, round(room_height * pixels_per_world))
    source_left = round((origin_x - min_world_x) * pixels_per_world)
    source_top = round((origin_y - min_world_y) * pixels_per_world)

    with Image.open(image_path) as opened:
        source = opened.convert("RGB")
    canvas = Image.new("RGB", (target_width, target_height), (7, 11, 18))

    source_box = (
        max(0, source_left),
        max(0, source_top),
        min(source.width, source_left + target_width),
        min(source.height, source_top + target_height),
    )
    if source_box[2] > source_box[0] and source_box[3] > source_box[1]:
        destination = (
            max(0, -source_left),
            max(0, -source_top),
        )
        canvas.paste(source.crop(source_box), destination)
    canvas.save(image_path, compress_level=1)


def export_navigation_maps(
    navigation_path: Path,
    room_views_path: Path,
    destination: Path,
) -> list[Path]:
    """Export maps and align their canvas with exact telemetry room bounds."""

    outputs = _BASE_EXPORT(navigation_path, room_views_path, destination)
    source = Path(room_views_path)
    index_path = source / "index.json" if source.is_dir() else source
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return outputs
    rooms = payload.get("rooms")
    if not isinstance(rooms, dict):
        return outputs
    region_world = max(1.0, _number(payload.get("region_pixels")) or 32.0)
    pixels_per_world = max(
        0.25,
        _number(payload.get("pixels_per_world")) or 1.0,
    )

    by_name = {path.name: path for path in outputs}
    for room, raw_record in rooms.items():
        if not isinstance(raw_record, dict):
            continue
        output = by_name.get(f"{_safe_filename(str(room))}.png")
        if output is not None:
            crop_navigation_map_to_room_bounds(
                output,
                raw_record,
                region_world=region_world,
                pixels_per_world=pixels_per_world,
            )
    return outputs


def install_aligned_navigation_exporter() -> None:
    """Install before progress.py imports its export helper."""

    run_artifacts.export_navigation_maps = export_navigation_maps
