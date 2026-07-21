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


def test_proven_stale_frame_stays_rejected_during_telemetry_gap():
    guard = VisualFreshnessGuard()
    frame = Image.new("RGB", (320, 240), (20, 40, 60))

    guard.validate(Observation(frame, 0), sample("room_a", 10, 10))
    moved = guard.validate(Observation(frame.copy(), 1), sample("room_a", 30, 10))
    during_gap = guard.validate(Observation(frame.copy(), 2), None)

    assert not moved.visual_valid
    assert not during_gap.visual_valid


def test_new_frame_clears_stale_capture_latch():
    guard = VisualFreshnessGuard()
    stale = Image.new("RGB", (320, 240), (20, 40, 60))
    fresh = Image.new("RGB", (320, 240), (21, 40, 60))

    guard.validate(Observation(stale, 0), sample("room_a", 10, 10))
    guard.validate(Observation(stale.copy(), 1), sample("room_a", 30, 10))
    recovered = guard.validate(Observation(fresh, 2), None)

    assert recovered.visual_valid


def test_first_telemetry_packet_binds_existing_frame_for_future_checks():
    guard = VisualFreshnessGuard()
    frame = Image.new("RGB", (320, 240), (20, 40, 60))

    before_telemetry = guard.validate(Observation(frame, 0), None)
    first_packet = guard.validate(
        Observation(frame.copy(), 1),
        sample("room_a", 10, 10),
    )
    frozen = guard.validate(
        Observation(frame.copy(), 2),
        sample("room_a", 30, 10),
    )

    assert before_telemetry.visual_valid
    assert first_packet.visual_valid
    assert not frozen.visual_valid


def test_new_gap_frame_binds_when_telemetry_resumes_then_detects_freeze():
    guard = VisualFreshnessGuard()
    first = Image.new("RGB", (320, 240), (20, 40, 60))
    during_gap = Image.new("RGB", (320, 240), (21, 40, 60))

    guard.validate(Observation(first, 0), sample("room_a", 10, 10))
    gap_frame = guard.validate(Observation(during_gap, 1), None)
    resumed = guard.validate(
        Observation(during_gap.copy(), 2),
        sample("room_a", 30, 10),
    )
    frozen = guard.validate(
        Observation(during_gap.copy(), 3),
        sample("room_a", 50, 10),
    )

    assert gap_frame.visual_valid
    assert resumed.visual_valid
    assert not frozen.visual_valid
