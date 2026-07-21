from collections import Counter

from deltarune_agent.improved_explorer import ImprovedExplorer


def test_unreliable_warp_direction_is_ignored():
    explorer = ImprovedExplorer()
    strong = ("room_a", 10, 10, "up", "room_b", 20, 20)
    weak = ("room_a", 10, 10, "left", "room_b", 20, 20)
    explorer.warps = Counter({strong: 8, weak: 1})

    reliable = dict(explorer._reliable_warps())

    assert strong in reliable
    assert weak not in reliable
    assert explorer._known_warp_direction("room_a", (10, 10), "up")
    assert not explorer._known_warp_direction("room_a", (10, 10), "left")


def test_entry_warp_is_not_selected_while_local_frontier_exists():
    explorer = ImprovedExplorer()
    backtrack = ("room_b", 10, 10, "up", "room_a", 20, 20)
    explorer.warps = Counter({backtrack: 5})
    explorer.room_entry_from["room_b"] = "room_a"
    explorer.room_entered_at["room_b"] = 0
    explorer.navigation_tick = 20

    route = explorer._route_to_learned_warp("room_b", (10, 10))

    assert route is None
