from __future__ import annotations

import deltarune_agent.run21_final as final_module
from deltarune_agent.run21_final import Run21Explorer
from deltarune_agent.run21_link_cooldowns import Run21CooldownExplorer


def _stub() -> Run21Explorer:
    explorer = Run21Explorer.__new__(Run21Explorer)
    explorer.navigation_tick = 300
    explorer.entry_direction_guards = {}
    explorer.room_entry_from = {"room_b": "room_a"}
    explorer.suppressed_room_links = {frozenset(("room_a", "room_b"))}
    explorer._room_link_cooldown_until = {}
    explorer.warps = {
        ("room_b", 10, 10, "left", "room_a", 20, 20): 1,
    }
    explorer.recovery_suppressed_link_approach_overrides = 0
    explorer._recovery_suppressed_link_override_ticks = set()
    return explorer


def test_strong_recovery_pressure_can_approach_observed_suppressed_warp(monkeypatch) -> None:
    explorer = _stub()
    monkeypatch.setattr(
        Run21CooldownExplorer,
        "_is_entry_warp_direction",
        lambda self, room, cell, direction: True,
    )
    monkeypatch.setattr(
        final_module,
        "_room_completion_pressure",
        lambda explorer, room: True,
    )

    # From one cell right of the learned source, moving left approaches it.
    assert not explorer._is_entry_warp_direction("room_b", (11, 10), "left")
    assert explorer.recovery_suppressed_link_approach_overrides == 1


def test_new_short_pingpong_hold_still_blocks_recovery_approach(monkeypatch) -> None:
    explorer = _stub()
    explorer._room_link_cooldown_until[frozenset(("room_a", "room_b"))] = 400
    monkeypatch.setattr(
        Run21CooldownExplorer,
        "_is_entry_warp_direction",
        lambda self, room, cell, direction: True,
    )
    monkeypatch.setattr(
        final_module,
        "_room_completion_pressure",
        lambda explorer, room: True,
    )

    assert explorer._is_entry_warp_direction("room_b", (11, 10), "left")


def test_immediate_entry_direction_guard_is_never_overridden(monkeypatch) -> None:
    explorer = _stub()
    explorer.entry_direction_guards["room_b"] = ("left", (11, 10), 350)
    monkeypatch.setattr(
        Run21CooldownExplorer,
        "_is_entry_warp_direction",
        lambda self, room, cell, direction: True,
    )
    monkeypatch.setattr(
        final_module,
        "_room_completion_pressure",
        lambda explorer, room: True,
    )

    assert explorer._is_entry_warp_direction("room_b", (11, 10), "left")
