from PIL import Image, ImageDraw

from deltarune_agent.perception import GameState, VisualStateDetector


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
