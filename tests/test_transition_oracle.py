from deltarune_agent.telemetry import TelemetrySample
from deltarune_agent.transition_oracle import (
    TransitionPrediction,
    build_oracle_from_telemetry,
    evaluate_transitions,
)


def _sample(*, sequence, source, destination, received_at):
    return TelemetrySample(
        "overworld",
        1,
        destination,
        100,
        50,
        "obj_mainchara",
        received_at,
        transition_from_room_name=source,
        transition_sequence=sequence,
        transition_from_x=90,
        transition_from_y=50,
    )


def test_oracle_uses_explicit_transition_metadata_only():
    samples = [
        _sample(sequence=7, source="room_a", destination="room_b", received_at=10.0),
        _sample(sequence=7, source="room_a", destination="room_b", received_at=10.1),
        TelemetrySample("overworld", 1, "room_c", 100, 50, "obj_mainchara", 11.0),
    ]

    oracle = build_oracle_from_telemetry(samples)

    assert len(oracle) == 1
    assert oracle[0].source_room == "room_a"
    assert oracle[0].destination_room == "room_b"
    assert oracle[0].sequence == 7


def test_transition_evaluation_tracks_match_miss_false_positive_and_wrong_destination():
    truth = build_oracle_from_telemetry([
        _sample(sequence=1, source="room_a", destination="room_b", received_at=10.0),
        _sample(sequence=2, source="room_b", destination="room_c", received_at=20.0),
    ])

    predictions = [
        TransitionPrediction(sequence=1, source_room="room_a", destination_room="room_b", predicted_at=10.2, confidence=0.9),
        TransitionPrediction(sequence=2, source_room="room_b", destination_room="room_wrong", predicted_at=20.1, confidence=0.8),
        TransitionPrediction(sequence=99, source_room="room_x", destination_room="room_y", predicted_at=30.0, confidence=0.7),
    ]

    result = evaluate_transitions(truth, predictions)

    assert result.oracle_count == 2
    assert result.prediction_count == 3
    assert result.matched == 1
    assert result.missed == 1
    assert result.false_positives == 1
    assert result.wrong_destinations == 1
    assert result.precision == round(1 / 3, 4)
    assert result.recall == 0.5
    assert result.mean_latency_seconds == 0.2


def test_transition_evaluation_accepts_unknown_predicted_source():
    truth = build_oracle_from_telemetry([
        _sample(sequence=5, source="room_a", destination="room_b", received_at=10.0)
    ])

    result = evaluate_transitions(
        truth,
        [TransitionPrediction(
            sequence=5,
            source_room=None,
            destination_room="room_b",
            predicted_at=10.1,
        )],
    )

    assert result.matched == 1
    assert result.missed == 0
