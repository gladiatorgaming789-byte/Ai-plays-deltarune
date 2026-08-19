"""Connected-component SOUL localization for Battle System v2.

Battle v2 originally aggregated every same-colored pixel in the playfield. A
same-colored HUD decoration could therefore make a real compact SOUL look too
wide/tall and disappear. This precision layer scores compact connected colored
components independently and patches the controller before it is instantiated.
"""

from __future__ import annotations

from collections import deque

from PIL import Image

from .battle_v2 import SOUL_COLORS, BattleV2Controller, SoulObservation


SOUL_COMPONENT_VERSION = 1
_INSTALLED = False


def observe_soul_components(frame: Image.Image) -> SoulObservation | None:
    image = BattleV2Controller._image(frame)
    pixels = image.load()
    candidates: list[SoulObservation] = []

    for mode, predicate in SOUL_COLORS.items():
        matching = {
            (x, y)
            for y in range(25, 210)
            for x in range(20, 300)
            if predicate(*pixels[x, y])
        }
        while matching:
            start = matching.pop()
            pending = deque((start,))
            points = [start]
            while pending:
                x, y = pending.popleft()
                for dx, dy in (
                    (-1, -1), (0, -1), (1, -1),
                    (-1, 0),            (1, 0),
                    (-1, 1),  (0, 1),   (1, 1),
                ):
                    neighbor = (x + dx, y + dy)
                    if neighbor in matching:
                        matching.remove(neighbor)
                        pending.append(neighbor)
                        points.append(neighbor)

            if not 4 <= len(points) <= 260:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            width = max(xs) - min(xs) + 1
            height = max(ys) - min(ys) + 1
            if not (3 <= width <= 38 and 3 <= height <= 38):
                continue
            compactness = len(points) / max(1, width * height)
            if compactness < 0.12:
                continue
            candidates.append(
                SoulObservation(
                    mode=mode,
                    x=sum(xs) / len(xs),
                    y=sum(ys) / len(ys),
                    pixels=len(points),
                )
            )

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda soul: (
            # The SOUL is normally in/near the active battle area, but center
            # distance is only a ranking signal—not a hardcoded encounter box.
            abs(soul.x - 160) + abs(soul.y - 125),
            abs(soul.pixels - 30),
        ),
    )


def install_battle_v2_components() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    BattleV2Controller.observe_soul = staticmethod(observe_soul_components)  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "SOUL_COMPONENT_VERSION",
    "install_battle_v2_components",
    "observe_soul_components",
]
