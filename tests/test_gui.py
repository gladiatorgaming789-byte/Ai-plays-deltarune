import json
from pathlib import Path
from tempfile import TemporaryDirectory

from deltarune_agent.gui import WallMapModel
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
    assert build_parser().parse_args(["gui"]).command == "gui"


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
    assert any(key[2] == "room_test" for key in model.rooms["room_next"].warps)


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
