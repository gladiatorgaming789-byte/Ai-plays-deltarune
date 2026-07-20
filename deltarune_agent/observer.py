from dataclasses import dataclass
from typing import Optional

import pyautogui
from PIL import Image


@dataclass(frozen=True)
class Observation:
    frame: Image.Image
    step: int


class ScreenObserver:
    def __init__(self, region: Optional[tuple[int, int, int, int]] = None):
        self.region = region

    def observe(self, step: int) -> Observation:
        return Observation(frame=pyautogui.screenshot(region=self.region), step=step)

    def set_region(self, region: tuple[int, int, int, int]) -> None:
        self.region = region
