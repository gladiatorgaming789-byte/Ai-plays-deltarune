from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from PIL import Image, ImageOps


@dataclass(frozen=True)
class DialogueObservation:
    signature: str
    option_rows: tuple[int, ...]
    text: str | None = None

    @property
    def option_count(self) -> int:
        return len(self.option_rows)


class DialogueReader:
    """Extract stable visible dialogue evidence without requiring hidden game data.

    OCR is optional. When pytesseract is unavailable, the signature and detected
    option rows still let the policy recognize repeated panels and enumerate
    their visible selections deterministically.
    """

    def __init__(self, enable_ocr: bool = True):
        self.enable_ocr = enable_ocr

    @staticmethod
    def _panel(frame: Image.Image) -> Image.Image:
        image = frame.convert("RGB").resize((320, 240), Image.Resampling.NEAREST)
        return image.crop((14, 156, 306, 235))

    @staticmethod
    def _row_activity(panel: Image.Image) -> list[float]:
        gray = ImageOps.grayscale(panel)
        values: list[float] = []
        for y in range(gray.height):
            bright = sum(gray.getpixel((x, y)) >= 200 for x in range(gray.width))
            values.append(bright / max(1, gray.width))
        return values

    @classmethod
    def _option_rows(cls, panel: Image.Image) -> tuple[int, ...]:
        activity = cls._row_activity(panel)
        rows: list[int] = []
        active_start: int | None = None
        for index, value in enumerate(activity + [0.0]):
            if value >= 0.015 and active_start is None:
                active_start = index
            elif value < 0.015 and active_start is not None:
                end = index - 1
                center = (active_start + end) // 2
                if end - active_start >= 2:
                    rows.append(center)
                active_start = None
        clustered: list[int] = []
        for row in rows:
            if not clustered or row - clustered[-1] >= 8:
                clustered.append(row)
            else:
                clustered[-1] = (clustered[-1] + row) // 2
        return tuple(clustered[:8])

    def _ocr(self, panel: Image.Image) -> str | None:
        if not self.enable_ocr:
            return None
        try:
            import pytesseract  # type: ignore
        except ImportError:
            return None
        prepared = ImageOps.autocontrast(ImageOps.grayscale(panel)).resize(
            (panel.width * 3, panel.height * 3),
            Image.Resampling.NEAREST,
        )
        try:
            text = pytesseract.image_to_string(prepared, config="--psm 6")
        except Exception:
            return None
        normalized = " ".join(text.split())
        return normalized or None

    def analyze(self, frame: Image.Image) -> DialogueObservation:
        panel = self._panel(frame)
        compact = ImageOps.grayscale(panel).resize((73, 20), Image.Resampling.BILINEAR)
        quantized = bytes(int(value) // 32 for value in compact.getdata())
        signature = sha256(quantized).hexdigest()[:24]
        return DialogueObservation(
            signature=signature,
            option_rows=self._option_rows(panel),
            text=self._ocr(panel),
        )


def selection_pattern(option_count: int, option_index: int) -> tuple[str, ...]:
    if option_count < 1:
        return ("confirm",)
    bounded = max(0, min(option_index, option_count - 1))
    return (*(("up",) * option_count), *(("down",) * bounded), "confirm")


def next_untested_option(option_count: int, attempted: Iterable[int]) -> int:
    tried = set(attempted)
    for index in range(max(1, option_count)):
        if index not in tried:
            return index
    return 0
