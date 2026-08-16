from __future__ import annotations

from deltarune_agent.exit_detection_v2 import (
    _adjust_exit_belief_scores,
    evaluate_exit_candidate,
    exit_candidate_source,
    exit_record_is_actionable,
)


def _record(**updates: object) -> dict[str, object]:
    record: dict[str, object] = {
        "guess_state": "proposed",
        "edge_opening_score": 0.86,
        "edge_width_ratio": 0.28,
        "multi_view_consistency": 0.82,
        "multi_view_sample_count": 2,
        "exit_candidate_views": 2,
        "exit_approach_length": 0,
        "path_continuation": False,
        "failed_approaches": 0,
        "guess_misses": 0,
    }
    record.update(updates)
    return record


def test_floor_boundary_never_promotes_from_pixels_alone() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        edge_opening_score=0.98,
        multi_view_consistency=0.98,
        multi_view_sample_count=5,
        exit_candidate_views=5,
        exit_approach_length=4,
        path_continuation=False,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "floor_boundary"
    assert state == "needs_approach_evidence"
    assert any("approach corridor" in reason for reason in reasons)


def test_floor_boundary_needs_visual_repeat_even_with_map_probe() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        path_continuation=True,
        exit_approach_length=3,
        exit_candidate_views=1,
        multi_view_sample_count=1,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "floor_boundary"
    assert state == "visual_candidate"
    assert any("independent boundary viewpoints" in reason for reason in reasons)
    assert not exit_record_is_actionable(record)


def test_stable_floor_plus_aligned_map_probe_can_promote() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        path_continuation=True,
        exit_approach_length=3,
        exit_candidate_views=2,
        multi_view_sample_count=2,
        multi_view_consistency=0.82,
        exit_candidate_state="semantic_ready",
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "semantic_ready"
    assert any("learned-open approach" in reason for reason in reasons)
    assert exit_record_is_actionable(record)


def test_geometry_path_probe_alone_is_never_semantic_exit() -> None:
    record = _record(
        visual_summary="",
        edge_opening_score=0.0,
        path_continuation=True,
        exit_approach_length=4,
        exit_candidate_views=0,
        exit_candidate_state="geometry_candidate",
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "geometry_path_probe"
    assert state == "geometry_candidate"
    assert any("not visual exit proof" in reason for reason in reasons)
    assert not exit_record_is_actionable(record)


def test_single_doorway_facade_is_only_a_visual_candidate() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 88%)",
        exit_candidate_views=1,
        multi_view_sample_count=1,
        exit_approach_length=3,
    )

    state, _score, _reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "doorway_facade"
    assert state == "visual_candidate"
    assert not exit_record_is_actionable(record)


def test_stable_repeated_doorway_still_needs_open_approach() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 88%)",
        exit_candidate_views=2,
        multi_view_sample_count=2,
        multi_view_consistency=0.80,
        edge_opening_score=0.86,
        exit_approach_length=0,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "needs_approach_evidence"
    assert any("learned-open approach" in reason for reason in reasons)


def test_stable_repeated_doorway_with_open_approach_can_promote() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 88%)",
        exit_candidate_state="semantic_ready",
        exit_candidate_views=2,
        multi_view_sample_count=2,
        multi_view_consistency=0.80,
        edge_opening_score=0.86,
        exit_approach_length=3,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "semantic_ready"
    assert any("aligns with a learned-open approach" in reason for reason in reasons)
    assert exit_record_is_actionable(record)


def test_dark_edge_opening_requires_stable_open_approach() -> None:
    record = _record(
        visual_summary="localized 28%-wide dark opening connected to the right edge; dark feature",
        exit_candidate_views=3,
        multi_view_sample_count=3,
        multi_view_consistency=0.82,
        edge_opening_score=0.76,
        edge_width_ratio=0.28,
        exit_approach_length=0,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "dark_edge_opening"
    assert state == "needs_approach_evidence"
    assert any("learned-open approach" in reason for reason in reasons)

    record["exit_approach_length"] = 3
    state, _score, reasons = evaluate_exit_candidate(record)
    assert state == "semantic_ready"
    assert any("stable localized opening" in reason for reason in reasons)


def test_poor_multiview_consistency_contradicts_visual_exit() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 90%)",
        exit_candidate_views=3,
        multi_view_sample_count=3,
        multi_view_consistency=0.20,
        exit_approach_length=4,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "contradicted"
    assert any("contradict" in reason for reason in reasons)


def test_path_and_opening_boosts_are_reduced_before_semantic_confirmation() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        edge_opening_score=0.90,
        path_continuation=True,
        exit_approach_length=3,
        exit_candidate_state="visual_candidate",
    )
    raw_scores = {
        "possible_exit": 5.80,
        "possible_character": 0.80,
        "possible_interactable": 0.80,
        "scenery": 1.00,
    }

    adjusted = _adjust_exit_belief_scores(record, raw_scores)

    assert adjusted["possible_exit"] < raw_scores["possible_exit"] / 3
    assert adjusted["possible_exit"] < raw_scores["possible_exit"]


def test_old_unverified_possible_exit_is_not_actionable_by_label_alone() -> None:
    record = _record(
        hypothesis="possible_exit",
        visual_summary="localized boundary opening",
    )

    assert not exit_record_is_actionable(record)


def test_path_continuation_is_not_actionable_by_itself() -> None:
    record = _record(
        hypothesis="possible_exit",
        path_continuation=True,
        exit_candidate_state="geometry_candidate",
    )

    assert not exit_record_is_actionable(record)


def test_confirmed_transition_remains_actionable() -> None:
    record = _record(
        hypothesis="possible_exit",
        guess_state="confirmed",
        visual_summary="localized boundary opening",
    )

    state, score, _reasons = evaluate_exit_candidate(record)

    assert state == "confirmed"
    assert score == 1.0
    assert exit_record_is_actionable(record)
