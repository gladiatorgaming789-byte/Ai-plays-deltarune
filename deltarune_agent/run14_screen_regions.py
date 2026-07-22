from __future__ import annotations

from dataclasses import replace
from math import floor, sqrt

from PIL import Image

from . import policy as policy_module
from .run13_screen_regions import analyze_screen_regions as run13_analyze_screen_regions
from .screen_regions import REGION_PIXELS, ScreenRegionObservation
from .telemetry import TelemetrySample
from .viewport import camera_viewport_box


DOORWAY_FACADE_PREFIX = "rectangular doorway facade"
DOORWAY_MIN_WIDTH = 24
DOORWAY_MAX_WIDTH = 52
DOORWAY_MIN_HEIGHT = 36
DOORWAY_MAX_HEIGHT = 68
DOORWAY_MIN_SCORE = 0.60
DOORWAY_APPROACH_OFFSET = 8.0
DOORWAY_CACHE_LIMIT = 64
_DOORWAY_CACHE: dict[
    tuple[object, ...],
    list[tuple[float, tuple[float, float, float, float]]],
] = {}


def _luma(pixel: tuple[int, int, int]) -> float:
    red, green, blue = pixel
    return (red * 299 + green * 587 + blue * 114) / 1000


def _world_view(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> tuple[Image.Image, float, float, float, float] | None:
    values = (
        telemetry.camera_x,
        telemetry.camera_y,
        telemetry.camera_width,
        telemetry.camera_height,
    )
    if any(value is None for value in values):
        return None
    camera_x, camera_y, camera_width, camera_height = (
        float(value) for value in values
    )
    if camera_width <= 0 or camera_height <= 0:
        return None
    viewport = camera_viewport_box(frame.size, (camera_width, camera_height))
    if viewport is None:
        return None
    image = frame.convert("RGB").crop(viewport).resize(
        (max(1, round(camera_width)), max(1, round(camera_height))),
        Image.Resampling.NEAREST,
    )
    return image, camera_x, camera_y, camera_width, camera_height


def _content_bounds(gray: list[list[float]]) -> tuple[int, int, int, int]:
    height = len(gray)
    width = len(gray[0]) if gray else 0
    if width <= 0 or height <= 0:
        return 0, 0, width, height
    column_threshold = max(8, round(height * 0.33))
    row_threshold = max(8, round(width * 0.25))
    columns = [
        x
        for x in range(width)
        if sum(gray[y][x] > 20.0 for y in range(height)) >= column_threshold
    ]
    rows = [
        y
        for y in range(height)
        if sum(value > 20.0 for value in gray[y]) >= row_threshold
    ]
    if not columns or not rows:
        return 0, 0, width, height
    return min(columns), min(rows), max(columns) + 1, max(rows) + 1


def _line_prefixes(
    gray: list[list[float]],
) -> tuple[list[list[float]], list[list[float]]]:
    height = len(gray)
    width = len(gray[0]) if gray else 0
    vertical = [[0.0] * (height + 1) for _ in range(width)]
    horizontal = [[0.0] * (width + 1) for _ in range(height)]
    for y in range(height):
        horizontal_row = horizontal[y]
        running = 0.0
        for x in range(width):
            if y:
                running += abs(gray[y][x] - gray[y - 1][x])
            horizontal_row[x + 1] = running
    for x in range(width):
        column = vertical[x]
        running = 0.0
        for y in range(height):
            if x:
                running += abs(gray[y][x] - gray[y][x - 1])
            column[y + 1] = running
    return vertical, horizontal


def _integral_images(
    gray: list[list[float]],
) -> tuple[list[list[float]], list[list[float]]]:
    height = len(gray)
    width = len(gray[0]) if gray else 0
    summed = [[0.0] * (width + 1) for _ in range(height + 1)]
    squared = [[0.0] * (width + 1) for _ in range(height + 1)]
    for y in range(height):
        row_sum = 0.0
        row_square = 0.0
        for x in range(width):
            value = gray[y][x]
            row_sum += value
            row_square += value * value
            summed[y + 1][x + 1] = summed[y][x + 1] + row_sum
            squared[y + 1][x + 1] = squared[y][x + 1] + row_square
    return summed, squared


def _rectangle_sum(
    integral: list[list[float]],
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> float:
    return (
        integral[bottom][right]
        - integral[top][right]
        - integral[bottom][left]
        + integral[top][left]
    )


def _vertical_line_strength(
    prefixes: list[list[float]],
    x: int,
    top: int,
    bottom: int,
    radius: int = 2,
) -> float:
    if bottom <= top or not prefixes:
        return 0.0
    scores = []
    for candidate_x in range(
        max(1, x - radius),
        min(len(prefixes), x + radius + 1),
    ):
        column = prefixes[candidate_x]
        scores.append((column[bottom] - column[top]) / (bottom - top))
    return max(scores, default=0.0)


def _horizontal_line_strength(
    prefixes: list[list[float]],
    y: int,
    left: int,
    right: int,
    radius: int = 2,
) -> float:
    if right <= left or not prefixes:
        return 0.0
    scores = []
    for candidate_y in range(
        max(1, y - radius),
        min(len(prefixes), y + radius + 1),
    ):
        row = prefixes[candidate_y]
        scores.append((row[right] - row[left]) / (right - left))
    return max(scores, default=0.0)


def _box_overlap_ratio(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min(
        (first[2] - first[0]) * (first[3] - first[1]),
        (second[2] - second[0]) * (second[3] - second[1]),
    )
    return intersection / max(1, smaller)


def _doorway_facades(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> list[tuple[float, tuple[float, float, float, float]]]:
    view = _world_view(frame, telemetry)
    if view is None:
        return []
    image, camera_x, camera_y, _camera_width, _camera_height = view
    width, height = image.size
    pixels = image.load()
    top_sample_height = max(1, min(height, round(height * 0.42)))
    fingerprint = tuple(
        int(_luma(pixels[x, y]) // 32)
        for y in range(0, top_sample_height, 16)
        for x in range(0, width, 16)
    )
    cache_key = (
        getattr(telemetry, "room_name", None)
        or getattr(telemetry, "room_id", "unknown"),
        round(camera_x, 1),
        round(camera_y, 1),
        width,
        height,
        fingerprint,
    )
    cached = _DOORWAY_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    gray = [[_luma(pixels[x, y]) for x in range(width)] for y in range(height)]
    content_left, content_top, content_right, content_bottom = _content_bounds(gray)
    if content_right - content_left < DOORWAY_MIN_WIDTH:
        return []
    vertical, horizontal = _line_prefixes(gray)
    summed, squared = _integral_images(gray)

    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    top_limit = min(content_bottom - DOORWAY_MIN_HEIGHT, content_top + 18)
    for box_width in range(DOORWAY_MIN_WIDTH, DOORWAY_MAX_WIDTH + 1, 4):
        for box_height in range(DOORWAY_MIN_HEIGHT, DOORWAY_MAX_HEIGHT + 1, 4):
            aspect = box_height / box_width
            if not 1.15 <= aspect <= 2.20:
                continue
            for top in range(content_top, top_limit + 1, 2):
                bottom = top + box_height
                if bottom > content_bottom:
                    continue
                for left in range(
                    content_left,
                    content_right - box_width + 1,
                    2,
                ):
                    right = left + box_width
                    left_edge = _vertical_line_strength(
                        vertical,
                        left,
                        top + 2,
                        bottom - 2,
                    )
                    right_edge = _vertical_line_strength(
                        vertical,
                        right - 1,
                        top + 2,
                        bottom - 2,
                    )
                    top_edge = _horizontal_line_strength(
                        horizontal,
                        top,
                        left + 2,
                        right - 2,
                    )
                    bottom_edge = _horizontal_line_strength(
                        horizontal,
                        bottom - 1,
                        left + 2,
                        right - 2,
                    )
                    if (
                        min(left_edge, right_edge) < 100.0
                        or top_edge < 110.0
                        or bottom_edge < 70.0
                    ):
                        continue

                    inner_left = left + 3
                    inner_top = top + 3
                    inner_right = right - 3
                    inner_bottom = bottom - 3
                    area = max(
                        1,
                        (inner_right - inner_left) * (inner_bottom - inner_top),
                    )
                    total = _rectangle_sum(
                        summed,
                        inner_left,
                        inner_top,
                        inner_right,
                        inner_bottom,
                    )
                    total_square = _rectangle_sum(
                        squared,
                        inner_left,
                        inner_top,
                        inner_right,
                        inner_bottom,
                    )
                    mean = total / area
                    variance = max(0.0, total_square / area - mean * mean)
                    deviation = sqrt(variance)
                    # Bright paired panes are usually windows. Door facades in
                    # the supplied screenshots have a darker solid interior and
                    # a small asymmetric inset or handle. Keep this conservative
                    # so a decorative window does not become a navigation goal.
                    if mean > 165.0:
                        continue

                    border = (
                        min(left_edge, right_edge) * 0.55
                        + min(top_edge, bottom_edge) * 0.45
                    ) / 255.0
                    width_preference = max(
                        0.0,
                        1.0 - abs(box_width - 36) / 24,
                    )
                    aspect_preference = max(
                        0.0,
                        1.0 - abs(aspect - 1.40) / 0.80,
                    )
                    boundary_nearness = max(
                        0.0,
                        1.0 - (top - content_top) / 20.0,
                    )
                    chaos_penalty = max(0.0, (deviation - 68.0) / 70.0)
                    score = (
                        border * 0.65
                        + width_preference * 0.12
                        + aspect_preference * 0.08
                        + boundary_nearness * 0.10
                        + min(1.0, deviation / 70.0) * 0.05
                        - chaos_penalty * 0.14
                    )
                    if score >= DOORWAY_MIN_SCORE:
                        candidates.append((score, (left, top, right, bottom)))

    selected: list[tuple[float, tuple[int, int, int, int]]] = []
    for candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
        if any(
            _box_overlap_ratio(candidate[1], existing[1]) >= 0.45
            for existing in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= 2:
            break
    result = [
        (
            score,
            (
                camera_x + box[0],
                camera_y + box[1],
                camera_x + box[2],
                camera_y + box[3],
            ),
        )
        for score, box in selected
    ]
    if len(_DOORWAY_CACHE) >= DOORWAY_CACHE_LIMIT:
        _DOORWAY_CACHE.pop(next(iter(_DOORWAY_CACHE)))
    _DOORWAY_CACHE[cache_key] = list(result)
    return result


def analyze_screen_regions(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> list[ScreenRegionObservation]:
    observations = run13_analyze_screen_regions(frame, telemetry)
    if not observations:
        return observations
    by_region = {
        (observation.region_x, observation.region_y): observation
        for observation in observations
    }
    room_width = float(telemetry.room_width or 0.0)
    room_height = float(telemetry.room_height or 0.0)
    for score, box in _doorway_facades(frame, telemetry):
        left, top, right, bottom = box
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        approach_y = min(
            max(0.0, bottom + DOORWAY_APPROACH_OFFSET),
            max(0.0, room_height - 1e-3),
        )
        approach_x = min(
            max(0.0, center_x),
            max(0.0, room_width - 1e-3),
        )
        key = (
            floor(
                min(
                    max(center_x, 0.0),
                    max(0.0, room_width - 1e-3),
                )
                / REGION_PIXELS
            ),
            floor(
                min(
                    max(center_y, 0.0),
                    max(0.0, room_height - 1e-3),
                )
                / REGION_PIXELS
            ),
        )
        observation = by_region.get(key)
        if observation is None:
            continue
        summary = (
            f"{DOORWAY_FACADE_PREFIX} near the upper wall "
            f"(frame score {score:.0%})"
        )
        by_region[key] = replace(
            observation,
            interest=max(0.78, observation.interest),
            hypothesis="possible_exit",
            focus_world_x=approach_x,
            focus_world_y=approach_y,
            feature_box_world=box,
            edge_hint="top",
            feature_summary=summary,
            edge_opening_score=max(0.86, score),
            edge_width_ratio=(right - left) / max(1.0, room_width),
            passage_box_world=box,
        )
    return [by_region[(item.region_x, item.region_y)] for item in observations]


def install_run14_screen_region_analyzer() -> None:
    policy_module.analyze_screen_regions = analyze_screen_regions
