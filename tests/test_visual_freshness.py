import time

from PIL import Image

from deltarune_agent.observer import Observation
from deltarune_agent.telemetry import TelemetrySample
from deltarune_agent.visual_freshness import VisualFreshnessGuard


def sample(room: str, x: float, y: float) -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name=room,
        x=x,
        y=y,
        object_name="obj_mainchara",
        received_at=time.monotonic(),
        player_x=x,
        player_y=y,
    )


def test_identical_frame_is_allowed_while_player_is_still():
    guard = VisualFreshnessGuard()
    frame = Image.new("RGB", (320, 240), (20, 40, 60))

    first = guard.validate(Observation(frame, 0), sample("room_a", 10, 10))
    second = guard.validate(Observation(frame.copy(), 1), sample("room_a", 10, 10))

    assert first.visual_valid
    assert second.visual_valid


def test_identical_frame_is_rejected_after_telemetry_moves():
    guard = VisualFreshnessGuard()
    frame = Image.new("RGB", (320, 240), (20, 40, 60))

    guard.validate(Observation(frame, 0), sample("room_a", 10, 10))
    frozen = guard.validate(Observation(frame.copy(), 1), sample("room_a", 30, 10))

    assert not frozen.visual_valid
    assert guard.frozen_frames == 1


def test_identical_frame_is_rejected_after_room_change():
    guard = VisualFreshnessGuard()
    frame = Image.new("RGB", (320, 240), (20, 40, 60))

    guard.validate(Observation(frame, 0), sample("room_a", 10, 10))
    frozen = guard.validate(Observation(frame.copy(), 1), sample("room_b", 10, 10))

    assert not frozen.visual_valid
