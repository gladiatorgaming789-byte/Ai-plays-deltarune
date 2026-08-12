from __future__ import annotations

from deltarune_agent.speed import SpeedSynchronizer, parse_requested_speed
from deltarune_agent.telemetry import SpeedSample


def _sample(multiplier: float, received_at: float = 10.0) -> SpeedSample:
    return SpeedSample(
        multiplier=multiplier,
        base_fps=30.0,
        target_fps=30.0 * multiplier,
        received_at=received_at,
    )


def test_auto_speed_uses_fresh_packet_and_scales_delays():
    speed = SpeedSynchronizer("auto", minimum_delay=0.008)
    speed.update(_sample(2.0))

    assert speed.effective_multiplier(now=10.2) == 2.0
    assert speed.source(now=10.2) == "telemetry"
    assert speed.synchronized(now=10.2) is True
    assert speed.scale_delay(0.10, now=10.2) == 0.05


def test_auto_speed_falls_back_to_one_when_packet_is_stale():
    speed = SpeedSynchronizer("auto", stale_after=2.0)
    speed.update(_sample(10.0))

    assert speed.effective_multiplier(now=12.1) == 1.0
    assert speed.source(now=12.1) == "safe_fallback"
    assert speed.stale_warning(now=12.1) is not None
    speed.update(_sample(10.0))
    assert speed.stale_warning(now=12.2) is None


def test_manual_speed_works_without_telemetry():
    speed = SpeedSynchronizer("3")

    assert speed.effective_multiplier(now=100.0) == 3.0
    assert speed.source(now=100.0) == "manual"
    assert speed.scale_delay(0.18, now=100.0) == 0.06
    assert speed.stale_warning(now=100.0) is None


def test_registration_floor_keeps_short_inputs_detectable():
    speed = SpeedSynchronizer("10", minimum_delay=0.008)

    assert speed.scale_delay(0.01) == 0.008
    assert speed.scale_delay(0.0) == 0.0


def test_requested_speed_accepts_gui_labels_and_rejects_unsafe_values():
    assert parse_requested_speed("2x") == "2"
    assert parse_requested_speed("10x") == "10"
    assert parse_requested_speed("AUTO") == "auto"

    for value in ("0", "11", "fast"):
        try:
            parse_requested_speed(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value!r} should have been rejected")
