from __future__ import annotations

from deltarune_agent.run16_semantics import install_run16_semantics

install_run16_semantics()

from deltarune_agent.run16_explorer import Run16Explorer
from deltarune_agent.run16_warp_guard import Run16GuardedExplorer


def test_guard_discards_automatic_route_from_legacy_router(monkeypatch):
    explorer = Run16GuardedExplorer()
    warp = ("room_a", 1, 1, "event", "room_b", 2, 2)
    explorer.world.warps[warp] = 1
    explorer.world.reconcile_warp_portals()

    monkeypatch.setattr(
        Run16Explorer,
        "_route_to_learned_warp",
        lambda self, room, start: ("up", warp),
    )

    assert explorer._route_to_learned_warp("room_a", (0, 0)) is None
    assert explorer.automatic_warps_deprioritized == 1


def test_guard_preserves_cardinal_manual_route(monkeypatch):
    explorer = Run16GuardedExplorer()
    warp = ("room_a", 1, 1, "down", "room_b", 2, 2)
    explorer.world.warps[warp] = 1
    explorer.world.reconcile_warp_portals()

    monkeypatch.setattr(
        Run16Explorer,
        "_route_to_learned_warp",
        lambda self, room, start: ("down", warp),
    )

    assert explorer._route_to_learned_warp("room_a", (0, 0)) == ("down", warp)
