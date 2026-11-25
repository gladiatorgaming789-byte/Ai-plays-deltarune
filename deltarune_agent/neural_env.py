"""Fixed numerical observations for the neural Deltarune agent.

This module is deliberately independent of the gameplay policy. It converts
already-observed telemetry into a deterministic, fixed-width numeric vector.
It does not read game assets, room maps, story text, interactable identities,
or screenshots.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Sequence

from .actions import ACTIONS
from .telemetry import TelemetrySample


FACING_ORDER = ("down", "left", "right", "up", "unknown")
MODE_ORDER = ("overworld", "dialogue", "cutscene", "menu", "battle", "unknown")
ACTION_ORDER = tuple(ACTIONS.keys())

POSITION_SCALE = 4096.0
VELOCITY_SCALE = 32.0
SPEED_SCALE = 32.0
ROOM_SIZE_SCALE = 4096.0
CAMERA_SCALE = 4096.0
TIME_SCALE_MS = 100_000.0
INTERVAL_SCALE_MS = 1000.0
ACTION_DURATION_SCALE = 1.0


@dataclass(frozen=True)
class FeatureSpec:
    """One named element of the fixed neural observation vector."""

    name: str


@dataclass(frozen=True)
class NeuralObservation:
    """One policy-ready numerical observation.

    values contains only finite floats. feature_names has the same length,
    making saved samples self-describing. available is a parallel 0/1 mask:
    optional telemetry fields are represented by zero plus a mask rather than
    NaN or an arbitrary sentinel.
    """

    values: tuple[float, ...]
    available: tuple[float, ...]
    feature_names: tuple[str, ...]
    source_sequence: int | None
    room_id: int
    room_name: str

    @property
    def width(self) -> int:
        return len(self.values)

    def combined(self) -> tuple[float, ...]:
        """Return values followed by their availability mask."""
        return self.values + self.available

    def as_dict(self) -> dict[str, object]:
        return {
            "values": list(self.values),
            "available": list(self.available),
            "feature_names": list(self.feature_names),
            "source_sequence": self.source_sequence,
            "room_id": self.room_id,
            "room_name": self.room_name,
        }


class NeuralObservationEncoder:
    """Encode TelemetrySample into a fixed numerical feature vector.

    Vision is intentionally absent. This encoder only consumes telemetry
    fields that the gameplay agent is already allowed to observe.
    """

    BASE_FEATURES: tuple[FeatureSpec, ...] = tuple(
        FeatureSpec(name)
        for name in (
            "position.x",
            "position.y",
            "position.foot_x",
            "position.foot_y",
            "velocity.x",
            "velocity.y",
            "motion.speed",
            "motion.direction_sin",
            "motion.direction_cos",
            "room.width",
            "room.height",
            "camera.x",
            "camera.y",
            "camera.width",
            "camera.height",
            "bbox.left",
            "bbox.top",
            "bbox.right",
            "bbox.bottom",
            "sprite.width",
            "sprite.height",
            "sprite.xscale",
            "sprite.yscale",
            "timing.room_speed",
            "timing.fps",
            "timing.game_time",
            "timing.sample_interval",
            "delta.x",
            "delta.y",
            "identity.room_id",
            "identity.room_bucket",
            "state.interaction",
            "state.player_controlled",
            "state.visible",
            "state.facing.down",
            "state.facing.left",
            "state.facing.right",
            "state.facing.up",
            "state.facing.unknown",
            "state.mode.overworld",
            "state.mode.dialogue",
            "state.mode.cutscene",
            "state.mode.menu",
            "state.mode.battle",
            "state.mode.unknown",
            "action.previous.wait",
            "action.previous.confirm",
            "action.previous.cancel",
            "action.previous.menu",
            "action.previous.up",
            "action.previous.down",
            "action.previous.left",
            "action.previous.right",
            "action.previous_duration",
        )
    )

    def __init__(self) -> None:
        self.feature_names = tuple(feature.name for feature in self.BASE_FEATURES)

    @staticmethod
    def _clip(value: float, limit: float) -> float:
        if not math.isfinite(value):
            return 0.0
        return max(-limit, min(limit, value))

    @classmethod
    def _norm_signed(cls, value: float | None, scale: float) -> tuple[float, float]:
        if value is None or not math.isfinite(value):
            return 0.0, 0.0
        return cls._clip(value / scale, 1.0), 1.0

    @classmethod
    def _norm_positive(cls, value: float | None, scale: float) -> tuple[float, float]:
        if value is None or not math.isfinite(value):
            return 0.0, 0.0
        return max(0.0, min(1.0, value / scale)), 1.0

    @staticmethod
    def _room_bucket(room_name: str, room_id: int) -> float:
        key = f"{room_id}:{room_name}".encode("utf-8", "replace")
        digest = hashlib.blake2b(key, digest_size=2).digest()
        return digest[0] / 255.0

    @staticmethod
    def _angle_components(direction: float | None) -> tuple[float, float, float]:
        if direction is None or not math.isfinite(direction):
            return 0.0, 0.0, 0.0
        radians = math.radians(direction)
        return math.sin(radians), math.cos(radians), 1.0

    @staticmethod
    def _one_hot(value: str | None, order: Sequence[str]) -> tuple[float, ...]:
        normalized = (value or "unknown").casefold()
        return tuple(1.0 if normalized == item else 0.0 for item in order)

    def encode(
        self,
        sample: TelemetrySample,
        *,
        previous_action: str | None = None,
        previous_action_duration: float | None = None,
    ) -> NeuralObservation:
        values: list[float] = []
        available: list[float] = []

        def add(value: float | None, mask: float = 1.0) -> None:
            if value is None or not math.isfinite(value):
                values.append(0.0)
                available.append(0.0)
            else:
                values.append(float(value))
                available.append(mask)

        def add_norm_signed(value: float | None, scale: float) -> None:
            normalized, mask = self._norm_signed(value, scale)
            add(normalized, mask)

        def add_norm_positive(value: float | None, scale: float) -> None:
            normalized, mask = self._norm_positive(value, scale)
            add(normalized, mask)

        add_norm_signed(sample.player_x if sample.player_x is not None else sample.x, POSITION_SCALE)
        add_norm_signed(sample.player_y if sample.player_y is not None else sample.y, POSITION_SCALE)
        add_norm_signed(sample.player_foot_x, POSITION_SCALE)
        add_norm_signed(sample.player_foot_y, POSITION_SCALE)
        add_norm_signed(sample.hspeed, VELOCITY_SCALE)
        add_norm_signed(sample.vspeed, VELOCITY_SCALE)
        add_norm_positive(sample.speed, SPEED_SCALE)

        angle_sin, angle_cos, angle_mask = self._angle_components(sample.direction)
        add(angle_sin, angle_mask)
        add(angle_cos, angle_mask)

        add_norm_positive(sample.room_width, ROOM_SIZE_SCALE)
        add_norm_positive(sample.room_height, ROOM_SIZE_SCALE)
        add_norm_signed(sample.camera_x, CAMERA_SCALE)
        add_norm_signed(sample.camera_y, CAMERA_SCALE)
        add_norm_positive(sample.camera_width, CAMERA_SCALE)
        add_norm_positive(sample.camera_height, CAMERA_SCALE)

        add_norm_signed(sample.player_bbox_left, POSITION_SCALE)
        add_norm_signed(sample.player_bbox_top, POSITION_SCALE)
        add_norm_signed(sample.player_bbox_right, POSITION_SCALE)
        add_norm_signed(sample.player_bbox_bottom, POSITION_SCALE)

        add_norm_positive(sample.sprite_width, POSITION_SCALE)
        add_norm_positive(sample.sprite_height, POSITION_SCALE)
        add_norm_signed(sample.image_xscale, 4.0)
        add_norm_signed(sample.image_yscale, 4.0)
        add_norm_positive(sample.room_speed, 120.0)
        add_norm_positive(sample.fps, 240.0)
        add_norm_positive(sample.game_time_ms, TIME_SCALE_MS)
        add_norm_positive(sample.sample_interval_ms, INTERVAL_SCALE_MS)
        add_norm_signed(sample.sample_delta_x, POSITION_SCALE)
        add_norm_signed(sample.sample_delta_y, POSITION_SCALE)

        add(self._clip(sample.room_id / 1000.0, 1.0), 1.0)
        add(self._room_bucket(sample.room_name, sample.room_id), 1.0)

        interaction = sample.interaction_state
        if interaction is None:
            add(0.0, 0.0)
        else:
            add(self._clip(float(interaction) / 4.0, 1.0), 1.0)
        add(
            None if sample.player_controlled is None else float(sample.player_controlled),
            1.0 if sample.player_controlled is not None else 0.0,
        )
        add(
            None if sample.visible is None else float(sample.visible),
            1.0 if sample.visible is not None else 0.0,
        )

        for value in self._one_hot(sample.player_facing_direction, FACING_ORDER):
            add(value, 1.0)

        for value in self._one_hot(sample.mode, MODE_ORDER):
            add(value, 1.0)

        action_name = previous_action if previous_action in ACTION_ORDER else "wait"
        for candidate in ACTION_ORDER:
            add(1.0 if action_name == candidate else 0.0, 1.0)
        add_norm_positive(previous_action_duration, ACTION_DURATION_SCALE)

        if len(values) != len(self.feature_names):
            raise AssertionError(
                f"neural feature width mismatch: {len(values)} != {len(self.feature_names)}"
            )

        return NeuralObservation(
            values=tuple(values),
            available=tuple(available),
            feature_names=self.feature_names,
            source_sequence=sample.packet_sequence,
            room_id=sample.room_id,
            room_name=sample.room_name,
        )

    @property
    def width(self) -> int:
        return len(self.feature_names)

    @property
    def combined_width(self) -> int:
        return self.width * 2


DEFAULT_ENCODER = NeuralObservationEncoder()
