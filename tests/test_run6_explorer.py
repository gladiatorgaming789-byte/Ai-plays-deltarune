from PIL import Image

from deltarune_agent.observer import Observation
from deltarune_agent.perception import GameState, Perception, VisualFeatures
from deltarune_agent.run6_explorer import (
    AUTOMATIC_SEQUENCE_SETTLE_STEPS,
    Run6Explorer,
)
from deltarune_agent.telemetry import TelemetrySample


def sample(
    room: str,
    *,
    x: float = 80.0,
    y: float = 80.0,
    controlled: bool | None = True,
    transition_from_room: str | None = None,
    transition_from_x: float | None = None,
    transition_from_y: float | None = None,
    transition_from_facing: str | None = None,
) -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=1,
        room_name=room,
        x=x,
        y=y,
        object_name="obj_mainchara",
        received_at=1.0,
        version=9,
        room_width=320.0,
        room_height=240.0,
        player_controlled=controlled,
        player_foot_x=x,
        player_foot_y=y,
        transition_from_room_name=transition_from_room,
        transition_from_foot_x=transition_from_x,
        transition_from_foot_y=transition_from_y,
        transition_from_facing=transition_from_facing,
    )


def perception(state: GameState) -> Perception:
    return Perception(
        state=state,
        confidence=1.0,
        features=VisualFeatures(0.0, 0.0, 0.0, 0.0, 0.0),
        source="test",
    )


def observation(step: int = 0) -> Observation:
    return Observation(Image.new("RGB", (320, 240)), step)


def test_v9_transition_facing_overrides_stale_requested_key():
    explorer = Run6Explorer()
    source = sample("room_a", x=120.0, y=120.0)
    destination = sample(
        "room_b",
        x=40.0,
        y=120.0,
        transition_from_room="room_a",
        transition_from_x=120.0,
        transition_from_y=120.0,
        transition_from_facing="left",
    )
    explorer._observe_room(source)
    explorer.last_movement = "right"
    explorer.last_overworld_movement = "right"

    explorer._observe_room(destination)

    learned = [
        warp
        for warp in explorer.warps
        if warp[0] == "room_a" and warp[4] == "room_b"
    ]
    assert learned
    assert {warp[3] for warp in learned} == {"left"}
    assert explorer.transition_direction_overrides == 1


def test_automatic_dialogue_boundaries_count_as_one_sequence():
    explorer = Run6Explorer()
    telemetry = sample("room_story", controlled=False)

    for state in (
        GameState.DIALOGUE,
        GameState.CUTSCENE,
        GameState.DIALOGUE,
        GameState.CUTSCENE,
    ):
        explorer._observe_story_state(state, telemetry, False)

    assert explorer.story_progress_events == 1
    assert explorer.automatic_sequence_events == 1

    controlled = sample("room_story", controlled=True)
    for _ in range(AUTOMATIC_SEQUENCE_SETTLE_STEPS):
        explorer._observe_story_state(GameState.OVERWORLD, controlled, False)
    explorer._observe_story_state(GameState.DIALOGUE, telemetry, False)

    assert explorer.story_progress_events == 2
    assert explorer.automatic_sequence_events == 2


def test_choice_cannot_succeed_before_planned_confirm():
    explorer = Run6Explorer()
    record = {
        "room": "room_choice",
        "attempts": [1] + [0] * 8,
        "failures": [0] * 9,
        "successes": [0] * 9,
        "successful_pattern": None,
    }
    explorer.choice_trials.append(record)
    explorer.pending_choice_record = record
    explorer.pending_choice_pattern = 0
    explorer.pending_choice_started_at = explorer.navigation_tick
    explorer.pending_choice_confirmed = False

    explorer._record_story_progress(
        "automatic scripted sequence",
        sample("room_choice"),
    )

    assert record["successes"][0] == 0
    assert record["successful_pattern"] is None
    assert record["failures"][0] == 1
    assert explorer.pending_choice_record is None


def test_entry_escape_moves_away_from_arrival_edge():
    explorer = Run6Explorer()
    source = sample("room_a", x=296.0, y=120.0)
    destination = sample(
        "room_b",
        x=16.0,
        y=120.0,
        transition_from_room="room_a",
        transition_from_x=296.0,
        transition_from_y=120.0,
        transition_from_facing="right",
    )
    explorer._observe_room(source)
    explorer._observe_room(destination)

    action = explorer.choose(
        observation(1),
        perception(GameState.OVERWORLD),
        destination,
    )

    assert action.name == "right"
    assert "clear arrival portal" in explorer.reason
    assert explorer.entry_escape_moves == 1
