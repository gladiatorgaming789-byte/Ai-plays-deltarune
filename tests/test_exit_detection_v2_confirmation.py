from __future__ import annotations

from deltarune_agent.exit_detection_v2_confirmation import (
    confirm_candidate_from_transition,
    transition_candidate_matches,
)
from deltarune_agent.run4_explorer import Run4Explorer


def _candidate(
    *,
    anchor: tuple[int, int] | None,
    state: str = "visual_candidate",
    score: float = 0.45,
) -> dict[str, object]:
    record: dict[str, object] = {
        "views": 2,
        "independent_views": 2,
        "interest": 0.5,
        "hypothesis": None,
        "guess_state": "proposed",
        "visual_summary": "rectangular doorway facade near the upper wall (frame score 82%)",
        "edge_opening_score": 0.82,
        "edge_width_ratio": 0.25,
        "exit_detection_version": 2,
        "exit_candidate_source": "doorway_facade",
        "exit_candidate_state": state,
        "exit_candidate_visual_score": score,
        "last_seen_step": 40,
        "last_seen_sequence": 4,
    }
    if anchor is not None:
        record["anchor_cell"] = [anchor[0], anchor[1]]
    return record


def test_real_transition_confirms_unresolved_nearby_candidate() -> None:
    explorer = Run4Explorer()
    key = ("room_a", 2, 2)
    explorer.screen_regions[key] = _candidate(anchor=(9, 8))

    confirmed = confirm_candidate_from_transition(
        explorer,
        "room_a",
        (8, 8),
        "room_b",
    )

    assert confirmed == key
    record = explorer.screen_regions[key]
    assert record["guess_state"] == "confirmed"
    assert record["hypothesis"] == "possible_exit"
    assert record["guess_semantic_state"] == "possible_exit"
    assert record["exit_candidate_state"] == "confirmed"
    assert record["confirmed_target_room"] == "room_b"
    assert record["confirmed_at_cell"] == [8, 8]
    assert any(
        "observed transition" in reason
        for reason in record["exit_candidate_reasons"]
    )


def test_far_candidate_is_not_confirmed_by_unrelated_transition() -> None:
    explorer = Run4Explorer()
    key = ("room_a", 4, 4)
    explorer.screen_regions[key] = _candidate(anchor=(20, 20))

    confirmed = confirm_candidate_from_transition(
        explorer,
        "room_a",
        (8, 8),
        "room_b",
    )

    assert confirmed is None
    assert explorer.screen_regions[key]["guess_state"] == "proposed"


def test_candidate_without_anchor_requires_exact_transition_region() -> None:
    explorer = Run4Explorer()
    source_cell = (8, 8)
    source_region = explorer._region(source_cell)
    exact = ("room_a", source_region[0], source_region[1])
    neighbor = ("room_a", source_region[0] + 1, source_region[1])
    explorer.screen_regions[exact] = _candidate(anchor=None, score=0.35)
    explorer.screen_regions[neighbor] = _candidate(anchor=None, score=0.95)

    matches = transition_candidate_matches(explorer, "room_a", source_cell)

    assert [key for key, _record in matches] == [exact]


def test_nearest_candidate_wins_over_higher_scored_farther_candidate() -> None:
    explorer = Run4Explorer()
    source_cell = (8, 8)
    near_key = ("room_a", 2, 2)
    farther_key = ("room_a", 2, 3)
    explorer.screen_regions[near_key] = _candidate(anchor=(8, 9), score=0.35)
    explorer.screen_regions[farther_key] = _candidate(anchor=(8, 11), score=0.95)

    matches = transition_candidate_matches(explorer, "room_a", source_cell)

    assert matches
    assert matches[0][0] == near_key


def test_transition_does_not_confirm_non_exit_visual_memory() -> None:
    explorer = Run4Explorer()
    key = ("room_a", 2, 2)
    explorer.screen_regions[key] = {
        "views": 3,
        "interest": 0.8,
        "hypothesis": None,
        "guess_state": "proposed",
        "visual_summary": "compact colorful feature",
        "anchor_cell": [8, 8],
    }

    confirmed = confirm_candidate_from_transition(
        explorer,
        "room_a",
        (8, 8),
        "room_b",
    )

    assert confirmed is None
    assert explorer.screen_regions[key]["guess_state"] == "proposed"
