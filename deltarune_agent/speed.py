from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from .telemetry import SpeedSample


MIN_MULTIPLIER = 1
MAX_MULTIPLIER = 10
AUTO_STALE_SECONDS = 2.0
REGISTRATION_SAFE_FLOOR = 0.008


def parse_requested_speed(value: object) -> str:
    requested = str(value or "auto").strip().casefold().removesuffix("x")
    if requested == "auto":
        return requested
    try:
        multiplier = int(requested)
    except ValueError as exc:
        raise ValueError(
            "speed must be auto or a whole multiplier from 1 to 10"
        ) from exc
    if multiplier < MIN_MULTIPLIER or multiplier > MAX_MULTIPLIER:
        raise ValueError("speed must be auto or a whole multiplier from 1 to 10")
    return str(multiplier)


@dataclass
class SpeedSynchronizer:
    """Resolve automatic or manual speed and expose auditable timing details."""

    requested: str = "auto"
    stale_after: float = AUTO_STALE_SECONDS
    minimum_delay: float = REGISTRATION_SAFE_FLOOR

    def __post_init__(self) -> None:
        self.requested = parse_requested_speed(self.requested)
        if self.stale_after <= 0:
            raise ValueError("stale_after must be positive")
        if self.minimum_delay < 0:
            raise ValueError("minimum_delay cannot be negative")
        self.sample: SpeedSample | None = None
        self.warned_stale = False

    @property
    def automatic(self) -> bool:
        return self.requested == "auto"

    def update(self, sample: SpeedSample | None) -> None:
        if sample is not None and (
            self.sample is None or sample.received_at > self.sample.received_at
        ):
            self.sample = sample
            self.warned_stale = False

    def packet_age(self, now: float | None = None) -> float | None:
        if self.sample is None:
            return None
        return max(
            0.0,
            (time.monotonic() if now is None else now) - self.sample.received_at,
        )

    def detected_multiplier(self, now: float | None = None) -> float | None:
        age = self.packet_age(now)
        if self.sample is None or age is None or age > self.stale_after:
            return None
        return self.sample.multiplier

    def effective_multiplier(self, now: float | None = None) -> float:
        if not self.automatic:
            return float(self.requested)
        return self.detected_multiplier(now) or 1.0

    def source(self, now: float | None = None) -> str:
        if not self.automatic:
            return "manual"
        return (
            "telemetry"
            if self.detected_multiplier(now) is not None
            else "safe_fallback"
        )

    def synchronized(self, now: float | None = None) -> bool:
        detected = self.detected_multiplier(now)
        return (
            detected is not None
            and abs(detected - self.effective_multiplier(now)) < 0.001
        )

    def stale_warning(self, now: float | None = None) -> str | None:
        if not self.automatic or self.detected_multiplier(now) is not None:
            self.warned_stale = False
            return None
        if self.warned_stale:
            return None
        self.warned_stale = True
        return (
            "Speed telemetry is unavailable or stale; the AI is using safe 1x "
            "timing until a fresh DRSPEED packet arrives."
        )

    def scale_delay(self, seconds: float, now: float | None = None) -> float:
        seconds = max(0.0, float(seconds))
        if seconds == 0:
            return 0.0
        return max(self.minimum_delay, seconds / self.effective_multiplier(now))

    def as_dict(
        self,
        *,
        now: float | None = None,
        action_duration: float | None = None,
        cooldown: float | None = None,
        loop_seconds: float | None = None,
    ) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        detected = self.detected_multiplier(now)
        effective = self.effective_multiplier(now)
        data: dict[str, Any] = {
            "requested": self.requested,
            "detected_multiplier": detected,
            "effective_multiplier": effective,
            "source": self.source(now),
            "packet_age_seconds": self.packet_age(now),
            "stale_after_seconds": self.stale_after,
            "synchronized": self.synchronized(now),
            "minimum_delay_seconds": self.minimum_delay,
        }
        if self.sample is not None:
            data.update(
                {
                    "game_multiplier": self.sample.multiplier,
                    "base_fps": self.sample.base_fps,
                    "target_fps": self.sample.target_fps,
                }
            )
        if action_duration is not None:
            data["effective_action_duration_seconds"] = self.scale_delay(
                action_duration, now
            )
        if cooldown is not None:
            data["effective_cooldown_seconds"] = self.scale_delay(cooldown, now)
        if loop_seconds is not None:
            data["loop_seconds"] = max(0.0, float(loop_seconds))
        return data
