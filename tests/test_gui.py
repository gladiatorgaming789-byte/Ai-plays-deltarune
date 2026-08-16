import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from deltarune_agent.gui import (
    MapTransform,
    RoomMap,
    WallMapModel,
    decision_parts,
    format_ai_decision,
    format_speed_status,
    format_telemetry_event,
    warp_role_badge,
    visual_guess_entries,
)
from deltarune_agent.runner import build_parser


def _event(x=160, y=128, reason="", mode="overworld", player=None):
    telemetry = {
        "mode": mode,
        "room_id": 2,
        "room_name": "room_test",
        "x": x,
        "y": y,
        "player_x": player[0] if player else None,
        "player_y": player[1] if player else None,
    }
    return {"step": 1, "reason": reason, "telemetry": telemetry}


def test_wall_map_loads_persistent_cells_paths_and_blocks():
    data = {
        "cells": [{"room": "room_test", "x": 1, "y": 2, "visits": 1}],
        "open_edges": [
            {
                "room": "room_test",
                "from_x": 1,
                "from_y": 2,
                "direction": "right",
                "to_x": 2,
                "to_y": 2,
            }
        ],
        "blocked_edges": [
            {
                "room": "room_test",
                "x": 2,
                "y": 2,
                "direction": "down",
                "failures": 1,
            }
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model = WallMapModel()
        model.load_memory(path)

    room = model.rooms["room_test"]
    assert (1, 2) in room.cells
    assert (1, 2, 2, 2) in room.open_edges
    assert (2, 2, "down") in room.blocked_edges


def test_wall_map_updates_path_and_block_from_live_event():
    model = WallMapModel()
    model.update(_event(160, 128))
    moved = _event(176, 128, "learned obstacle up; turn right")
    moved["map_updates"] = [
        {
            "type": "open_edge",
            "room": "room_test",
            "from_cell": [20, 16],
            "to_cell": [21, 16],
        },
        {
            "type": "open_edge",
            "room": "room_test",
            "from_cell": [21, 16],
            "to_cell": [22, 16],
        },
        {
            "type": "blocked",
            "room": "room_test",
            "cell": [22, 16],
            "direction": "up",
            "failures": 1,
        },
    ]
    model.update(moved)

    room = model.rooms["room_test"]
    assert (20, 16) in room.cells
    assert (22, 16) in room.cells
    assert (20, 16, 21, 16) in room.open_edges
    assert (21, 16, 22, 16) in room.open_edges
    assert (22, 16, "up") in room.blocked_edges
    assert model.current_cell == (22, 16)


def test_dialogue_event_maps_player_position_not_writer_position():
    model = WallMapModel()
    model.update(_event(29, 170, mode="dialogue", player=(160, 128)))

    assert model.current_cell == (20, 16)
    assert (20, 16) in model.rooms["room_test"].cells


def test_gui_is_available_as_a_controller_command():
    default = build_parser().parse_args(["gui"])
    legacy = build_parser().parse_args(["gui", "--legacy"])

    assert default.command == "gui"
    assert default.legacy is False
    assert legacy.legacy is True


def test_run_speed_defaults_to_auto_and_accepts_manual_override():
    assert build_parser().parse_args(["run"]).speed == "auto"
    assert build_parser().parse_args(["run", "--speed", "10"]).speed == "10"


def test_run_population_training_is_explicit_and_off_by_default():
    default = build_parser().parse_args(["run"])
    configured = build_parser().parse_args(
        ["run", "--training", "--population-size", "9"]
    )
    assert default.training is False
    assert default.population_size == 4
    assert configured.training is True
    assert configured.population_size == 9


@pytest.mark.parametrize("value", ("1", "17"))
def test_run_population_size_rejects_values_outside_safe_range(value: str):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--population-size", value])


def test_speed_status_distinguishes_sync_manual_and_fallback():
    assert format_speed_status(
        {
            "game_multiplier": 2,
            "effective_multiplier": 2,
            "source": "telemetry",
            "synchronized": True,
        }
    ) == "Game: 2x | AI: 2x | synchronized"
    assert "manual override" in format_speed_status(
        {
            "game_multiplier": 2,
            "effective_multiplier": 3,
            "source": "manual",
            "synchronized": False,
        }
    )
    assert "safe 1x fallback" in format_speed_status(
        {
            "game_multiplier": 4,
            "effective_multiplier": 1,
            "source": "safe_fallback",
            "synchronized": False,
        }
    )


def test_transient_unknown_room_does_not_replace_visible_map_room():
    model = WallMapModel()
    model.update(_event())
    unknown = _event(0, 0)
    unknown["telemetry"]["room_name"] = ""

    model.update(unknown)

    assert model.current_room == "room_test"
    assert "unknown" not in model.rooms


def test_detailed_map_updates_interactables_warps_and_wall_corrections():
    model = WallMapModel()
    event = _event()
    event["map_updates"] = [
        {
            "type": "blocked",
            "room": "room_test",
            "cell": [10, 8],
            "direction": "up",
            "failures": 3,
        },
        {
            "type": "interactable",
            "room": "room_test",
            "cell": [11, 8],
            "name": "obj_bookshelf",
            "instance_id": 100123,
        },
        {
            "type": "warp",
            "from_room": "room_test",
            "from_cell": [12, 8],
            "action": "right",
            "to_room": "room_next",
            "to_cell": [1, 8],
            "count": 2,
        },
        {
            "type": "unblocked",
            "room": "room_test",
            "cell": [10, 8],
            "direction": "up",
        },
    ]

    model.update(event)

    room = model.rooms["room_test"]
    assert (10, 8, "up") not in room.blocked_edges
    assert room.interactables[(11, 8)]["status"] == "confirmed"
    assert any(key[2] == "room_next" for key in room.warps)
    assert not model.rooms["room_next"].warps
    assert (1, 8) in model.rooms["room_next"].cells


def test_warp_samples_cluster_without_turning_arrivals_into_exits():
    model = WallMapModel()
    event = _event()
    event["map_updates"] = [
        {
            "type": "warp",
            "from_room": "room_test",
            "from_cell": [18, 24],
            "action": "event",
            "to_room": "room_next",
            "to_cell": [3, 8],
            "count": 1,
        },
        {
            "type": "warp",
            "from_room": "room_test",
            "from_cell": [19, 24],
            "action": "down",
            "to_room": "room_next",
            "to_cell": [3, 9],
            "count": 3,
        },
    ]

    model.update(event)

    assert len(model.rooms["room_test"].warps) == 1
    key, record = next(iter(model.rooms["room_test"].warps.items()))
    assert key == (19, 24, "room_next")
    assert record["action"] == "down"
    assert record["count"] == 4
    assert record["target_cell"] == (3, 9)
    assert not model.rooms["room_next"].warps


def test_ai_decision_output_explains_exit_search_in_plain_language():
    payload = _event()
    payload.update(
        {
            "step": 42,
            "state": "overworld",
            "action": "down",
            "reason": "probe possible room exit down at learned map edge (19,24)",
        }
    )

    category, action, explanation = decision_parts(payload)
    line = format_ai_decision(payload)

    assert category == "EXIT SEARCH"
    assert action == "Move down"
    assert "test whether it changes rooms" in explanation
    assert "Step 0042  |  EXIT SEARCH  |  Move down" in line
    assert "\n  Why: " in line
    assert "\n  Where: " in line
    assert "room_test" not in line
    assert "test at (160, 128)" in line


def test_ai_decision_output_explains_story_objective_search():
    payload = _event()
    payload.update(
        {
            "step": 84,
            "state": "overworld",
            "action": "right",
            "reason": (
                "story search: investigate visible possible exit via mapped path "
                "right toward region (8, 1)"
            ),
        }
    )

    category, action, explanation = decision_parts(payload)

    assert category == "STORY OBJECTIVE"
    assert action == "Move right"
    assert "remembered visual passage" in explanation


def test_ai_decision_output_explains_learned_choice_trial():
    payload = _event(mode="choice")
    payload.update(
        {
            "step": 91,
            "state": "menu",
            "action": "right",
            "reason": "choice trial 3: move selection right",
        }
    )

    category, action, explanation = decision_parts(payload)

    assert category == "CHOICE LEARNING"
    assert action == "Move right"
    assert "observed story progress" in explanation


def test_ai_decision_output_explains_choice_settle_wait():
    payload = _event(mode="choice")
    payload.update(
        {
            "state": "menu",
            "action": "wait",
            "reason": "wait for choice result to settle",
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "CHOICE CHECK"
    assert "already confirmed" in explanation


def test_ai_decision_output_explains_stale_choice_wait():
    payload = _event(mode="dialogue")
    payload.update(
        {
            "state": "dialogue",
            "action": "wait",
            "reason": "choice capture stale; wait for a fresh menu frame",
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "CHOICE CHECK"
    assert "wrong response" in explanation


def test_ai_decision_output_explains_exhausted_choice_wait():
    payload = _event(mode="choice")
    payload.update(
        {
            "state": "menu",
            "action": "wait",
            "reason": (
                "choice patterns exhausted; wait for menu state to change"
            ),
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "CHOICE CHECK"
    assert "cycling forever" in explanation


def test_gui_tracks_current_camera_regions_and_unconfirmed_visual_guesses():
    model = WallMapModel()
    event = _event(48, 48)
    event["telemetry"].update(
        {
            "room_width": 128,
            "room_height": 64,
            "camera_x": 0,
            "camera_y": 0,
            "camera_width": 128,
            "camera_height": 64,
        }
    )
    event["map_updates"] = [
        {
            "type": "screen_region",
            "room": "room_test",
            "region": [3, 0],
            "views": 2,
            "interest": 0.72,
            "hypothesis": "possible_exit",
            "inspections": 0,
        }
    ]

    model.update(event)

    assert len(model.current_visible_regions) == 8
    assert ("room_test", 3, 0) in model.current_visible_regions
    assert model.rooms["room_test"].screen_regions[(3, 0)] == {
        "views": 2,
        "interest": 0.72,
        "hypothesis": "possible_exit",
        "inspections": 0,
    }
    assert model.current_camera == ("room_test", 0.0, 0.0, 128.0, 64.0)


def test_map_transform_keeps_scene_regions_navigation_and_clicks_aligned():
    transform = MapTransform(5, 3, 2.5, 10.0, 20.0)

    region_box = transform.region_box((2, 1))
    assert region_box[:2] == transform.cell_boundary((8, 4))
    assert region_box[2:] == transform.cell_boundary((12, 8))
    assert transform.world_point((64, 32)) == region_box[:2]
    assert transform.canvas_cell(transform.cell_center((10, 6))) == (10, 6)


def test_warp_role_badges_are_explicit_and_unknown_is_safe():
    assert warp_role_badge("progression")[0] == "P"
    assert warp_role_badge("likely_optional")[0] == "O"
    assert warp_role_badge("return/backtrack")[0] == "R"
    assert warp_role_badge("not-a-role")[0] == "?"


def test_gui_loads_canonical_portal_extent_and_observed_role(tmp_path: Path):
    data = {
        "version": 3,
        "cell_size": 8,
        "warp_portals": [
            {
                "id": "portal_test",
                "from_room": "room_a",
                "to_room": "room_b",
                "action": "down",
                "role": "progression",
                "confidence": 0.92,
                "basis": ["scripted sequence followed the crossing"],
                "crossings": 3,
                "source_footprint": {
                    "center": [11, 14],
                    "bounds": [10, 14, 12, 14],
                },
                "arrival_footprint": {
                    "center": [5, 2],
                    "bounds": [5, 2, 5, 2],
                },
                "aperture": {"axis": "horizontal", "span_cells": 3},
            }
        ],
    }
    path = tmp_path / "navigation.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    model = WallMapModel()

    model.load_memory(path)

    record = model.rooms["room_a"].warps[(11, 14, "room_b")]
    assert record["portal_id"] == "portal_test"
    assert record["role"] == "progression"
    assert record["role_confidence"] == 0.92
    assert record["source_footprint"]["bounds"] == [10, 14, 12, 14]


def test_live_warp_role_update_changes_existing_portal_without_new_marker():
    model = WallMapModel()
    event = _event()
    event["map_updates"] = [
        {
            "type": "warp",
            "portal_id": "portal_test",
            "from_room": "room_a",
            "from_cell": [11, 14],
            "to_room": "room_b",
            "to_cell": [5, 2],
            "action": "down",
            "count": 1,
            "role": "new_area",
        }
    ]
    model.update(event)
    role_update = _event()
    role_update["map_updates"] = [
        {
            "type": "warp_role",
            "portal_id": "portal_test",
            "role": "progression",
            "role_confidence": 0.9,
            "role_basis": ["observed scripted sequence"],
            "crossings": 1,
        }
    ]

    model.update(role_update)

    assert len(model.rooms["room_a"].warps) == 1
    record = next(iter(model.rooms["room_a"].warps.values()))
    assert record["role"] == "progression"
    assert record["role_basis"] == ["observed scripted sequence"]


def test_player_marker_uses_kris_bounds_only_for_overworld_packets():
    model = WallMapModel()
    overworld = _event(48, 64)
    overworld["telemetry"].update(
        {
            "bbox_left": 42,
            "bbox_top": 54,
            "bbox_right": 58,
            "bbox_bottom": 76,
        }
    )
    model.update(overworld)
    assert model.current_display_position == (50.0, 76.0)

    dialogue = _event(0, 0, mode="dialogue", player=(52, 68))
    dialogue["telemetry"].update(
        {
            # These are the writer object's bounds, not Kris's bounds.
            "bbox_left": 180,
            "bbox_top": 120,
            "bbox_right": 240,
            "bbox_bottom": 210,
        }
    )
    model.update(dialogue)
    assert model.current_world_position == (52.0, 68.0)
    assert model.current_display_position == (52.0, 68.0)


def test_v9_player_foot_drives_both_marker_and_navigation_cell():
    model = WallMapModel()
    event = _event(48, 64)
    event["telemetry"].update(
        {"player_foot_x": 71, "player_foot_y": 87}
    )

    model.update(event)

    assert model.current_world_position == (48.0, 64.0)
    assert model.current_display_position == (71.0, 87.0)
    assert model.current_cell == (8, 10)


def test_specific_visual_guesses_are_grouped_and_keep_story_retry_status():
    room = RoomMap()
    room.screen_regions[(2, 0)] = {
        "hypothesis": "possible_exit",
        "guess_label": "Possible passage at top",
        "guess_confidence": 0.56,
        "evidence_summary": "wide bright feature; near the top room edge",
        "edge_hint": "top",
        "anchor_cell": [10, 1],
        "feature_box_world": [88, 4, 96, 20],
        "inspections": 0,
    }
    room.screen_regions[(3, 0)] = {
        "hypothesis": "possible_exit",
        "guess_label": "Possible passage at top",
        "guess_confidence": 0.72,
        "evidence_summary": "wide detailed feature; near the top room edge",
        "edge_hint": "top",
        "anchor_cell": [13, 1],
        "feature_box_world": [96, 5, 104, 21],
        "inspections": 0,
    }
    room.screen_regions[(5, 2)] = {
        "hypothesis": "possible_character",
        "guess_label": "Possible stationary character",
        "guess_confidence": 0.81,
        "evidence_summary": "compact 2-cell obstruction approached from left/up",
        "anchor_cell": [21, 10],
        "inspections": 2,
    }

    guesses = visual_guess_entries(
        "room_test",
        room,
        {("room_test", 3, 0)},
    )

    assert [guess.marker for guess in guesses] == ["C1", "E1"]
    assert guesses[0].anchor_cell == (21.0, 10.0)
    assert guesses[0].status == "proposed; 2 completed tests"
    assert guesses[1].regions == ((2, 0), (3, 0))
    assert guesses[1].feature_box_world == (88.0, 4.0, 104.0, 21.0)
    assert guesses[1].anchor_world == (96.0, 12.5)
    assert guesses[1].status.endswith("visible now")


def test_adjacent_legacy_exit_guesses_without_shared_feature_stay_separate():
    room = RoomMap()
    for region in ((2, 0), (3, 0)):
        room.screen_regions[region] = {
            "hypothesis": "possible_exit",
            "interest": 0.6,
            "inspections": 0,
        }

    guesses = visual_guess_entries("room_test", room)

    assert [guess.regions for guess in guesses] == [((2, 0),), ((3, 0),)]


def test_sparse_live_guess_updates_preserve_specific_evidence_and_active_target():
    model = WallMapModel()
    event = _event(48, 48)
    event["decision_context"] = {
        "kind": "visual_guess",
        "id": "room_test@3,0",
        "room": "room_test",
        "region": [3, 0],
    }
    event["map_updates"] = [
        {
            "type": "screen_region",
            "room": "room_test",
            "region": [3, 0],
            "views": 4,
            "interest": 0.7,
            "hypothesis": "possible_exit",
            "inspections": 0,
            "guess_label": "Possible passage at right",
            "evidence_kind": "visual_edge_landmark",
            "evidence_summary": "tall detailed feature near the right edge",
            "guess_confidence": 0.68,
            "anchor_cell": [14, 2],
            "feature_box_world": [108, 8, 124, 28],
        }
    ]
    model.update(event)

    sparse = _event(48, 48)
    sparse["decision_context"] = event["decision_context"]
    sparse["map_updates"] = [
        {
            "type": "screen_region",
            "room": "room_test",
            "region": [3, 0],
            "inspections": 1,
        }
    ]
    model.update(sparse)

    record = model.rooms["room_test"].screen_regions[(3, 0)]
    assert record["guess_label"] == "Possible passage at right"
    assert record["evidence_summary"].startswith("tall detailed")
    assert record["anchor_cell"] == [14, 2]
    assert record["feature_box_world"] == [108, 8, 124, 28]
    assert record["inspections"] == 1
    assert model.current_guess_region == ("room_test", 3, 0)
    assert model.current_guess_id == "room_test@3,0"


def test_gui_loads_specific_guess_evidence_from_persistent_memory():
    data = {
        "version": 2,
        "cell_size": 8,
        "screen_regions": [
            {
                "room": "room_test",
                "region_x": 3,
                "region_y": 0,
                "views": 5,
                "interest": 0.74,
                "hypothesis": "possible_exit",
                "inspections": 1,
                "guess_label": "Possible passage at right",
                "evidence_kind": "visual_edge_landmark",
                "evidence_summary": "tall bright feature near the right edge",
                "guess_confidence": 0.66,
                "anchor_cell": [14, 2],
                "focus_world": [116, 18],
                "feature_box_world": [108, 8, 124, 28],
                "edge_hint": "right",
            }
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model = WallMapModel()
        model.load_memory(path)

    record = model.rooms["room_test"].screen_regions[(3, 0)]
    assert record["guess_label"] == "Possible passage at right"
    assert record["evidence_kind"] == "visual_edge_landmark"
    assert record["guess_confidence"] == 0.66
    assert record["anchor_cell"] == [14, 2]
    assert record["feature_box_world"] == [108, 8, 124, 28]


def test_decision_explanation_names_the_exact_visual_lead_and_evidence():
    payload = _event(reason="investigate possible exit seen on screen")
    payload.update(
        {
            "state": "overworld",
            "action": "right",
            "decision_context": {
                "kind": "visual_guess",
                "label": "Possible passage at right",
                "anchor_cell": [14, 2],
                "evidence": "tall bright feature near the right room edge",
                "confidence": 0.68,
            },
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "VISUAL GUESS"
    assert "Possible passage at right" in explanation
    assert "map cell (14, 2)" in explanation
    assert "tall bright feature" in explanation
    assert "68% evidence score" in explanation


def test_camera_view_updates_during_cutscene_without_player_coordinates():
    model = WallMapModel()
    initial = _event(48, 48)
    initial["telemetry"].update(
        {
            "room_width": 320,
            "room_height": 420,
            "camera_x": 0,
            "camera_y": 40,
            "camera_width": 320,
            "camera_height": 240,
        }
    )
    model.update(initial)
    cutscene = _event(0, 0, mode="dialogue", player=None)
    cutscene["telemetry"].update(
        {
            "room_width": 320,
            "room_height": 420,
            "camera_x": 0,
            "camera_y": 80,
            "camera_width": 320,
            "camera_height": 240,
        }
    )

    model.update(cutscene)

    assert model.current_cell == (6, 6)
    assert model.current_camera == ("room_test", 0.0, 80.0, 320.0, 240.0)
    assert model.current_visible_regions


def test_telemetry_output_uses_plain_labels_and_cutscene_state():
    payload = _event(0, 0, mode="dialogue", player=(160, 128))
    payload.update({"step": 42, "state": "cutscene"})
    payload["telemetry"].update(
        {
            "facing_direction": "up",
            "camera_x": 0,
            "camera_y": 80,
            "camera_width": 320,
            "camera_height": 240,
        }
    )

    text = format_telemetry_event(payload)

    assert "Step 0042  |  CUTSCENE  |  test" in text
    assert "Kris: (160, 128)" in text
    assert "Facing: up" in text
    assert "Camera: (0, 80) 320x240" in text


def test_telemetry_output_explains_v9_packet_motion_and_player_geometry():
    payload = _event(80, 72)
    payload["telemetry"].update(
        {
            "version": 9,
            "packet_sequence": 44,
            "packet_parts": ["core", "motion", "render"],
            "player_foot_x": 88,
            "player_foot_y": 96,
            "sample_delta_x": 0,
            "sample_delta_y": 4,
            "sample_interval_ms": 16.7,
            "hspeed": 0,
            "vspeed": 4,
            "player_sprite_name": "spr_krisd",
            "player_bbox_left": 80,
            "player_bbox_top": 72,
            "player_bbox_right": 96,
            "player_bbox_bottom": 96,
            "image_index": 2,
            "fps": 30,
        }
    )

    text = format_telemetry_event(payload)

    assert "Packet: v9  #44" in text
    assert "Parts: core, motion, render" in text
    assert "Motion sample: delta (0.0, 4.0) in 16.7 ms" in text
    assert "Render: spr_krisd" in text
    assert "player bounds (80, 72)-(96, 96)" in text


def test_gui_loads_and_updates_persistent_remembered_room_tiles():
    with TemporaryDirectory() as directory:
        root = Path(directory) / "room_views"
        tile_path = root / "room-test" / "2_1.png"
        tile_path.parent.mkdir(parents=True)
        from PIL import Image

        Image.new("RGBA", (32, 32), (20, 40, 60, 255)).save(tile_path)
        index = {
            "version": 1,
            "region_pixels": 32,
            "rooms": {
                "room_test": {
                    "tiles": {
                        "2,1": {
                            "region_x": 2,
                            "region_y": 1,
                            "path": "room-test/2_1.png",
                            "coverage": 1.0,
                            "last_step": 10,
                        }
                    }
                }
            },
        }
        (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        model = WallMapModel()
        model.load_room_views(root / "index.json")

        assert model.rooms["room_test"].view_tiles[(2, 1)]["path"] == str(
            tile_path.resolve()
        )

        other_path = root / "room-test" / "3_1.png"
        Image.new("RGBA", (32, 32), (80, 40, 20, 255)).save(other_path)
        event = _event()
        event["map_updates"] = [
            {
                "type": "room_view_tile",
                "room": "room_test",
                "region": [3, 1],
                "path": str(other_path),
                "coverage": 0.75,
                "last_step": 15,
            }
        ]
        model.update(event)

        assert model.rooms["room_test"].view_tiles[(3, 1)]["coverage"] == 0.75


def test_ai_decision_output_explains_visual_guesses_as_unconfirmed():
    payload = _event()
    payload.update(
        {
            "step": 17,
            "state": "overworld",
            "action": "right",
            "reason": (
                "investigate possible interactable seen on screen: "
                "move right toward region (3, 1)"
            ),
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "OBJECT GUESS"
    assert "one collision side" in explanation


def test_ai_decision_output_explains_current_character_guess():
    payload = _event()
    payload.update(
        {
            "state": "overworld",
            "action": "left",
            "reason": (
                "investigate possible character seen on screen: "
                "move left toward region (2, 1)"
            ),
        }
    )

    category, _action, explanation = decision_parts(payload)

    assert category == "VISUAL GUESS"
    assert "compact obstruction" in explanation
    assert "unconfirmed" in explanation


def test_gui_discards_old_character_guess_without_map_topology():
    data = {
        "version": 2,
        "cell_size": 8,
        "cells": [{"room": "room_test", "x": 1, "y": 1, "visits": 1}],
        "screen_regions": [
            {
                "room": "room_test",
                "region_x": 0,
                "region_y": 0,
                "views": 8,
                "interest": 0.8,
                "hypothesis": "possible_character",
                "inspections": 0,
            }
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model = WallMapModel()
        model.load_memory(path)

    assert model.rooms["room_test"].screen_regions[(0, 0)]["hypothesis"] is None


def test_gui_keeps_character_guess_supported_from_two_sides():
    data = {
        "version": 2,
        "cell_size": 8,
        "cells": [
            {"room": "room_test", "x": 0, "y": 1, "visits": 1},
            {"room": "room_test", "x": 1, "y": 0, "visits": 1},
        ],
        "blocked_edges": [
            {
                "room": "room_test",
                "x": 0,
                "y": 1,
                "direction": "right",
                "failures": 3,
            },
            {
                "room": "room_test",
                "x": 1,
                "y": 0,
                "direction": "down",
                "failures": 3,
            },
        ],
        "screen_regions": [
            {
                "room": "room_test",
                "region_x": 0,
                "region_y": 0,
                "views": 3,
                "interest": 0.2,
                "hypothesis": "possible_character",
                "inspections": 0,
            }
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model = WallMapModel()
        model.load_memory(path)

    assert (
        model.rooms["room_test"].screen_regions[(0, 0)]["hypothesis"]
        == "possible_character"
    )


def test_tentative_interaction_probe_is_not_shown_on_learned_map():
    model = WallMapModel()
    event = _event(reason="blocked right; try interaction")
    event["map_updates"] = [
        {
            "type": "interaction_probe",
            "room": "room_test",
            "cell": [11, 8],
            "name": "undiscovered object",
        }
    ]

    model.update(event)

    assert not model.rooms["room_test"].interactables
    assert not model.rooms["room_test"].blocked_edges


def test_gui_migrates_old_map_to_finer_adjacent_paths():
    data = {
        "version": 1,
        "cells": [{"room": "room_test", "x": 1, "y": 2, "visits": 1}],
        "open_edges": [
            {
                "room": "room_test",
                "from_x": 1,
                "from_y": 2,
                "direction": "right",
                "to_x": 3,
                "to_y": 2,
            }
        ],
    }
    with TemporaryDirectory() as directory:
        path = Path(directory) / "navigation.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        model = WallMapModel()
        model.load_memory(path)

    room = model.rooms["room_test"]
    assert (2, 4) in room.cells
    assert (2, 4, 3, 4) in room.open_edges
    assert (5, 4, 6, 4) in room.open_edges
    assert (2, 4, 6, 4) not in room.open_edges


def test_room_map_tracks_repeat_visits_and_current_facing():
    model = WallMapModel()
    event = _event(160, 128)
    event["telemetry"]["facing_direction"] = "right"

    model.update(event)
    model.update(event)

    assert model.rooms["room_test"].visits[(20, 16)] == 2
    assert model.current_direction == "right"
