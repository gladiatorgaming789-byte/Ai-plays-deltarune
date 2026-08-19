from __future__ import annotations

from PIL import Image, ImageDraw

from deltarune_agent.perception import GameState
from deltarune_agent.special_gameplay import (
    MISSING_TELEMETRY_GRACE,
    SpecialGameplayCoordinator,
)


def _dynamic_frame(offset: int) -> Image.Image:
    image = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40 + offset, 80, 90 + offset, 130), fill="white")
    draw.rectangle((180 - offset, 145, 220 - offset, 180), fill=(120, 120, 120))
    return image


def test_static_telemetry_gap_does_not_activate_control_discovery() -> None:
    coordinator = SpecialGameplayCoordinator()
    frame = Image.new("RGB", (320, 240), "black")
    actions = [
        coordinator.choose(
            frame,
            telemetry_present=False,
            visual_valid=True,
            state=GameState.OVERWORLD,
        )
        for _ in range(MISSING_TELEMETRY_GRACE + 4)
    ]
    assert all(action is None for action in actions)
    assert coordinator.active is False


def test_dynamic_gap_eventually_runs_bounded_control_experiment() -> None:
    coordinator = SpecialGameplayCoordinator()
    selected = []
    for step in range(MISSING_TELEMETRY_GRACE + 6):
        action = coordinator.choose(
            _dynamic_frame(step * 4),
            telemetry_present=False,
            visual_valid=True,
            state=GameState.OVERWORLD,
        )
        if action is not None:
            selected.append(action.name)
    assert coordinator.active is True
    assert selected
    assert coordinator.actions_selected == len(selected)
    assert coordinator.contexts


def test_returning_telemetry_immediately_deactivates_special_fallback() -> None:
    coordinator = SpecialGameplayCoordinator()
    for step in range(MISSING_TELEMETRY_GRACE + 2):
        coordinator.choose(
            _dynamic_frame(step * 5),
            telemetry_present=False,
            visual_valid=True,
            state=GameState.UNKNOWN,
        )
    assert coordinator.active is True

    action = coordinator.choose(
        _dynamic_frame(80),
        telemetry_present=True,
        visual_valid=True,
        state=GameState.OVERWORLD,
    )
    assert action is None
    assert coordinator.active is False
    assert coordinator.missing_telemetry_steps == 0


def test_dialogue_or_menu_never_uses_special_control_discovery() -> None:
    coordinator = SpecialGameplayCoordinator()
    for state in (GameState.DIALOGUE, GameState.MENU, GameState.BATTLE):
        action = coordinator.choose(
            _dynamic_frame(20),
            telemetry_present=False,
            visual_valid=True,
            state=state,
        )
        assert action is None
        assert coordinator.missing_telemetry_steps == 0
