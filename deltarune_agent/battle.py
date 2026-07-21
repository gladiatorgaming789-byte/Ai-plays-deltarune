from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from PIL import Image, ImageOps

from .actions import ACTIONS, Action


@dataclass(frozen=True)
class Threat:
    x: float
    y: float
    radius: float


class BattleController:
    """Short-horizon safety controller for the Deltarune soul arena."""

    def __init__(self) -> None:
        self.previous_threats: tuple[Threat, ...] = ()
        self.last_action = "wait"
        self.reason = "battle controller starting"

    @staticmethod
    def _arena(
        frame: Image.Image,
    ) -> tuple[Image.Image, tuple[int, int]]:
        image = frame.convert("RGB").resize(
            (320, 240),
            Image.Resampling.NEAREST,
        )
        return image.crop((30, 30, 290, 205)), (30, 30)

    @staticmethod
    def _components(mask: Image.Image) -> list[Threat]:
        pixels = mask.load()
        width, height = mask.size
        remaining = {
            (x, y)
            for y in range(height)
            for x in range(width)
            if pixels[x, y] > 0
        }
        threats: list[Threat] = []
        while remaining:
            start = remaining.pop()
            stack = [start]
            points = [start]
            while stack:
                x, y = stack.pop()
                for dx, dy in (
                    (1, 0),
                    (-1, 0),
                    (0, 1),
                    (0, -1),
                ):
                    neighbor = (x + dx, y + dy)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
                        points.append(neighbor)
            if 2 <= len(points) <= 400:
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                threats.append(
                    Threat(
                        sum(xs) / len(xs),
                        sum(ys) / len(ys),
                        max(
                            max(xs) - min(xs),
                            max(ys) - min(ys),
                            2,
                        )
                        / 2,
                    )
                )
        return threats

    def detect_threats(
        self,
        frame: Image.Image,
    ) -> tuple[Threat, ...]:
        arena, (offset_x, offset_y) = self._arena(frame)
        gray = ImageOps.grayscale(arena)
        mask = gray.point(
            lambda value: 255 if value >= 190 else 0
        )
        # Connected components are measured in the cropped arena. Convert them
        # back into the same 320x240 coordinate space used by soul telemetry
        # before comparing distances.
        threats = tuple(
            Threat(
                threat.x + offset_x,
                threat.y + offset_y,
                threat.radius,
            )
            for threat in self._components(mask)
        )
        self.previous_threats = threats
        return threats

    def choose(
        self,
        frame: Image.Image,
        soul_position: tuple[float, float] | None,
        *,
        visual_valid: bool = True,
    ) -> Action:
        if not visual_valid:
            self.reason = (
                "battle capture is stale; hold position instead of "
                "dodging phantom projectiles"
            )
            self.last_action = "wait"
            return ACTIONS["wait"]

        threats = self.detect_threats(frame)
        if soul_position is None:
            self.reason = (
                "battle telemetry unavailable; use conservative "
                "movement cycle"
            )
            cycle = ["left", "up", "right", "down"]
            name = (
                cycle[(cycle.index(self.last_action) + 1) % len(cycle)]
                if self.last_action in cycle
                else "left"
            )
            self.last_action = name
            return ACTIONS[name]

        soul_x, soul_y = soul_position
        candidates = {
            "wait": (soul_x, soul_y),
            "left": (soul_x - 8, soul_y),
            "right": (soul_x + 8, soul_y),
            "up": (soul_x, soul_y - 8),
            "down": (soul_x, soul_y + 8),
        }

        def score(
            item: tuple[str, tuple[float, float]],
        ) -> tuple[float, float]:
            name, (x, y) = item
            nearest = min(
                (
                    hypot(x - threat.x, y - threat.y)
                    - threat.radius
                    for threat in threats
                ),
                default=999.0,
            )
            wall_margin = min(
                x - 30,
                290 - x,
                y - 30,
                205 - y,
            )
            repeat_penalty = (
                2.0 if name == self.last_action else 0.0
            )
            return (
                nearest
                + min(wall_margin, 20) * 0.25
                - repeat_penalty,
                wall_margin,
            )

        name, _position = max(
            candidates.items(),
            key=score,
        )
        self.last_action = name
        self.reason = (
            "move toward safest local battle position; detected "
            f"{len(threats)} threats"
        )
        return ACTIONS[name]
