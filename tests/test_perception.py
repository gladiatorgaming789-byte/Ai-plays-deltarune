from PIL import Image, ImageDraw

from deltarune_agent.perception import (
    CutsceneTracker,
    GameState,
    Perception,
    VisualFeatures,
    VisualStateDetector,
    looks_like_dialogue_choice,
)
from deltarune_agent.telemetry import TelemetrySample


def _draw_option_marker(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    for dx, dy in (
        (2, 0), (2, 1), (0, 2), (1, 2), (2, 2), (3, 2), (4, 2), (2, 3), (2, 4)
    ):
        draw.point((x + dx, y + dy), fill="white")


def test_detects_repeated_dialogue_option_markers():
    frame = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(frame)
    _draw_option_marker(draw, 28, 178)
    _draw_option_marker(draw, 28, 205)

    assert looks_like_dialogue_choice(frame)


def test_ordinary_dialogue_marker_is_not_a_choice():
    frame = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(frame)
    _draw_option_marker(draw, 28, 178)

    assert not looks_like_dialogue_choice(frame)


def test_detects_dialogue_box():
    image = Image.new("RGB", (320, 180), (40, 90, 120))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 112, 309, 173), fill="black", outline="white", width=3)
    draw.rectangle((30, 130, 180, 135), fill="white")
    result = VisualStateDetector().classify(image)
    assert result.state is GameState.DIALOGUE


def test_detects_structural_battle_arena_without_a_soul_color_signal():
    image = Image.new("RGB", (320, 180), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 90, 240, 170), outline="white", width=3)
    draw.rectangle((150, 135, 170, 155), fill=(80, 130, 255))
    detector = VisualStateDetector()
    detector.classify(image)
    result = detector.classify(image)
    assert result.state is GameState.BATTLE


def test_detects_plain_overworld():
    image = Image.new("RGB", (320, 180), (45, 100, 140))
    result = VisualStateDetector().classify(image)
    assert result.state is GameState.OVERWORLD


def test_red_scenery_near_screen_edge_is_not_a_battle():
    image = Image.new("RGB", (320, 180), (210, 110, 55))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 112, 319, 179), fill=(80, 45, 30))
    draw.rectangle((270, 130, 300, 160), fill=(245, 20, 20))

    result = VisualStateDetector().classify(image)

    assert result.state is not GameState.BATTLE


def test_dark_scene_with_red_center_but_no_arena_is_not_a_battle():
    image = Image.new("RGB", (320, 180), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    draw.rectangle((145, 125, 175, 155), fill=(255, 0, 0))

    result = VisualStateDetector().classify(image)

    assert result.state is not GameState.BATTLE


def test_bright_window_rectangle_is_not_a_battle_arena():
    image = Image.new("RGB", (320, 180), (40, 35, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 45, 220, 125), outline="white", width=3, fill=(245, 220, 130))

    result = VisualStateDetector().classify(image)

    assert result.state is not GameState.BATTLE


def test_missing_player_data_alone_does_not_create_a_cutscene():
    features = VisualFeatures(0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    writer = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        40,
        121,
        "obj_writer",
        0,
    )

    result = CutsceneTracker().update(dialogue, writer)

    assert result.state is GameState.DIALOGUE


def test_sustained_control_lock_detects_cutscene_with_player_still_present():
    features = VisualFeatures(0, 0, 0, 0, 0)
    overworld = Perception(GameState.OVERWORLD, 0.80, features, "visual+telemetry")
    player = TelemetrySample(
        "overworld",
        1,
        "room_test",
        100,
        120,
        "obj_mainchara",
        0,
        player_x=100,
        player_y=120,
        interaction_state=1,
        player_controlled=False,
    )
    tracker = CutsceneTracker()
    result = overworld
    for _ in range(tracker.CONTROL_LOCK_THRESHOLD):
        result = tracker.update(overworld, player)

    assert result.state is GameState.CUTSCENE
    assert result.source == "telemetry-control"


def test_sustained_dialogue_and_following_packet_gap_remain_a_cutscene():
    features = VisualFeatures(0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    writer = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        40,
        121,
        "obj_writer",
        0,
        player_x=100,
        player_y=120,
    )
    tracker = CutsceneTracker()
    result = dialogue
    for _ in range(tracker.DIALOGUE_THRESHOLD):
        result = tracker.update(dialogue, writer)

    assert result.state is GameState.CUTSCENE
    assert result.source == "automatic-dialogue-sequence"
    continued = tracker.update(
        Perception(GameState.OVERWORLD, 0.58, features),
        None,
        visual_valid=False,
    )
    assert continued.state is GameState.CUTSCENE
    assert continued.source == "cutscene-continuity"


def test_deliberately_started_object_dialogue_does_not_become_a_cutscene():
    features = VisualFeatures(0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    writer = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        40,
        121,
        "obj_writer",
        0,
        player_x=100,
        player_y=120,
        player_controlled=False,
    )
    tracker = CutsceneTracker()
    tracker.note_action("confirm", "blocked up; try interaction")

    result = dialogue
    for _ in range(tracker.DIALOGUE_THRESHOLD + 10):
        result = tracker.update(dialogue, writer)

    assert result.state is GameState.DIALOGUE
    assert not tracker.cutscene_active


def test_dialogue_packet_gap_preserves_deliberate_interaction_provenance():
    features = VisualFeatures(0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    unknown = Perception(GameState.UNKNOWN, 0.0, features, "stale-capture")
    writer = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        40,
        121,
        "obj_writer",
        0,
        player_x=100,
        player_y=120,
        player_controlled=False,
    )
    tracker = CutsceneTracker()
    tracker.note_action("confirm", "blocked up; try interaction")
    for _ in range(tracker.DIALOGUE_THRESHOLD + 2):
        result = tracker.update(dialogue, writer)
        tracker.note_action("confirm", "advance dialogue")

    assert result.state is GameState.DIALOGUE
    tracker.update(unknown, None, visual_valid=False)
    assert tracker.in_dialogue
    assert tracker.dialogue_started_by_interaction

    resumed = tracker.update(dialogue, writer)

    assert resumed.state is GameState.DIALOGUE
    assert not tracker.cutscene_active


def test_long_non_dialogue_gap_expires_old_interaction_provenance():
    features = VisualFeatures(0, 0, 0, 0, 0)
    dialogue = Perception(GameState.DIALOGUE, 0.99, features, "telemetry")
    unknown = Perception(GameState.UNKNOWN, 0.0, features, "stale-capture")
    writer = TelemetrySample(
        "dialogue",
        1,
        "room_test",
        40,
        121,
        "obj_writer",
        0,
        player_x=100,
        player_y=120,
    )
    tracker = CutsceneTracker()
    tracker.note_action("confirm", "blocked up; try interaction")
    tracker.update(dialogue, writer)

    for _ in range(tracker.TELEMETRY_GAP_GRACE + 1):
        tracker.update(unknown, None, visual_valid=False)

    assert not tracker.in_dialogue
    assert not tracker.dialogue_started_by_interaction
    assert tracker.dialogue_steps == 0
