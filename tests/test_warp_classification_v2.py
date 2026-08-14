from deltarune_agent.run4_explorer import (
    ROOM_EXIT_PRIORITY_MIN_CELLS,
    ROOM_EXIT_PRIORITY_STORY_STALL,
    Run4Explorer,
)
from deltarune_agent.warp_classification_v2 import install_warp_classification_v2


install_warp_classification_v2()


def _warp(source="room_a", target="room_b"):
    return (source, 5, 5, "right", target, 1, 5)


def _record(explorer: Run4Explorer, warp, *, novel=False):
    return explorer.world.record_warp_transition(
        warp,
        destination_was_novel=novel,
        step=explorer.navigation_tick,
    )


def _apply_room_completion_pressure(explorer: Run4Explorer, room: str) -> None:
    explorer.room_entered_at[room] = 0
    explorer.navigation_tick = 300
    explorer.story_stall_steps = ROOM_EXIT_PRIORITY_STORY_STALL
    explorer.seen_cells.update(
        (room, index, 0)
        for index in range(ROOM_EXIT_PRIORITY_MIN_CELLS)
    )


def test_return_prone_non_arrival_warp_stays_eligible() -> None:
    explorer = Run4Explorer()
    outbound = _warp("room_a", "room_b")
    returning = _warp("room_b", "room_a")
    outbound_id = _record(explorer, outbound, novel=True)
    returning_id = _record(explorer, returning, novel=False)
    explorer.world.record_warp_return(
        outbound_id,
        dwell_steps=5,
        returned_via=returning_id,
    )

    assert explorer.world.warp_portals[outbound_id]["role"] == "unknown"
    assert "quick_return" in explorer.world.warp_portals[outbound_id]["behavior_tags"]
    assert explorer._warp_is_priority_candidate(outbound)


def test_arrival_return_warp_is_temporarily_blocked_before_stall() -> None:
    explorer = Run4Explorer()
    warp = _warp("room_b", "room_a")
    _record(explorer, warp, novel=False)
    explorer.room_entry_from["room_b"] = "room_a"

    assert not explorer._warp_is_priority_candidate(warp)


def test_arrival_return_warp_becomes_eligible_under_room_completion_pressure() -> None:
    explorer = Run4Explorer()
    warp = _warp("room_b", "room_a")
    _record(explorer, warp, novel=False)
    explorer.room_entry_from["room_b"] = "room_a"
    _apply_room_completion_pressure(explorer, "room_b")

    assert explorer._warp_is_priority_candidate(warp)


def test_loop_suppressed_warp_is_a_temporary_safety_hold() -> None:
    explorer = Run4Explorer()
    warp = _warp("room_a", "room_b")
    portal_id = _record(explorer, warp, novel=False)
    explorer.world.record_warp_suppression(portal_id, "A-B-A loop")
    explorer.world.record_warp_suppression(portal_id, "A-B-A loop")

    assert explorer.world.warp_portals[portal_id]["role"] == "loop_suppressed"
    assert not explorer._warp_is_priority_candidate(warp)

    _apply_room_completion_pressure(explorer, "room_a")
    assert explorer._warp_is_priority_candidate(warp)


def test_confirmed_progression_warp_is_eligible_even_when_it_is_arrival_direction() -> None:
    explorer = Run4Explorer()
    warp = _warp("room_b", "room_a")
    portal_id = _record(explorer, warp, novel=False)
    explorer.room_entry_from["room_b"] = "room_a"
    explorer.world.record_warp_progress(
        portal_id,
        "observed story progress after crossing",
    )

    assert explorer.world.warp_portals[portal_id]["role"] == "progression"
    assert explorer._warp_is_priority_candidate(warp)
