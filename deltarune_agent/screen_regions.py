from __future__ import annotations

from dataclasses import dataclass
from math import floor, sqrt

from PIL import Image

from .telemetry import TelemetrySample
from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


REGION_PIXELS = CELL_SIZE * EXPLORATION_REGION_CELLS
MAX_VISUAL_HYPOTHESES = 12


@dataclass(frozen=True)
class ScreenRegionObservation:
    region_x: int
    region_y: int
    interest: float
    contrast: float
    edge_density: float
    colorfulness: float
    dark_ratio: float
    hypothesis: str | None = None
    appearance_signature: str = ""


def visible_region_coordinates(
    camera_x: float | None,
    camera_y: float | None,
    camera_width: float | None,
    camera_height: float | None,
    room_width: float | None = None,
    room_height: float | None = None,
) -> set[tuple[int, int]]:
    """Return regions intersecting the reported camera, clipped to the room."""
    if (
        camera_x is None
        or camera_y is None
        or camera_width is None
        or camera_height is None
        or camera_width <= 0
        or camera_height <= 0
    ):
        return set()
    left = max(0.0, camera_x)
    top = max(0.0, camera_y)
    right = camera_x + camera_width
    bottom = camera_y + camera_height
    if room_width is not None and room_width > 0:
        right = min(right, room_width)
    if room_height is not None and room_height > 0:
        bottom = min(bottom, room_height)
    if right <= left or bottom <= top:
        return set()
    last_x = floor((right - 1e-6) / REGION_PIXELS)
    last_y = floor((bottom - 1e-6) / REGION_PIXELS)
    return {
        (region_x, region_y)
        for region_x in range(floor(left / REGION_PIXELS), last_x + 1)
        for region_y in range(floor(top / REGION_PIXELS), last_y + 1)
    }


def analyze_screen_regions(
    frame: Image.Image,
    telemetry: TelemetrySample,
) -> list[ScreenRegionObservation]:
    """Map anonymous visual structure to world regions currently on camera."""
    coordinates = visible_region_coordinates(
        telemetry.camera_x,
        telemetry.camera_y,
        telemetry.camera_width,
        telemetry.camera_height,
        telemetry.room_width,
        telemetry.room_height,
    )
    if not coordinates:
        return []
    camera_x = float(telemetry.camera_x)
    camera_y = float(telemetry.camera_y)
    camera_width = float(telemetry.camera_width)
    camera_height = float(telemetry.camera_height)
    image = frame.convert("RGB")
    width, height = image.size
    observations: list[ScreenRegionObservation] = []
    for region_x, region_y in sorted(coordinates):
        world_left = max(region_x * REGION_PIXELS, camera_x, 0.0)
        world_top = max(region_y * REGION_PIXELS, camera_y, 0.0)
        world_right = min((region_x + 1) * REGION_PIXELS, camera_x + camera_width)
        world_bottom = min((region_y + 1) * REGION_PIXELS, camera_y + camera_height)
        if telemetry.room_width is not None:
            world_right = min(world_right, telemetry.room_width)
        if telemetry.room_height is not None:
            world_bottom = min(world_bottom, telemetry.room_height)
        left = max(0, floor((world_left - camera_x) / camera_width * width))
        top = max(0, floor((world_top - camera_y) / camera_height * height))
        right = min(width, max(left + 1, round((world_right - camera_x) / camera_width * width)))
        bottom = min(height, max(top + 1, round((world_bottom - camera_y) / camera_height * height)))
        if right - left < 2 or bottom - top < 2:
            continue
        crop = image.crop((left, top, right, bottom))
        sample_width = min(16, crop.width)
        sample_height = min(16, crop.height)
        crop = crop.resize((sample_width, sample_height), Image.Resampling.BILINEAR)
        pixels = list(crop.getdata())
        gray = [
            (red * 299 + green * 587 + blue * 114) / 1000
            for red, green, blue in pixels
        ]
        mean = sum(gray) / len(gray)
        deviation = sqrt(sum((value - mean) ** 2 for value in gray) / len(gray))
        contrast = min(1.0, deviation / 96.0)
        colorfulness = sum(
            max(pixel) - min(pixel) for pixel in pixels
        ) / (len(pixels) * 255)
        dark_ratio = sum(value < 35 for value in gray) / len(gray)
        edge_total = 0.0
        edge_count = 0
        for y in range(sample_height):
            for x in range(sample_width):
                index = y * sample_width + x
                if x:
                    edge_total += abs(gray[index] - gray[index - 1]) / 255
                    edge_count += 1
                if y:
                    edge_total += abs(gray[index] - gray[index - sample_width]) / 255
                    edge_count += 1
        edge_density = min(1.0, (edge_total / max(1, edge_count)) * 4.0)
        interest = min(
            1.0,
            contrast * 0.45 + edge_density * 0.40 + colorfulness * 0.15,
        )
        # The captured project run has a persistent ReShade notification band
        # over the top of the client. It is screen UI rather than room scenery,
        # so a crop wholly inside that narrow band cannot become a landmark.
        if bottom <= height * 0.10:
            interest = 0.0
        observations.append(
            ScreenRegionObservation(
                region_x,
                region_y,
                interest,
                contrast,
                edge_density,
                colorfulness,
                dark_ratio,
                appearance_signature="".join(
                    format(min(3, int(value) // 64), "x") for value in gray
                ),
            )
        )

    player_region = (
        floor(telemetry.x / REGION_PIXELS),
        floor(telemetry.y / REGION_PIXELS),
    )
    eligible = [
        observation
        for observation in observations
        if max(
            abs(observation.region_x - player_region[0]),
            abs(observation.region_y - player_region[1]),
        )
        > 1
        and observation.interest >= 0.12
        and observation.dark_ratio < 0.96
    ]
    ranked = sorted(
        eligible,
        key=lambda item: (-item.interest, item.region_y, item.region_x),
    )

    def edge_names(observation: ScreenRegionObservation) -> set[str]:
        left = observation.region_x * REGION_PIXELS
        top = observation.region_y * REGION_PIXELS
        right = left + REGION_PIXELS
        bottom = top + REGION_PIXELS
        names = set()
        wide_room = (
            telemetry.room_width is not None
            and telemetry.room_width >= REGION_PIXELS * 4
        )
        tall_room = (
            telemetry.room_height is not None
            and telemetry.room_height >= REGION_PIXELS * 4
        )
        if left <= 0 or (wide_room and left <= REGION_PIXELS):
            names.add("left")
        if top <= 0 or (tall_room and top <= REGION_PIXELS):
            names.add("top")
        if telemetry.room_width is not None and (
            right >= telemetry.room_width
            or (wide_room and right >= telemetry.room_width - REGION_PIXELS)
        ):
            names.add("right")
        if telemetry.room_height is not None and (
            bottom >= telemetry.room_height
            or (tall_room and bottom >= telemetry.room_height - REGION_PIXELS)
        ):
            names.add("bottom")
        return names

    # Static interior detail by itself is not evidence of an interactable or
    # NPC. Only edge landmarks are proposed from pixels here. The policy can
    # separately form a static character candidate after Kris has mapped a
    # compact obstruction from more than one approach direction.
    candidates: set[tuple[int, int]] = set()
    for edge_name in ("top", "right", "bottom", "left"):
        for observation in [
            item for item in ranked if edge_name in edge_names(item)
        ][:2]:
            if len(candidates) >= MAX_VISUAL_HYPOTHESES:
                break
            candidates.add((observation.region_x, observation.region_y))
    result: list[ScreenRegionObservation] = []
    for observation in observations:
        key = (observation.region_x, observation.region_y)
        hypothesis = None
        if key in candidates:
            left = observation.region_x * REGION_PIXELS
            top = observation.region_y * REGION_PIXELS
            right = left + REGION_PIXELS
            bottom = top + REGION_PIXELS
            near_room_edge = bool(edge_names(observation))
            hypothesis = "possible_exit" if near_room_edge else "possible_interactable"
        result.append(
            ScreenRegionObservation(
                observation.region_x,
                observation.region_y,
                observation.interest,
                observation.contrast,
                observation.edge_density,
                observation.colorfulness,
                observation.dark_ratio,
                hypothesis,
                observation.appearance_signature,
            )
        )
    return result
