import json

from deltarune_agent.transition_oracle import TransitionTruth
from deltarune_agent.visual_exit_benchmark import (
    collect_visual_exit_candidates,
    calibrate_visual_exit_candidates,
    evaluate_visual_exits,
    write_visual_exit_calibration,
)


def test_collects_first_observed_visual_exit_candidate_per_region():
    events = [
        {
            "step": 5,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [2, 3],
                "guess_id": "room_a@2,3",
                "anchor_world": [100, 100],
                "exit_candidate_state": "visual_candidate",
                "exit_candidate_source": "doorway_facade",
                "exit_candidate_visual_score": 0.72,
            }],
        },
        {
            "step": 9,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [2, 3],
                "guess_id": "room_a@2,3",
                "anchor_world": [101, 100],
                "exit_candidate_state": "semantic_ready",
                "exit_candidate_source": "doorway_facade",
                "exit_candidate_visual_score": 0.91,
            }],
        },
    ]

    candidates = collect_visual_exit_candidates(events)

    assert len(candidates) == 1
    assert candidates[0].candidate_id == "room_a@2,3"
    assert candidates[0].step == 5
    assert candidates[0].state == "visual_candidate"
    assert candidates[0].state_history == ((5, "visual_candidate"), (9, "semantic_ready"))


def test_visual_exit_benchmark_matches_spatially_and_reports_lead_time():
    candidates = collect_visual_exit_candidates([{
        "step": 10,
        "map_updates": [{
            "type": "screen_region",
            "exit_detection_version": 2,
            "room": "room_a",
            "region": [1, 2],
            "guess_id": "room_a@1,2",
            "anchor_world": [100, 100],
            "exit_candidate_state": "semantic_ready",
            "exit_candidate_source": "doorway_facade",
            "exit_candidate_visual_score": 0.9,
        }],
    }])
    transitions = [
        TransitionTruth(
            sequence=4,
            source_room="room_a",
            destination_room="room_b",
            source_x=108,
            source_y=104,
            observed_at=2.0,
            step=25,
        )
    ]

    result = evaluate_visual_exits(candidates, transitions, spatial_match_radius=20)

    assert result.matched_transitions == 1
    assert result.missed_transitions == 0
    assert result.detection_recall == 1.0
    assert result.semantic_ready_matches == 1
    assert result.semantic_ready_recall == 1.0
    assert result.mean_lead_steps == 15
    assert result.mean_match_distance_world_units == 8.944


def test_unmatched_visual_candidates_are_unvalidated_not_false_positives():
    candidates = collect_visual_exit_candidates([{
        "step": 2,
        "map_updates": [{
            "type": "screen_region",
            "exit_detection_version": 2,
            "room": "room_a",
            "region": [0, 0],
            "guess_id": "room_a@0,0",
            "anchor_world": [50, 50],
            "exit_candidate_state": "visual_candidate",
        }],
    }])

    result = evaluate_visual_exits(candidates, [], spatial_match_radius=16)

    assert result.candidate_count == 1
    assert result.unmatched_candidates_unvalidated == 1
    assert result.missed_transitions == 0
    assert result.detection_recall == 1.0

def test_semantic_readiness_is_only_credited_if_observed_before_crossing():
    candidates = collect_visual_exit_candidates([
        {
            "step": 5,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [1, 1],
                "guess_id": "room_a@1,1",
                "anchor_world": [100, 100],
                "exit_candidate_state": "visual_candidate",
            }],
        },
        {
            "step": 12,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [1, 1],
                "guess_id": "room_a@1,1",
                "anchor_world": [100, 100],
                "exit_candidate_state": "semantic_ready",
            }],
        },
    ])
    before_ready = evaluate_visual_exits(
        candidates,
        [TransitionTruth(1, "room_a", "room_b", 100, 100, step=8)],
        spatial_match_radius=4,
    )
    after_ready = evaluate_visual_exits(
        candidates,
        [TransitionTruth(1, "room_a", "room_b", 100, 100, step=15)],
        spatial_match_radius=4,
    )

    assert before_ready.semantic_ready_matches == 0
    assert after_ready.semantic_ready_matches == 1

def test_candidate_first_seen_on_crossing_step_does_not_count_as_prediction():
    candidates = collect_visual_exit_candidates([{
        "step": 10,
        "map_updates": [{
            "type": "screen_region",
            "exit_detection_version": 2,
            "room": "room_a",
            "region": [1, 1],
            "guess_id": "room_a@1,1",
            "anchor_world": [100, 100],
            "last_seen_step": 10,
            "exit_candidate_state": "semantic_ready",
        }],
    }])
    result = evaluate_visual_exits(
        candidates,
        [TransitionTruth(1, "room_a", "room_b", 100, 100, step=10)],
        spatial_match_radius=4,
    )

    assert result.matched_transitions == 0
    assert result.missed_transitions == 1
    assert result.unmatched_candidates_unvalidated == 1


def test_calibration_sweeps_observed_scores_without_changing_agent_thresholds(tmp_path):
    candidates = collect_visual_exit_candidates([
        {
            "step": 2,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [0, 0],
                "guess_id": "low-score",
                "anchor_world": [100, 100],
                "exit_candidate_state": "visual_candidate",
                "exit_candidate_source": "doorway_facade",
                "exit_candidate_visual_score": 0.4,
            }],
        },
        {
            "step": 4,
            "map_updates": [{
                "type": "screen_region",
                "exit_detection_version": 2,
                "room": "room_a",
                "region": [1, 0],
                "guess_id": "high-score",
                "anchor_world": [101, 100],
                "exit_candidate_state": "semantic_ready",
                "exit_candidate_source": "floor_boundary",
                "exit_candidate_visual_score": 0.9,
            }],
        },
    ])
    transitions = [
        TransitionTruth(1, "room_a", "room_b", 100, 100, step=10),
    ]

    report = calibrate_visual_exit_candidates(
        candidates, transitions, spatial_match_radius=8
    )

    assert report["agent_thresholds_modified"] is False
    assert report["candidate_count"] == 2
    assert report["scored_candidate_count"] == 2
    assert [row["score_threshold"] for row in report["threshold_sweep"]] == [0.4, 0.9]
    assert report["threshold_sweep"][0]["candidate_count"] == 2
    assert report["threshold_sweep"][1]["candidate_count"] == 1
    assert report["threshold_sweep"][0]["matched_transitions"] == 1
    assert report["threshold_sweep"][1]["matched_transitions"] == 1
    assert len(report["source_breakdown"]) == 2
    assert "Precision, specificity" in report["limitations"][1]

    output = tmp_path / "visual_exit_calibration.json"
    write_visual_exit_calibration(output, report)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["calibration_version"] == 1
    assert saved["agent_thresholds_modified"] is False


def test_calibration_with_no_scores_returns_empty_sweep():
    candidates = collect_visual_exit_candidates([{
        "step": 1,
        "map_updates": [{
            "type": "screen_region",
            "exit_detection_version": 2,
            "room": "room_a",
            "region": [0, 0],
            "guess_id": "unscored",
            "anchor_world": [10, 10],
            "exit_candidate_state": "visual_candidate",
        }],
    }])

    report = calibrate_visual_exit_candidates(candidates, [])

    assert report["candidate_count"] == 1
    assert report["unscored_candidate_count"] == 1
    assert report["threshold_sweep"] == []
