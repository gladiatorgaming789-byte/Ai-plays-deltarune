from __future__ import annotations

from dataclasses import replace
from hashlib import blake2b

from PIL import Image

from .observer import Observation
from .telemetry import TelemetrySample


class VisualFreshnessGuard:
    """Reject a frozen capture when live telemetry proves the scene moved.

    Windows ``PrintWindow`` can keep returning one old, non-blank bitmap while
    Deltarune continues running. A blank-frame check cannot catch that failure.
    This guard compares a compact frame fingerprint with authoritative room and
    player motion. Exact visual repetition is allowed while the game is still,
    but the same bitmap cannot remain valid after a room change or meaningful
    player displacement.
    """

    POSITION_TOLERANCE = 4.0

    def __init__(self) -> None:
        self._fingerprint: bytes | None = None
        self._stale_fingerprint: bytes | None = None
        self._room: str | None = None
        self._position: tuple[float, float] | None = None
        self.frozen_frames = 0

    @staticmethod
    def _frame_fingerprint(frame: Image.Image) -> bytes:
        sample = frame.convert("RGB").resize(
            (64, 48),
            Image.Resampling.NEAREST,
        )
        return blake2b(sample.tobytes(), digest_size=16).digest()

    @staticmethod
    def _telemetry_context(
        telemetry: TelemetrySample | None,
    ) -> tuple[str | None, tuple[float, float] | None]:
        if telemetry is None:
            return None, None
        room = telemetry.room_name or str(telemetry.room_id)
        x = (
            telemetry.player_x
            if telemetry.player_x is not None
            else telemetry.x
        )
        y = (
            telemetry.player_y
            if telemetry.player_y is not None
            else telemetry.y
        )
        return room, (float(x), float(y))

    def validate(
        self,
        observation: Observation,
        telemetry: TelemetrySample | None,
    ) -> Observation:
        if not observation.visual_valid:
            return observation

        fingerprint = self._frame_fingerprint(observation.frame)
        room, position = self._telemetry_context(telemetry)
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._stale_fingerprint = None
            self._room = room
            self._position = position
            return observation

        if fingerprint == self._stale_fingerprint:
            # Once telemetry proves a bitmap is frozen, do not trust the same
            # bitmap during a later packet gap.  Only a genuinely new frame
            # can clear the stale-capture state.
            self.frozen_frames += 1
            return replace(observation, visual_valid=False)

        # Capture can start a few frames before the first telemetry packet.
        # Bind that first authoritative context to the existing bitmap so a
        # later movement can still prove that the capture froze.
        if self._room is None and room is not None:
            self._room = room
        if self._position is None and position is not None:
            self._position = position

        room_changed = (
            room is not None
            and self._room is not None
            and room != self._room
        )
        moved = False
        if position is not None and self._position is not None:
            moved = max(
                abs(position[0] - self._position[0]),
                abs(position[1] - self._position[1]),
            ) > self.POSITION_TOLERANCE

        if room_changed or moved:
            self._stale_fingerprint = fingerprint
            self.frozen_frames += 1
            return replace(observation, visual_valid=False)
        return observation
