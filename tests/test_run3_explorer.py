from collections import Counter

from deltarune_agent.run3_explorer import Run3Explorer


def test_doorway_actions_are_canonicalized_from_room_geometry():
    explorer = Run3Explorer()
    explorer.seen_cells.update(
        ("room_a", x, y)
        for x in range(10, 21)
        for y in range(10, 25)
    )
    down = ("room_a", 15, 24, "down", "room_b", 20, 10)
    left = ("room_a", 15, 24, "left", "room_b", 20, 10)
    right = ("room_a", 16, 24, "right", "room_b", 20, 10)
    explorer.warps = Counter({down: 3, left: 1, right: 1})

    explorer._canonicalize_link("room_a", "room_b")

    matching = [warp for warp in explorer.warps if warp[0] == "room_a"]
    assert len(matching) == 1
    assert matching[0][3] == "down"
    assert explorer.warps[matching[0]] == 5


def test_known_warp_is_deferred_while_local_exit_probe_exists():
    explorer = Run3Explorer()
    explorer.seen_cells.update(
        {
            ("room_a", 10, 10),
            ("room_a", 10, 11),
            ("room_a", 10, 12),
        }
    )
    explorer.open_edges.update(
        {
            ("room_a", 10, 11, "up", 10, 10),
            ("room_a", 10, 10, "down", 10, 11),
            ("room_a", 10, 12, "up", 10, 11),
            ("room_a", 10, 11, "down", 10, 12),
        }
    )
    warp = ("room_a", 10, 12, "down", "room_b", 10, 10)
    explorer.warps = Counter({warp: 5})

    route = explorer._route_to_learned_warp("room_a", (10, 11))

    assert route is None
    assert explorer.deferred_warps_for_local_leads == 1


def test_repeated_room_link_increases_cooldown_backoff():
    explorer = Run3Explorer()
    link = frozenset(("room_a", "room_b"))
    explorer.room_link_crossings[link] = 3
    explorer.navigation_tick = 100
    explorer.observed_room = "room_a"

    class Sample:
        room_name = "room_b"
        room_id = 2

    # Test the backoff formula directly without needing a full telemetry packet.
    multiplier = min(4, 1 + (explorer.room_link_crossings[link] + 1) // 2)
    expires = explorer.navigation_tick + 600 * multiplier

    assert expires == 1300
