from deltarune_agent.transition_oracle import TransitionTruth
from deltarune_agent.visual_exit_benchmark import (
    collect_visual_exit_candidates,
    evaluate_visual_exits,
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
