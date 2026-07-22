from __future__ import annotations

from dataclasses import replace
from math import floor

from PIL import Image

from . import policy as policy_module
from .screen_regions import (
    REGION_PIXELS,
    ScreenRegionObservation,
    analyze_screen_regions as base_analyze_screen_regions,
)
from .telemetry import TelemetrySample
from .viewport import camera_viewport_box


FLOOR_DARK_LUMA = 42.0
FLOOR_MIN_ACTIVE_RATIO = 0.68
FLOOR_MIN_RUN_WORLD = 8
FLOOR_MAX_EDGE_RATIO = 0.45
FLOOR_PROBE_DEPTH_WORLD = 14
FLOOR_EVIDENCE_PREFIX = "visible floor-colored continuation"


def _luma(pixel: tuple[int, int, int]) -> float:
    red, green, blue = pixel
    return (red * 299 + green * 587 + blue * 114) / 1000


def _looks_like_room_surface(pixel: tuple[int, int, int]) -> bool:
    # Deltarune's out-of-room void is near-black. A visible continuation can be
    # any reasonably lit room color; it does not need to be orange or match a
    # hard-coded chapter palette.
    return _luma(pixel) >= FLOOR_DARK_LUMA and max(pixel) >= 55


def _runs(active: list[bool]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(active + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index))
            start = None
    return result


def _edge_floor_candidate(
    image: Image.Image,
    *,
    edge: str,
    camera_x: float,
    camera_y: float,
    camera_width: int,
    camera_height: int,
    room_width: float,
    room_height: float,
) -> tuple[float, float, tuple[float, float, float, float]] | None:
    if edge in {"top", "bottom"}:
        edge_world = 0.0 if edge == "top" else room_height
        if not (camera_y - 0.5 <= edge_world <= camera_y + camera_height + 0.5):
            return None
        edge_index = 0 if edge == "top" else camera_height - 1
        lanes = camera_width
        depth = min(FLOOR_PROBE_DEPTH_WORLD, camera_height)

        def pixel(lane: int, level: int) -> tuple[int, int, int]:
            y = edge_index + level if edge == "top" else edge_index - level
            return image.getpixel((lane, max(0, min(camera_height - 1, y))))

        lane_world_start = camera_x
        room_edge_length = room_width
    else:
        edge_world = 0.0 if edge == "left" else room_width
        if not (camera_x - 0.5 <= edge_world <= camera_x + camera_width + 0.5):
            return None
        edge_index = 0 if edge == "left" else camera_width - 1
        lanes = camera_height
        depth = min(FLOOR_PROBE_DEPTH_WORLD, camera_width)

        def pixel(lane: int, level: int) -> tuple[int, int, int]:
            x = edge_index + level if edge == "left" else edge_index - level
            return image.getpixel((max(0, min(camera_width - 1, x)), lane))

        lane_world_start = camera_y
        room_edge_length = room_height

    ratios: list[float] = []
    active: list[bool] = []
    for lane in range(lanes):
        samples = [_looks_like_room_surface(pixel(lane, level)) for level in range(depth)]
        ratio = sum(samples) / max(1, len(samples))
        ratios.append(ratio)
        active.append(bool(samples and samples[0] and ratio >= FLOOR_MIN_ACTIVE_RATIO))

    candidates: list[tuple[float, int, int, float]] = []
    for start, end in _runs(active):
        width = end - start
        if width < FLOOR_MIN_RUN_WORLD:
            continue
        edge_ratio = width / max(1.0, room_edge_length)
        if edge_ratio > FLOOR_MAX_EDGE_RATIO:
            continue
        continuity = sum(ratios[start:end]) / max(1, width)
        left_flank = start == 0 or not active[start - 1]
        right_flank = end == lanes or not active[end]
        flank_score = (int(left_flank) + int(right_flank)) / 2
        # Typical door/corridor widths are localized, but retain a broad range
        # rather than assuming one game's exact tile size.
        width_preference = max(0.0, 1.0 - abs(width - 28) / 48)
        score = min(
            0.98,
            0.58 + continuity * 0.22 + flank_score * 0.10 + width_preference * 0.08,
        )
        candidates.append((score, start, end, continuity))
    if not candidates:
        return None

    score, start, end, _continuity = max(
        candidates,
        key=lambda item: (item[0], item[2] - item[1], -item[1]),
    )
    world_start = lane_world_start + start
    world_end = lane_world_start + end
    if edge == "top":
        box = (world_start, 0.0, world_end, float(depth))
    elif edge == "bottom":
        box = (world_start, room_height - depth, world_end, room_height)
    elif edge == "left":
        box = (0.0, world_start, float(depth), world_end)
    else:
        box = (room_width - depth, world_start, room_width, world_end)
    return score, (end - start) / max(1.0, room_edge_length), box


def _visible_floor_continuations(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> list[tuple[str, float, float, tuple[float, float, float, float]]]:
    values = (
        telemetry.camera_x,
        telemetry.camera_y,
        telemetry.camera_width,
        telemetry.camera_height,
        telemetry.room_width,
        telemetry.room_height,
    )
    if any(value is None for value in values):
        return []
    camera_x, camera_y, camera_width, camera_height, room_width, room_height = (
        float(value) for value in values
    )
    if min(camera_width, camera_height, room_width, room_height) <= 0:
        return []
    pixel_width = max(1, round(camera_width))
    pixel_height = max(1, round(camera_height))
    viewport = camera_viewport_box(frame.size, (camera_width, camera_height))
    if viewport is None:
        return []
    image = frame.convert("RGB").crop(viewport).resize(
        (pixel_width, pixel_height),
        Image.Resampling.BILINEAR,
    )
    result = []
    for edge in ("top", "right", "bottom", "left"):
        candidate = _edge_floor_candidate(
            image,
            edge=edge,
            camera_x=camera_x,
            camera_y=camera_y,
            camera_width=pixel_width,
            camera_height=pixel_height,
            room_width=room_width,
            room_height=room_height,
        )
        if candidate is not None:
            score, width_ratio, box = candidate
            result.append((edge, score, width_ratio, box))
    return result


def analyze_screen_regions(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> list[ScreenRegionObservation]:
    observations = base_analyze_screen_regions(frame, telemetry)
    if not observations:
        return observations
    by_region = {
        (observation.region_x, observation.region_y): observation
        for observation in observations
    }
    room_width = float(telemetry.room_width or 0.0)
    room_height = float(telemetry.room_height or 0.0)
    for edge, score, width_ratio, box in _visible_floor_continuations(frame, telemetry):
        left, top, right, bottom = box
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        inside_x = min(max(center_x, 0.0), max(0.0, room_width - 1e-3))
        inside_y = min(max(center_y, 0.0), max(0.0, room_height - 1e-3))
        if edge == "top":
            inside_y = 0.0
        elif edge == "bottom":
            inside_y = max(0.0, room_height - 1e-3)
        elif edge == "left":
            inside_x = 0.0
        else:
            inside_x = max(0.0, room_width - 1e-3)
        key = (floor(inside_x / REGION_PIXELS), floor(inside_y / REGION_PIXELS))
        observation = by_region.get(key)
        if observation is None:
            continue
        summary = (
            f"{FLOOR_EVIDENCE_PREFIX} touching the true {edge} room boundary"
        )
        by_region[key] = replace(
            observation,
            interest=max(0.68, observation.interest),
            hypothesis="possible_exit",
            focus_world_x=center_x,
            focus_world_y=center_y,
            feature_box_world=box,
            edge_hint=edge,
            feature_summary=summary,
            edge_opening_score=max(0.82, score),
            edge_width_ratio=width_ratio,
            passage_box_world=box,
        )
    return [by_region[(item.region_x, item.region_y)] for item in observations]


def install_run13_screen_region_analyzer() -> None:
    policy_module.analyze_screen_regions = analyze_screen_regions
