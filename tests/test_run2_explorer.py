from PIL import Image

from deltarune_agent.observer import Observation
from deltarune_agent.perception import (
    GameState,
    Perception,
    VisualFeatures,
)
from deltarune_agent.run2_explorer import Run2Explorer
from deltarune_agent.telemetry import TelemetrySample


def sample(
    *,
    mode: str = "overworld",
    room: str = "room_a",
    object_name: str = "obj_mainchara",
    controlled: bool | None = True,
) -> TelemetrySample:
    return TelemetrySample(
        mode=mode,
        room_id=1,
        room_name=room,
        x=80.0,
        y=80.0,
        object_name=object_name,
        received_at=1.0,
        player_controlled=controlled,
    )


def perception(state: GameState) -> Perception:
    return Perception(
        state=state,
        confidence=1.0,
        features=VisualFeatures(0.0, 0.0, 0.0, 0.0, 0.0),
        source="test",
    )


def test_control_lock_releases_movement_during_room_transition():
    explorer = Run2Explorer()
    observation = Observation(Image.new("RGB", (320, 240)), 10)

    action = explorer.choose(
        observation,
        perception(GameState.OVERWORLD),
        sample(controlled=False),
    )

    assert action.name == "wait"
    assert "control locked" in explorer.reason
    assert explorer.control_lock_waits == 1


def test_single_writer_text_row_is_not_treated_as_choice():
    explorer = Run2Explorer()
    observation = Observation(Image.new("RGB", (320, 240)), 20)

    action = explorer.choose(
        observation,
        perception(GameState.DIALOGUE),
        sample(
            mode="dialogue",
            object_name="obj_writer",
            controlled=False,
        ),
    )

    assert action.name == "confirm"
    assert "not a choice" in explorer.reason
    assert explorer.rejected_writer_choices == 1
    assert not explorer.menu_action_queue


def test_recent_room_link_is_temporarily_blocked():
    explorer = Run2Explorer()
    explorer.navigation_tick = 100
    explorer.room_link_cooldowns[frozenset(("room_a", "room_b"))] = 500

    assert explorer._link_is_cooling_down("room_a", "room_b")

    explorer.navigation_tick = 500
    assert not explorer._link_is_cooling_down("room_a", "room_b")


def test_first_room_entry_temporarily_blocks_continuing_same_direction():
    explorer = Run2Explorer()
    source = sample(room="room_a")
    destination = sample(room="room_b")
    explorer._observe_room(source)
    explorer._select("up", "enter room", source)

    explorer._observe_room(destination)
    arrival = explorer._cell(destination)

    assert explorer._is_entry_warp_direction("room_b", arrival, "up")
    assert not explorer._is_entry_warp_direction("room_b", arrival, "down")
    assert explorer.entry_direction_guard_avoids == 1

    explorer.navigation_tick += 24
    assert not explorer._is_entry_warp_direction("room_b", arrival, "up")
