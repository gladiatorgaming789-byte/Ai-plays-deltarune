import json

from deltarune_agent.transition_benchmark import main


def test_benchmark_automatically_compares_recorded_policy_predictions(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    telemetry = {
        "room_name": "room_b",
        "transition_from_room_name": "room_a",
        "transition_sequence": 3,
        "transition_from_foot_x": 90,
        "transition_from_foot_y": 50,
        "player_foot_x": 20,
        "player_foot_y": 30,
    }
    (run / "events.jsonl").write_text(
        json.dumps({
            "step": 17,
            "elapsed_seconds": 1.2,
            "telemetry": telemetry,
        }) + "\n",
        encoding="utf-8",
    )
    (run / "predictions.jsonl").write_text(
        json.dumps({
            "step": 17,
            "elapsed_seconds": 1.3,
            "transition_predictions": [{
                "sequence": 3,
                "source_room": "room_a",
                "destination_room": "room_b",
                "confidence": 1.0,
                "step": 17,
            }],
        }) + "\n",
        encoding="utf-8",
    )

    assert main([str(run)]) == 0

    output = json.loads(
        (run / "transition_benchmark" / "summary.json").read_text(encoding="utf-8")
    )
    evaluation = output["evaluation"]
    assert output["oracle_transitions"] == 1
    assert output["prediction_count"] == 1
    assert evaluation["matched"] == 1
    assert evaluation["missed"] == 0
    assert evaluation["false_positives"] == 0
    assert evaluation["f1"] == 1.0
    assert evaluation["mean_latency_seconds"] == 0.1
