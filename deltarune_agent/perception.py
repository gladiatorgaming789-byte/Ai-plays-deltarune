from collections import deque
from dataclasses import asdict, dataclass, replace
from enum import Enum
from math import sqrt
from pathlib import Path

from PIL import Image

from .visual_model import OnlineVisualModel


class GameState(str, Enum):
    DIALOGUE = "dialogue"
    CUTSCENE = "cutscene"
    OVERWORLD = "overworld"
    MENU = "menu"
    BATTLE = "battle"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VisualFeatures:
    dark_ratio: float
    white_ratio: float
    bottom_dark_ratio: float
    bottom_white_ratio: float
    bottom_row_peak: float
    battle_arena_score: float = 0.0
    battle_arena_width_ratio: float = 0.0
    battle_arena_height_ratio: float = 0.0
    battle_interior_dark_ratio: float = 0.0
    learned_confidence: float = 0.0
    learned_distance: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {key: round(value, 5) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class Perception:
    state: GameState
    confidence: float
    features: VisualFeatures
    source: str = "visual"


def looks_like_dialogue_choice(frame: Image.Image) -> bool:
    """Detect repeated option markers inside Deltarune's dialogue panel.

    Some response prompts are drawn by ``obj_writer`` and therefore still emit
    dialogue telemetry. At the game's native 320x240 layout, separate options
    repeat the same small asterisk glyph in a narrow left marker column. This
    checks that visible UI structure without reading option text or a hidden
    selection value.
    """
    image = frame.convert("RGB").resize((320, 240), Image.Resampling.NEAREST)
    pixels = image.load()
    left, right = 24, 42
    top, bottom = 168, 228
    white = {
        (x, y)
        for y in range(top, bottom)
        for x in range(left, right)
        if min(pixels[x, y]) >= 220
    }
    components: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
    while white:
        start = white.pop()
        stack = [start]
        points = [start]
        while stack:
            x, y = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in white:
                        white.remove(neighbor)
                        stack.append(neighbor)
                        points.append(neighbor)
        min_x = min(x for x, _y in points)
        max_x = max(x for x, _y in points)
        min_y = min(y for _x, y in points)
        max_y = max(y for _x, y in points)
        width = max_x - min_x + 1
        height = max_y - min_y + 1
        if 3 <= len(points) <= 40 and 2 <= width <= 9 and 2 <= height <= 9:
            signature = tuple(sorted((x - min_x, y - min_y) for x, y in points))
            components.append((min_x, min_y, signature))
    return any(
        first_signature == second_signature
        and abs(first_x - second_x) <= 2
        and abs(first_y - second_y) >= 12
        for index, (first_x, first_y, first_signature) in enumerate(components)
        for second_x, second_y, second_signature in components[index + 1 :]
    )


class CutsceneTracker:
    """Recognize sustained scripted control without using route coordinates."""

    DIALOGUE_THRESHOLD = 12
    CONTROL_LOCK_THRESHOLD = 12
    TELEMETRY_GAP_GRACE = 300

    def __init__(self) -> None:
        self.dialogue_steps = 0
        self.control_locked_steps = 0
        self.cutscene_active = False
        self.gap_steps = 0
        self.in_dialogue = False
        self.dialogue_started_by_interaction = False
        self.last_action: str | None = None
        self.last_reason = ""

    def note_action(self, action: str, reason: str) -> None:
        self.last_action = action
        self.last_reason = reason

    def update(
        self,
        perception: Perception,
        telemetry,
        visual_valid: bool = True,
    ) -> Perception:
        mode = getattr(telemetry, "mode", None)
        # A present telemetry sender is authoritative about writer state.
        # Visual dialogue remains a fallback only during a telemetry gap.
        dialogue = mode == "dialogue" or (
            telemetry is None and perception.state is GameState.DIALOGUE
        )
        player_controlled = getattr(telemetry, "player_controlled", None)
        control_locked = player_controlled is False and mode == "overworld"
        if control_locked:
            self.control_locked_steps += 1
        elif telemetry is not None:
            self.control_locked_steps = 0
        if dialogue:
            if not self.in_dialogue:
                self.dialogue_started_by_interaction = (
                    self.last_action == "confirm"
                    and "try interaction" in self.last_reason.casefold()
                )
            self.in_dialogue = True
            self.dialogue_steps += 1
            if (
                not self.dialogue_started_by_interaction
                and self.dialogue_steps >= self.DIALOGUE_THRESHOLD
            ):
                self.cutscene_active = True
                self.gap_steps = 0
                return replace(
                    perception,
                    state=GameState.CUTSCENE,
                    confidence=max(0.90, perception.confidence),
                    source="automatic-dialogue-sequence",
                )
            return perception

        self.in_dialogue = False
        self.dialogue_started_by_interaction = False
        if telemetry is not None:
            self.dialogue_steps = 0
        if self.control_locked_steps >= self.CONTROL_LOCK_THRESHOLD:
            self.cutscene_active = True
            self.gap_steps = 0
            return replace(
                perception,
                state=GameState.CUTSCENE,
                confidence=0.96,
                source="telemetry-control",
            )

        if telemetry is None and self.cutscene_active:
            self.gap_steps += 1
            if self.gap_steps <= self.TELEMETRY_GAP_GRACE:
                return replace(
                    perception,
                    state=GameState.CUTSCENE,
                    confidence=0.88 if visual_valid else 0.96,
                    source="cutscene-continuity",
                )

        if mode in {"battle", "choice"} or (
            mode == "overworld" and not control_locked
        ) or (
            telemetry is None and self.gap_steps > self.TELEMETRY_GAP_GRACE
        ):
            self.dialogue_steps = 0
            self.control_locked_steps = 0
            self.cutscene_active = False
            self.gap_steps = 0
        return perception


class VisualStateDetector:
    """Explainable detector based on UI geometry rather than scene colors."""

    TELEMETRY_LABELS = {
        "overworld": GameState.OVERWORLD,
        "battle": GameState.BATTLE,
        "dialogue": GameState.DIALOGUE,
        "choice": GameState.MENU,
    }

    def __init__(self, memory_path: Path | None = None):
        self.model = OnlineVisualModel.load(memory_path)
        self.memory_warning = self.model.load_warning
        self._history: deque[GameState] = deque(maxlen=4)
        self._stable_state = GameState.UNKNOWN
        self._cached_frame: Image.Image | None = None
        self._cached_vector: tuple[float, ...] | None = None

    @staticmethod
    def _line_segments(row: list[bool], minimum: int = 16) -> list[tuple[int, int]]:
        segments: list[tuple[int, int]] = []
        start: int | None = None
        last_white: int | None = None
        gap = 0
        for x, value in enumerate(row + [False, False]):
            if value:
                if start is None:
                    start = x
                last_white = x
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 1:
                    end = last_white if last_white is not None else x - gap
                    if end - start + 1 >= minimum:
                        segments.append((start, end))
                    start = None
                    last_white = None
                    gap = 0
        return segments

    @classmethod
    def _battle_arena_features(
        cls,
        white_mask: list[bool],
        dark_mask: list[bool],
        width: int,
        height: int,
    ) -> tuple[float, float, float, float]:
        rows = [white_mask[y * width : (y + 1) * width] for y in range(height)]
        horizontal: list[tuple[int, int, int]] = []
        for y in range(int(height * 0.18), int(height * 0.99)):
            horizontal.extend((y, left, right) for left, right in cls._line_segments(rows[y]))

        best = (0.0, 0.0, 0.0, 0.0)
        for top, top_left, top_right in horizontal:
            for bottom, bottom_left, bottom_right in horizontal:
                if bottom - top < 8:
                    continue
                left = max(top_left, bottom_left)
                right = min(top_right, bottom_right)
                box_width = right - left + 1
                box_height = bottom - top + 1
                width_ratio = box_width / width
                height_ratio = box_height / height
                if not (0.16 <= width_ratio <= 0.82 and 0.10 <= height_ratio <= 0.66):
                    continue
                overlap = box_width / max(1, min(top_right - top_left + 1, bottom_right - bottom_left + 1))
                if overlap < 0.78:
                    continue

                top_coverage = sum(rows[top][left : right + 1]) / box_width
                bottom_coverage = sum(rows[bottom][left : right + 1]) / box_width
                vertical_count = box_height
                left_coverage = sum(
                    any(rows[y][max(0, left - 1) : min(width, left + 2)])
                    for y in range(top, bottom + 1)
                ) / vertical_count
                right_coverage = sum(
                    any(rows[y][max(0, right - 1) : min(width, right + 2)])
                    for y in range(top, bottom + 1)
                ) / vertical_count
                edge_score = (
                    top_coverage + bottom_coverage + left_coverage + right_coverage
                ) / 4

                interior = [
                    dark_mask[y * width + x]
                    for y in range(top + 2, bottom - 1)
                    for x in range(left + 2, right - 1)
                ]
                if not interior:
                    continue
                interior_dark = sum(interior) / len(interior)
                score = edge_score * (0.55 + 0.45 * interior_dark)
                if score > best[0]:
                    best = (score, width_ratio, height_ratio, interior_dark)
        return best

    @staticmethod
    def _embedding(
        pixels: list[tuple[int, int, int]],
        width: int,
        height: int,
        features: VisualFeatures,
    ) -> tuple[float, ...]:
        gray = [
            (r * 299 + g * 587 + b * 114) / (255 * 1000)
            for r, g, b in pixels
        ]
        vector = [
            features.dark_ratio,
            features.white_ratio,
            features.bottom_dark_ratio,
            features.bottom_white_ratio,
            features.bottom_row_peak,
            features.battle_arena_score,
            features.battle_arena_width_ratio,
            features.battle_arena_height_ratio,
            features.battle_interior_dark_ratio,
        ]
        columns, rows = 4, 3
        for grid_y in range(rows):
            top = grid_y * height // rows
            bottom = (grid_y + 1) * height // rows
            for grid_x in range(columns):
                left = grid_x * width // columns
                right = (grid_x + 1) * width // columns
                values = [
                    gray[y * width + x]
                    for y in range(top, bottom)
                    for x in range(left, right)
                ]
                mean = sum(values) / len(values)
                deviation = sqrt(
                    sum((value - mean) ** 2 for value in values) / len(values)
                )
                edge_count = 0
                edge_total = 0
                for y in range(top, bottom):
                    for x in range(left, right):
                        index = y * width + x
                        if x > left:
                            edge_count += abs(gray[index] - gray[index - 1]) > 0.12
                            edge_total += 1
                        if y > top:
                            edge_count += abs(gray[index] - gray[index - width]) > 0.12
                            edge_total += 1
                vector.extend((mean, deviation, edge_count / max(1, edge_total)))
        return tuple(vector)

    def _smooth_battle_state(
        self, raw_state: GameState, confidence: float
    ) -> tuple[GameState, float, bool]:
        self._history.append(raw_state)
        recent = list(self._history)[-3:]
        battle_votes = recent.count(GameState.BATTLE)
        if raw_state is GameState.BATTLE and battle_votes < 2:
            state = (
                self._stable_state
                if self._stable_state is not GameState.UNKNOWN
                else GameState.UNKNOWN
            )
            return state, min(confidence, 0.60), True
        if self._stable_state is GameState.BATTLE and raw_state is not GameState.BATTLE:
            if list(self._history)[-2:].count(GameState.BATTLE) > 0:
                return GameState.BATTLE, min(confidence, 0.72), True
        self._stable_state = raw_state
        return raw_state, confidence, False

    def classify(self, frame: Image.Image) -> Perception:
        sample = frame.convert("RGB").resize((160, 90))
        pixels = list(sample.getdata())
        width, height = sample.size
        bottom_start = int(height * 0.62)
        bottom_pixels = pixels[bottom_start * width :]

        def dark(pixel: tuple[int, int, int]) -> bool:
            r, g, b = pixel
            return (r * 299 + g * 587 + b * 114) // 1000 < 48

        def white(pixel: tuple[int, int, int]) -> bool:
            r, g, b = pixel
            return min(r, g, b) > 190 and max(r, g, b) - min(r, g, b) < 45

        row_peak = 0.0
        for y in range(bottom_start, height):
            row = pixels[y * width : (y + 1) * width]
            row_peak = max(row_peak, sum(map(white, row)) / width)

        dark_mask = list(map(dark, pixels))
        white_mask = list(map(white, pixels))
        arena_score, arena_width, arena_height, arena_dark = self._battle_arena_features(
            white_mask, dark_mask, width, height
        )

        features = VisualFeatures(
            dark_ratio=sum(dark_mask) / len(pixels),
            white_ratio=sum(white_mask) / len(pixels),
            bottom_dark_ratio=sum(map(dark, bottom_pixels)) / len(bottom_pixels),
            bottom_white_ratio=sum(map(white, bottom_pixels)) / len(bottom_pixels),
            bottom_row_peak=row_peak,
            battle_arena_score=arena_score,
            battle_arena_width_ratio=arena_width,
            battle_arena_height_ratio=arena_height,
            battle_interior_dark_ratio=arena_dark,
        )

        vector = self._embedding(pixels, width, height, features)
        self._cached_frame = frame
        self._cached_vector = vector
        learned = self.model.predict(vector)
        learned_state = None
        learned_confidence = 0.0
        learned_distance = 0.0
        if learned is not None:
            label, learned_confidence, learned_distance = learned
            try:
                learned_state = GameState(label)
            except ValueError:
                learned_state = None
            features = VisualFeatures(
                **{
                    **asdict(features),
                    "learned_confidence": learned_confidence,
                    "learned_distance": learned_distance,
                }
            )

        if (
            features.battle_arena_score > 0.72
            and features.battle_interior_dark_ratio > 0.72
            and features.dark_ratio > 0.52
        ):
            confidence = min(
                0.96,
                0.50
                + features.battle_arena_score * 0.35
                + features.battle_interior_dark_ratio * 0.12,
            )
            state = GameState.BATTLE
            source = "visual-structure"
        elif (
            features.bottom_dark_ratio > 0.62
            and features.bottom_white_ratio > 0.012
            and features.bottom_row_peak > 0.08
        ):
            confidence = min(0.92, 0.52 + features.bottom_dark_ratio * 0.25 + features.bottom_row_peak)
            state = GameState.DIALOGUE
            source = "visual-structure"
        elif features.dark_ratio > 0.64 and features.white_ratio > 0.012:
            confidence = min(0.88, 0.48 + features.dark_ratio * 0.35)
            state = GameState.MENU
            source = "visual-structure"
        elif features.dark_ratio > 0.94 and features.white_ratio < 0.004:
            confidence = 0.8
            state = GameState.UNKNOWN
            source = "visual-structure"
        else:
            confidence = 0.58
            state = GameState.OVERWORLD
            source = "visual-structure"

        if learned_state is not None and learned_confidence >= 0.74:
            should_use_learned = state in {GameState.OVERWORLD, GameState.UNKNOWN}
            if state is GameState.BATTLE and learned_state is GameState.OVERWORLD:
                should_use_learned = features.battle_arena_score < 0.88
            if learned_state is GameState.BATTLE:
                should_use_learned = True
            if should_use_learned:
                state = learned_state
                confidence = learned_confidence
                source = "visual-learned"

        state, confidence, smoothed = self._smooth_battle_state(state, confidence)
        if smoothed:
            source = "visual-temporal"

        return Perception(state, round(confidence, 3), features, source)

    def learn_from_telemetry(self, frame: Image.Image, mode: str) -> None:
        state = self.TELEMETRY_LABELS.get(mode)
        if state is None:
            return
        if frame is not self._cached_frame or self._cached_vector is None:
            self.classify(frame)
        if self._cached_vector is not None:
            self.model.update(state.value, self._cached_vector)

    def save_memory(self) -> None:
        self.model.save()
