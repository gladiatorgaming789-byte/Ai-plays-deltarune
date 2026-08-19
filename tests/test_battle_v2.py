from __future__ import annotations

from PIL import Image, ImageDraw

from deltarune_agent.battle_v2 import BattleV2Controller
from deltarune_agent.battle_v2_components import install_battle_v2_components
from deltarune_agent.battle_v2_menu_guard import install_battle_v2_menu_guard


install_battle_v2_components()
install_battle_v2_menu_guard()


def _frame_with_soul(color: tuple[int, int, int], *, x: int = 160, y: int = 125) -> Image.Image:
    image = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((x - 3, y - 3, x + 3, y + 3), fill=color)
    # A compact bright threat far enough from the SOUL to be detected.
    draw.rectangle((220, 120, 224, 124), fill="white")
    return image


def _menu(label: str, x: int = 40) -> Image.Image:
    image = Image.new("RGB", (320, 240), "black")
    ImageDraw.Draw(image).text((x, 170), label, fill="white")
    return image


def test_detects_visible_red_soul_without_telemetry_position() -> None:
    controller = BattleV2Controller()
    soul = controller.observe_soul(_frame_with_soul((255, 0, 0)))
    assert soul is not None
    assert soul.mode == "red"
    assert abs(soul.x - 160) < 2
    assert abs(soul.y - 125) < 2


def test_same_colored_hud_pixels_do_not_merge_with_real_soul() -> None:
    image = _frame_with_soul((255, 0, 0))
    draw = ImageDraw.Draw(image)
    # Separate red HUD-like strip inside the broad scan area. The old global
    # color aggregation made the combined red extent too wide and rejected the
    # real compact SOUL; connected components must keep them independent.
    draw.rectangle((25, 190, 95, 194), fill=(255, 0, 0))
    soul = BattleV2Controller.observe_soul(image)
    assert soul is not None
    assert soul.mode == "red"
    assert abs(soul.x - 160) < 2


def test_yellow_mode_fires_while_using_visible_defense() -> None:
    controller = BattleV2Controller()
    action = controller.choose(
        _frame_with_soul((255, 220, 0)),
        (9999.0, 9999.0),
        visual_valid=True,
    )
    assert controller.mode_steps["yellow"] == 1
    assert "z" in action.keys
    assert controller.yellow_shots == 1


def test_green_mode_returns_shield_direction_from_visible_threat() -> None:
    controller = BattleV2Controller()
    image = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((157, 122, 163, 128), fill=(0, 255, 0))
    draw.rectangle((218, 122, 224, 128), fill="white")
    action = controller.choose(image, None, visual_valid=True)
    assert controller.mode_steps["green"] == 1
    assert any(key in action.keys for key in ("left", "right", "up", "down"))
    assert controller.green_blocks == 1


def test_cursor_only_visual_change_does_not_mark_menu_pattern_successful() -> None:
    controller = BattleV2Controller()
    first = _menu("COMMAND", 40)
    moved_cursor_like_view = _menu("COMMAND", 55)

    controller.choose(first, None, visual_valid=True)
    signature = controller.pending_signature
    assert signature is not None
    assert controller.action_queue

    # The image changed while reset/navigation inputs are still queued. This is
    # not evidence that the selected battle command advanced the turn.
    controller.choose(moved_cursor_like_view, None, visual_valid=True)
    memory = controller.menu_memory[signature]
    assert memory.successful_pattern is None
    assert controller.turns_advanced == 0


def test_menu_state_learns_pattern_when_visible_state_advances() -> None:
    controller = BattleV2Controller()
    menu = _menu("COMMAND")

    # Run the bounded reset + first pattern + confirm sequence.
    for _ in range(7):
        controller.choose(menu, None, visual_valid=True)
    signature = controller.last_menu_signature or controller.menu_signature(menu)

    # A newly visible defensive SOUL is observed consequence evidence.
    controller.choose(_frame_with_soul((255, 0, 0)), None, visual_valid=True)
    memory = controller.menu_memory[signature]
    assert memory.successful_pattern is not None
    assert controller.turns_advanced >= 1


def test_untrusted_battle_frame_never_generates_inputs() -> None:
    controller = BattleV2Controller()
    action = controller.choose(
        _frame_with_soul((255, 0, 0)),
        None,
        visual_valid=False,
    )
    assert action.name == "wait"
    assert controller.visual_invalid_waits == 1
