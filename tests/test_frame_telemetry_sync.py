from __future__ import annotations

from types import SimpleNamespace

from deltarune_agent.frame_telemetry_sync import (
    MAX_PREVIOUS_SAMPLE_AGE_SECONDS,
    MAX_SYNC_OFFSET_SECONDS,
    _same_identity,
    _transition_trace_requires_current,
)


def _sample(room: str, *, agent: str = "ai-1"):
    return SimpleNamespace(room_name=room, room_id=1, agent_id=agent)


def test_agent_identity_mismatch_can_never_reuse_previous_sample() -> None:
    assert _same_identity(_sample("room_a", agent="a"), _sample("room_a", agent="b")) is False


def test_same_agent_can_be_temporally_compared() -> None:
    assert _same_identity(_sample("room_a", agent="a"), _sample("room_a", agent="a")) is True


def test_room_transition_forces_current_sample() -> None:
    receiver = SimpleNamespace(
        overworld_trace=[_sample("room_a"), _sample("room_b")]
    )
    assert _transition_trace_requires_current(
        receiver,
        _sample("room_a"),
        _sample("room_b"),
    ) is True


def test_same_room_trace_can_use_nearer_previous_sample() -> None:
    receiver = SimpleNamespace(overworld_trace=[_sample("room_a"), _sample("room_a")])
    assert _transition_trace_requires_current(
        receiver,
        _sample("room_a"),
        _sample("room_a"),
    ) is False


def test_sync_windows_are_intentionally_tighter_than_telemetry_freshness() -> None:
    assert 0 < MAX_SYNC_OFFSET_SECONDS < MAX_PREVIOUS_SAMPLE_AGE_SECONDS <= 0.25
