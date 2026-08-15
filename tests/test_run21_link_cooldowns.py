from __future__ import annotations

import deltarune_agent.run21_link_cooldowns as cooldown_module
from deltarune_agent.run21_link_cooldowns import Run21CooldownExplorer


def _stub() -> Run21CooldownExplorer:
    explorer = Run21CooldownExplorer.__new__(Run21CooldownExplorer)
    explorer.navigation_tick = 200
    explorer.room_link_cooldowns = {}
    explorer._room_link_cooldown_until = {}
    explorer.legacy_link_cooldown_pressure_overrides = 0
    explorer._legacy_override_links = set()
    return explorer


def test_room_completion_pressure_can_override_old_blanket_link_cooldown(monkeypatch) -> None:
    explorer = _stub()
    link = frozenset(("room_a", "room_b"))
    explorer.room_link_cooldowns[link] = 700
    monkeypatch.setattr(
        cooldown_module,
        "_room_completion_pressure",
        lambda explorer, room: True,
    )

    assert not explorer._link_is_cooling_down("room_a", "room_b")
    assert explorer.legacy_link_cooldown_pressure_overrides == 1


def test_old_link_cooldown_remains_before_recovery_pressure(monkeypatch) -> None:
    explorer = _stub()
    link = frozenset(("room_a", "room_b"))
    explorer.room_link_cooldowns[link] = 700
    monkeypatch.setattr(
        cooldown_module,
        "_room_completion_pressure",
        lambda explorer, room: False,
    )

    assert explorer._link_is_cooling_down("room_a", "room_b")
    assert explorer.legacy_link_cooldown_pressure_overrides == 0


def test_new_short_pingpong_hold_remains_authoritative_under_pressure(monkeypatch) -> None:
    explorer = _stub()
    link = frozenset(("room_a", "room_b"))
    explorer.room_link_cooldowns[link] = 700
    explorer._room_link_cooldown_until[link] = 300
    monkeypatch.setattr(
        cooldown_module,
        "_room_completion_pressure",
        lambda explorer, room: True,
    )

    assert explorer._link_is_cooling_down("room_a", "room_b")
    assert explorer.legacy_link_cooldown_pressure_overrides == 0
