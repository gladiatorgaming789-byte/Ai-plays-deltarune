from pathlib import Path
from tempfile import TemporaryDirectory

from deltarune_agent.evaluation import calculate_metrics, load_events


def test_calculate_metrics_tracks_rooms_cells_and_unknown_states():
    events = [
        {
            "step": 0,
            "state": "overworld",
            "confidence": 0.9,
            "action": "right",
            "reason": "explore frontier",
            "telemetry": {"room_name": "room_a", "x": 8, "y": 8},
        },
        {
            "step": 1,
            "state": "unknown",
            "confidence": 0.2,
            "action": "right",
            "reason": "wait",
            "telemetry": {"room_name": "room_b", "x": 16, "y": 8},
        },
    ]

    metrics = calculate_metrics(events)

    assert metrics.steps == 2
    assert metrics.rooms_seen == 2
    assert metrics.unique_cells == 2
    assert metrics.room_transitions == 1
    assert metrics.unknown_steps == 1
    assert metrics.low_confidence_steps == 1
    assert metrics.telemetry_coverage == 1.0


def test_load_events_rejects_invalid_json():
    with TemporaryDirectory() as directory:
        run = Path(directory)
        (run / "events.jsonl").write_text("{bad json}\n", encoding="utf-8")
        try:
            load_events(run)
        except ValueError as exc:
            assert "Invalid JSON" in str(exc)
        else:
            raise AssertionError("expected invalid JSON to fail")
