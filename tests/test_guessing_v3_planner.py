from __future__ import annotations

import deltarune_agent.guessing_v3 as guessing_v3
from deltarune_agent.run4_explorer import Run4Explorer


def _unknown_visible_guess(room: str) -> tuple[tuple[str, int, int], dict[str, object]]:
    key = (room, 2, 2)
    return key, {
        "views": 2,
        "interest": 0.25,
        "guess_state": "proposed",
        "guess_semantic_state": guessing_v3.UNKNOWN_BUT_INTERESTING,
        "guess_beliefs": {
            "possible_exit": 0.24,
            "possible_character": 0.26,
            "possible_interactable": 0.27,
            "scenery": 0.23,
        },
        "anchor_cell": [8, 8],
        "information_probe_attempts": 0,
        "information_probe_cooldown_until": 0,
        "guess_evidence_ledger": [],
    }


def test_v3_replaces_only_final_blind_probe_with_information_gain(monkeypatch) -> None:
    explorer = Run4Explorer()
    room = "room_test"
    key, record = _unknown_visible_guess(room)
    explorer.screen_regions[key] = record
    explorer.current_visible_regions = {key}
    explorer.navigation_tick = 100
    explorer.open_edges.update(
        {
            (room, 4, 8, "up", 4, 7),
            (room, 4, 7, "down", 4, 8),
        }
    )
    monkeypatch.setattr(
        guessing_v3,
        "_ORIGINAL_RUN4_PLAN",
        lambda _self, _room, _cell: (
            "left",
            1,
            "no reachable frontier; probe left",
        ),
    )

    direction, commitment, reason = guessing_v3._plan_exploration_v3(
        explorer,
        room,
        (4, 8),
    )

    assert direction == "up"
    assert commitment == 2
    assert reason.startswith("information gain:")


def test_v3_never_steals_known_warp_or_other_evidence_plan(monkeypatch) -> None:
    explorer = Run4Explorer()
    room = "room_test"
    key, record = _unknown_visible_guess(room)
    explorer.screen_regions[key] = record
    explorer.current_visible_regions = {key}
    explorer.navigation_tick = 100
    expected = (
        "right",
        1,
        "follow learned warp to room_next via right from (9,8)",
    )
    monkeypatch.setattr(
        guessing_v3,
        "_ORIGINAL_RUN4_PLAN",
        lambda _self, _room, _cell: expected,
    )

    assert guessing_v3._plan_exploration_v3(explorer, room, (4, 8)) == expected
    assert record["information_probe_attempts"] == 0
