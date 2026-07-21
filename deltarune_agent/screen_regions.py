from __future__ import annotations

from dataclasses import dataclass
from math import floor, sqrt

from PIL import Image

from .telemetry import TelemetrySample
from .viewport import camera_viewport_box
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
    focus_world_x: float | None = None
    focus_world_y: float | None = None
    feature_box_world: tuple[float, float, float, float] | None = None
    edge_hint: str | None = None
    feature_summary: str = ""
    edge_opening_score: float = 0.0
    edge_width_ratio: float = 0.0
    passage_box_world: tuple[float, float, float, float] | None = None


def edge_opening_profile(
    gray: list[float],
    width: int,
    height: int,
    edge: str,
) -> tuple[float, float, tuple[int, int, int, int] | None]:
    """Score a localized dark channel connected to one side of a view crop.

    A broad room-art-to-black seam touches almost every lane and is deliberately
    weak. A plausible opening occupies a bounded span, has scenery on at least
    one flank, and continues inward for multiple samples.
    """
    if width < 3 or height < 3 or edge not in {"top", "right", "bottom", "left"}:
        return 0.0, 0.0, None
    mean = sum(gray) / max(1, len(gray))
    threshold = min(82.0, max(24.0, mean * 0.58))
    dark = [value <= threshold for value in gray]
    lanes = width if edge in {"top", "bottom"} else height
    depth = height if edge in {"top", "bottom"} else width

    def at(lane: int, level: int) -> bool:
        if edge == "top":
            x, y = lane, level
        elif edge == "bottom":
            x, y = lane, height - 1 - level
        elif edge == "left":
            x, y = level, lane
        else:
            x, y = width - 1 - level, lane
        return dark[y * width + x]

    probe_depth = max(3, min(depth, round(depth * 0.55)))
    channel_depths: list[int] = []
    for lane in range(lanes):
        continuous = 0
        for level in range(probe_depth):
            if not at(lane, level):
                break
            continuous += 1
        channel_depths.append(continuous)
    minimum_depth = max(2, round(probe_depth * 0.55))
    channel_lanes = {
        lane for lane, continuous in enumerate(channel_depths) if continuous >= minimum_depth
    }
    if not channel_lanes:
        return 0.0, 0.0, None

    runs: list[tuple[int, int]] = []
    start = previous = min(channel_lanes)
    for lane in sorted(channel_lanes)[1:]:
        if lane != previous + 1:
            runs.append((start, previous + 1))
            start = lane
        previous = lane
    runs.append((start, previous + 1))

    scored: list[tuple[float, float, int, int, int]] = []
    for start, end in runs:
        span = (end - start) / lanes
        if span < 0.08:
            continue
        average_depth = sum(channel_depths[start:end]) / max(1, end - start)
        continuity = min(1.0, average_depth / probe_depth)
        # Passage-like spans peak near one third of the edge. Full-width dark
        # bands are letterbox/background seams, not specific openings.
        localization = max(0.0, 1.0 - abs(span - 0.34) / 0.42)
        left_flank = start > 0 and channel_depths[start - 1] < minimum_depth
        right_flank = end < lanes and channel_depths[end] < minimum_depth
        flank_score = (int(left_flank) + int(right_flank)) / 2
        score = continuity * 0.48 + localization * 0.34 + flank_score * 0.18
        if span >= 0.78:
            score *= 0.18
        elif span >= 0.66:
            score *= 0.55
        if not left_flank and not right_flank:
            score *= 0.55
        max_depth = max(channel_depths[start:end])
        scored.append((score, span, start, end, max_depth))
    if not scored:
        return 0.0, 0.0, None
    score, span, start, end, connected_depth = max(
        scored,
        key=lambda item: (item[0], item[4], -item[2]),
    )
    if edge == "top":
        box = (start, 0, end, connected_depth)
    elif edge == "bottom":
        box = (start, height - connected_depth, end, height)
    elif edge == "left":
        box = (0, start, connected_depth, end)
    else:
        box = (width - connected_depth, start, width, end)
    return min(1.0, score), span, box


def _strongest_salient_component(
    salience: list[tuple[float, int, int]],
    width: int,
    height: int,
) -> list[tuple[float, int, int]]:
    """Keep one coherent visible feature instead of boxing unrelated pixels."""
    if not salience:
        return []
    ranked_scores = sorted((score for score, _x, _y in salience), reverse=True)
    threshold_index = min(len(ranked_scores) - 1, max(3, len(salience) // 8) - 1)
    threshold = ranked_scores[threshold_index]
    scores = {
        (x, y): score
        for score, x, y in salience
        if score >= threshold and score > 1e-6
    }
    if not scores:
        return []
    remaining = set(scores)
    components: list[list[tuple[float, int, int]]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        pending = [seed]
        component = []
        while pending:
            x, y = pending.pop()
            component.append((scores[(x, y)], x, y))
            for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
                for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = (neighbor_x, neighbor_y)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
        components.append(component)
    return max(
        components,
        key=lambda component: (
            sum(score for score, _x, _y in component),
            len(component),
            -min(y for _score, _x, y in component),
            -min(x for _score, x, _y in component),
        ),
    )


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
    viewport_box = camera_viewport_box(
        image.size,
        (camera_width, camera_height),
    )
    if viewport_box is None:
        return []
    image = image.crop(viewport_box)
    width, height = image.size
    observations: list[ScreenRegionObservation] = []
    opening_profiles: dict[
        tuple[int, int],
        dict[str, tuple[float, float, tuple[float, float, float, float] | None]],
    ] = {}
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
        salience: list[tuple[float, int, int]] = []
        for sample_y in range(sample_height):
            for sample_x in range(sample_width):
                index = sample_y * sample_width + sample_x
                local_edges = []
                if sample_x:
                    local_edges.append(abs(gray[index] - gray[index - 1]) / 255)
                if sample_x + 1 < sample_width:
                    local_edges.append(abs(gray[index] - gray[index + 1]) / 255)
                if sample_y:
                    local_edges.append(
                        abs(gray[index] - gray[index - sample_width]) / 255
                    )
                if sample_y + 1 < sample_height:
                    local_edges.append(
                        abs(gray[index] - gray[index + sample_width]) / 255
                    )
                pixel_color = (max(pixels[index]) - min(pixels[index])) / 255
                score = (
                    abs(gray[index] - mean) / 255 * 0.55
                    + (sum(local_edges) / max(1, len(local_edges))) * 0.30
                    + pixel_color * 0.15
                )
                salience.append((score, sample_x, sample_y))
        strongest = _strongest_salient_component(
            salience,
            sample_width,
            sample_height,
        )
        if not strongest:
            strongest = [
                (
                    1.0,
                    max(0, (sample_width - 1) // 2),
                    max(0, (sample_height - 1) // 2),
                )
            ]
        focus_weight = sum(score for score, _x, _y in strongest)
        if focus_weight <= 1e-9:
            focus_sample_x = (sample_width - 1) / 2
            focus_sample_y = (sample_height - 1) / 2
        else:
            focus_sample_x = sum(score * x for score, x, _y in strongest) / focus_weight
            focus_sample_y = sum(score * y for score, _x, y in strongest) / focus_weight
        focus_world_x = world_left + (focus_sample_x + 0.5) / sample_width * (
            world_right - world_left
        )
        focus_world_y = world_top + (focus_sample_y + 0.5) / sample_height * (
            world_bottom - world_top
        )
        feature_left = world_left + min(x for _score, x, _y in strongest) / sample_width * (
            world_right - world_left
        )
        feature_top = world_top + min(y for _score, _x, y in strongest) / sample_height * (
            world_bottom - world_top
        )
        feature_right = world_left + (
            max(x for _score, x, _y in strongest) + 1
        ) / sample_width * (world_right - world_left)
        feature_bottom = world_top + (
            max(y for _score, _x, y in strongest) + 1
        ) / sample_height * (world_bottom - world_top)
        feature_width = (
            max(x for _score, x, _y in strongest)
            - min(x for _score, x, _y in strongest)
            + 1
        ) / sample_width
        feature_height = (
            max(y for _score, _x, y in strongest)
            - min(y for _score, _x, y in strongest)
            + 1
        ) / sample_height
        if feature_width >= feature_height * 1.7 and feature_width >= 0.25:
            shape = "wide"
        elif feature_height >= feature_width * 1.7 and feature_height >= 0.25:
            shape = "tall"
        elif max(feature_width, feature_height) <= 0.45:
            shape = "compact"
        else:
            shape = "broad"
        qualities = [shape]
        if contrast >= 0.28:
            qualities.append("high-contrast")
        if edge_density >= 0.28:
            qualities.append("detailed")
        if colorfulness >= 0.18:
            qualities.append("colorful")
        if mean >= 180:
            qualities.append("bright")
        elif mean <= 55:
            qualities.append("dark")
        relative_x = (focus_world_x - world_left) / max(1e-6, world_right - world_left)
        relative_y = (focus_world_y - world_top) / max(1e-6, world_bottom - world_top)
        horizontal_position = (
            "left" if relative_x < 0.34 else "right" if relative_x > 0.66 else "center"
        )
        vertical_position = (
            "upper" if relative_y < 0.34 else "lower" if relative_y > 0.66 else "middle"
        )
        position = (
            f"{vertical_position}-{horizontal_position}"
            if horizontal_position != "center" and vertical_position != "middle"
            else horizontal_position
            if vertical_position == "middle"
            else vertical_position
        )
        feature_summary = (
            f"{' '.join(qualities[:4])} feature toward the {position} of its view region"
        )
        profiles: dict[
            str,
            tuple[float, float, tuple[float, float, float, float] | None],
        ] = {}
        for edge_name in ("top", "right", "bottom", "left"):
            opening_score, opening_width, sample_box = edge_opening_profile(
                gray,
                sample_width,
                sample_height,
                edge_name,
            )
            world_box = None
            if sample_box is not None:
                sample_left, sample_top, sample_right, sample_bottom = sample_box
                world_box = (
                    world_left
                    + sample_left / sample_width * (world_right - world_left),
                    world_top
                    + sample_top / sample_height * (world_bottom - world_top),
                    world_left
                    + sample_right / sample_width * (world_right - world_left),
                    world_top
                    + sample_bottom / sample_height * (world_bottom - world_top),
                )
            profiles[edge_name] = (opening_score, opening_width, world_box)
        opening_profiles[(region_x, region_y)] = profiles
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
                focus_world_x=focus_world_x,
                focus_world_y=focus_world_y,
                feature_box_world=(
                    feature_left,
                    feature_top,
                    feature_right,
                    feature_bottom,
                ),
                feature_summary=feature_summary,
            )
        )

    player_x = (
        telemetry.player_foot_x
        if telemetry.player_foot_x is not None
        else telemetry.x
    )
    player_y = (
        telemetry.player_foot_y
        if telemetry.player_foot_y is not None
        else telemetry.y
    )
    player_region = (
        floor(player_x / REGION_PIXELS),
        floor(player_y / REGION_PIXELS),
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
    ranked = sorted(eligible, key=lambda item: (item.region_y, item.region_x))

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
    candidate_edges: dict[tuple[int, int], str] = {}
    for edge_name in ("top", "right", "bottom", "left"):
        edge_ranked = sorted(
            (
                item
                for item in ranked
                if edge_name in edge_names(item)
                and opening_profiles[(item.region_x, item.region_y)][edge_name][0]
                >= 0.44
            ),
            key=lambda item: (
                -opening_profiles[(item.region_x, item.region_y)][edge_name][0],
                -item.interest,
                item.region_y,
                item.region_x,
            ),
        )
        for observation in edge_ranked[:1]:
            if len(candidates) >= MAX_VISUAL_HYPOTHESES:
                break
            key = (observation.region_x, observation.region_y)
            candidates.add(key)
            candidate_edges.setdefault(key, edge_name)
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
        edge_hint = candidate_edges.get(key)
        feature_summary = observation.feature_summary
        opening_score = 0.0
        opening_width = 0.0
        passage_box = None
        if edge_hint:
            opening_score, opening_width, passage_box = opening_profiles[key][edge_hint]
            feature_summary = (
                f"localized {opening_width:.0%}-wide dark opening connected to "
                f"the {edge_hint} edge; {feature_summary}"
            )
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
                observation.focus_world_x,
                observation.focus_world_y,
                observation.feature_box_world,
                edge_hint,
                feature_summary,
                opening_score,
                opening_width,
                passage_box,
            )
        )
    return result
