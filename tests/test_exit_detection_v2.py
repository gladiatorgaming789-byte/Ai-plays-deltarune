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
        "walkable_evidence": False,
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
        walkable_evidence=True,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "floor_boundary"
    assert state == "needs_path_evidence"
    assert any("alone does not prove traversability" in reason for reason in reasons)


def test_mapped_path_promotes_boundary_candidate() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        path_continuation=True,
    )

    state, score, _reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "mapped_path_continuation"
    assert state == "semantic_ready"
    assert score >= 0.95
    assert exit_record_is_actionable(record)


def test_single_doorway_facade_is_only_a_visual_candidate() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 88%)",
        exit_candidate_views=1,
        multi_view_sample_count=1,
    )

    state, _score, _reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "doorway_facade"
    assert state == "visual_candidate"
    assert not exit_record_is_actionable(record)


def test_stable_repeated_doorway_facade_can_promote() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 88%)",
        exit_candidate_state="semantic_ready",
        exit_candidate_views=2,
        multi_view_sample_count=2,
        multi_view_consistency=0.80,
        edge_opening_score=0.86,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "semantic_ready"
    assert any("repeated stably" in reason for reason in reasons)
    assert exit_record_is_actionable(record)


def test_dark_edge_opening_requires_walkable_approach() -> None:
    record = _record(
        visual_summary="localized 28%-wide dark opening connected to the right edge; dark feature",
        exit_candidate_views=3,
        multi_view_sample_count=3,
        multi_view_consistency=0.82,
        edge_opening_score=0.76,
        edge_width_ratio=0.28,
        walkable_evidence=False,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert exit_candidate_source(record) == "dark_edge_opening"
    assert state == "needs_path_evidence"
    assert any("walkable approach" in reason for reason in reasons)

    record["walkable_evidence"] = True
    state, _score, reasons = evaluate_exit_candidate(record)
    assert state == "semantic_ready"
    assert any("learned walkable approach" in reason for reason in reasons)


def test_poor_multiview_consistency_contradicts_visual_exit() -> None:
    record = _record(
        visual_summary="rectangular doorway facade near the upper wall (frame score 90%)",
        exit_candidate_views=3,
        multi_view_sample_count=3,
        multi_view_consistency=0.20,
    )

    state, _score, reasons = evaluate_exit_candidate(record)

    assert state == "contradicted"
    assert any("contradict" in reason for reason in reasons)


def test_visual_floor_boost_is_reduced_before_semantic_confirmation() -> None:
    record = _record(
        visual_summary="visible floor-colored continuation touching the true bottom room boundary",
        edge_opening_score=0.90,
        exit_candidate_state="needs_path_evidence",
    )
    raw_scores = {
        "possible_exit": 3.10,
        "possible_character": 0.80,
        "possible_interactable": 0.80,
        "scenery": 1.00,
    }

    adjusted = _adjust_exit_belief_scores(record, raw_scores)

    assert adjusted["possible_exit"] < raw_scores["possible_exit"]
    assert adjusted["scenery"] > raw_scores["scenery"]


def test_old_unverified_possible_exit_is_not_actionable_by_label_alone() -> None:
    record = _record(
        hypothesis="possible_exit",
        visual_summary="localized boundary opening",
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
