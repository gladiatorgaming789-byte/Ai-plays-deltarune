from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageEnhance, ImageTk

from .policy import (
    CHARACTER_APPROACH_DIRECTIONS,
    CHARACTER_SINGLE_APPROACH_MAX_TARGETS,
    CHARACTER_SINGLE_APPROACH_MIN_INTEREST,
    CHARACTER_SINGLE_APPROACH_MIN_VIEWS,
)
from .room_view import room_view_image_is_usable
from .screen_regions import visible_region_coordinates
from .window import (
    find_window,
    focus_window,
    is_window_foreground,
    remember_window,
)
from .world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


DIRECTION_VECTORS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
EVENT_PREFIX = "AI_GUI_EVENT\t"
WARP_CLUSTER_RADIUS = 2

ACTION_LABELS = {
    "up": "Move up",
    "down": "Move down",
    "left": "Move left",
    "right": "Move right",
    "confirm": "Press Z",
    "cancel": "Press X",
    "menu": "Open menu",
    "wait": "Wait",
}


def _decision_explanation(reason: str, state: str) -> tuple[str, str]:
    reason_lower = reason.casefold()
    if "story search:" in reason_lower:
        if "retry another response" in reason_lower:
            return (
                "CHOICE RETRY",
                "The previous response produced no observed story progress, so the AI is returning to the same person to try the next untested option.",
            )
        if "possible character" in reason_lower:
            return (
                "STORY OBJECTIVE",
                "Story progress has stalled, so the AI is routing to a learned side of a compact static character lead and will face it to interact.",
            )
        if "visible possible exit" in reason_lower:
            return (
                "STORY OBJECTIVE",
                "Story progress has stalled, so a remembered visual passage is being tested through the learned room map.",
            )
        if "room edge" in reason_lower:
            return (
                "STORY OBJECTIVE",
                "Story progress has stalled, so an untested edge of the learned room is being checked for a way forward.",
            )
        return (
            "STORY OBJECTIVE",
            "Story progress has stalled, so the best untested visible lead is being investigated.",
        )
    if "choice trial" in reason_lower:
        return (
            "CHOICE LEARNING",
            "Trying a remembered response pattern; outcomes that cause observed story progress will be preferred next time.",
        )
    if "wait for choice result to settle" in reason_lower:
        return (
            "CHOICE CHECK",
            "The AI already confirmed a response and is briefly waiting to see whether the menu closes or the game changes.",
        )
    if "choice capture stale" in reason_lower:
        return (
            "CHOICE CHECK",
            "The choice is still active, but the current game image is stale, so the AI is waiting instead of moving or confirming the wrong response.",
        )
    if "choice patterns exhausted" in reason_lower:
        return (
            "CHOICE CHECK",
            "Every safe response pattern for this menu has been tested, so the AI is waiting for the game state to change instead of cycling forever.",
        )
    if "search room edge" in reason_lower:
        return "EXIT SEARCH", "Following an observed path continuation toward a possible room transition; it does not need to look like a door."
    if "probe possible room exit" in reason_lower:
        return "EXIT SEARCH", "Pressing through a learned map edge to test whether it changes rooms."
    if "investigate possible exit" in reason_lower:
        return "VISUAL GUESS", "Moving toward an on-screen region that might lead out of the room."
    if "investigate possible character" in reason_lower:
        return (
            "VISUAL GUESS",
            "Moving toward a compact obstruction currently visible on screen that might be a character; it remains unconfirmed until an interaction has a result.",
        )
    if "investigate possible interactable" in reason_lower:
        return "VISUAL GUESS", "Moving toward visually distinctive scenery to test it through play."
    if "follow learned warp" in reason_lower:
        destination = "another room"
        marker = "follow learned warp to "
        if marker in reason_lower:
            destination = reason[len(marker) :].split(" via ", 1)[0].removeprefix("room_")
        return "KNOWN EXIT", f"Following a previously discovered exit toward {destination}."
    if "loop" in reason_lower and "detected" in reason_lower:
        return "LOOP RECOVERY", "The recent movement pattern repeated, so a different route was chosen."
    if "route to mapped frontier" in reason_lower:
        return "EXPLORING", "Following a known path toward an unexplored part of this room."
    if "explore new edge" in reason_lower:
        return "EXPLORING", "Testing a new direction in this part of the room."
    if "no reachable frontier" in reason_lower:
        return "SEARCHING", "No useful mapped route remains, so a different local direction is being tested."
    if "continue clear path" in reason_lower:
        return "MOVING", "Continuing briefly along the current clear path for smoother movement."
    if "checking blockage" in reason_lower or "await fresh telemetry" in reason_lower:
        return "PATH CHECK", "Waiting for enough fresh position evidence before remembering a wall."
    if "align facing" in reason_lower:
        return "INTERACTION", "Waiting for telemetry to confirm Kris is facing the intended character side before pressing Z."
    if "try interaction" in reason_lower:
        return "INTERACTION", "Movement stopped, so the object ahead is being checked once."
    if "interaction completed" in reason_lower:
        return "INTERACTION", "The interaction finished; testing whether it opened the way forward."
    if "blocked" in reason_lower or "learned obstacle" in reason_lower:
        return "WALL AVOIDANCE", "A remembered obstruction is being avoided."
    if "input not reflected" in reason_lower or "input remained frozen" in reason_lower:
        return "INPUT CHECK", "The last key was not reflected in telemetry, so input is being retried or changed."
    if "advance dialogue" in reason_lower:
        return "DIALOGUE", "Advancing the current dialogue."
    if "cutscene" in reason_lower or state == "cutscene":
        return "CUTSCENE", "Advancing scripted dialogue while movement stays suspended."
    if "menu" in reason_lower:
        return "MENU", "Responding to the current menu."
    if "battle" in reason_lower or state == "battle":
        return "BATTLE", "Using the current deterministic battle response."
    if "unknown state" in reason_lower or state == "unknown":
        return "WAITING", "The game state is temporarily unclear, so movement is paused safely."
    if "vision-only" in reason_lower:
        return "VISION ONLY", "Telemetry is unavailable, so basic visual exploration is being used."
    return state.upper() or "DECISION", reason or "Choosing the next action from current observations."


def decision_parts(payload: dict[str, object]) -> tuple[str, str, str]:
    state = str(payload.get("state") or "unknown")
    action = str(payload.get("action") or "wait")
    reason = str(payload.get("reason") or "")
    category, explanation = _decision_explanation(reason, state)
    return category, ACTION_LABELS.get(action, action.replace("_", " ").title()), explanation


def format_ai_decision(payload: dict[str, object]) -> str:
    category, action_label, explanation = decision_parts(payload)
    telemetry = payload.get("telemetry")
    location = "Unknown location"
    if isinstance(telemetry, dict):
        room = str(telemetry.get("room_name") or telemetry.get("room_id") or "transition")
        room = room.removeprefix("room_")
        if telemetry.get("mode") == "overworld":
            x = telemetry.get("x")
            y = telemetry.get("y")
        else:
            x = telemetry.get("player_x")
            y = telemetry.get("player_y")
        if x is not None and y is not None:
            try:
                location = f"{room} at ({round(float(x))}, {round(float(y))})"
            except (TypeError, ValueError):
                location = room
        else:
            location = room
    return (
        f"Step {int(payload.get('step') or 0):04d}  |  {category}  |  {action_label}\n"
        f"  Why: {explanation}\n"
        f"  Where: {location}"
    )


def format_telemetry_event(payload: dict[str, object]) -> str:
    telemetry = payload.get("telemetry")
    if not isinstance(telemetry, dict):
        return ""
    room = str(
        telemetry.get("room_name")
        or telemetry.get("room_id")
        or "room transition"
    ).removeprefix("room_")
    state = str(payload.get("state") or telemetry.get("mode") or "unknown").upper()
    if telemetry.get("mode") == "overworld":
        x = telemetry.get("x")
        y = telemetry.get("y")
    else:
        x = telemetry.get("player_x")
        y = telemetry.get("player_y")
    try:
        position = f"({round(float(x))}, {round(float(y))})"
    except (TypeError, ValueError):
        position = "not reported"
    direction = telemetry.get("facing_direction") or "not reported"
    camera_values = (
        telemetry.get("camera_x"),
        telemetry.get("camera_y"),
        telemetry.get("camera_width"),
        telemetry.get("camera_height"),
    )
    try:
        camera_x, camera_y, camera_width, camera_height = (
            round(float(value)) for value in camera_values
        )
        camera = f"({camera_x}, {camera_y}) {camera_width}x{camera_height}"
    except (TypeError, ValueError):
        camera = "not reported"
    player_controlled = telemetry.get("player_controlled")
    control = (
        "player"
        if player_controlled is True
        else "locked"
        if player_controlled is False
        else "not reported"
    )
    source = str(payload.get("source") or "unknown")
    try:
        confidence = f"{float(payload.get('confidence')):.0%}"
    except (TypeError, ValueError):
        confidence = "not reported"
    scene = "live" if payload.get("visual_valid", True) else "last clean frame"
    return (
        f"Step {int(payload.get('step') or 0):04d}  |  {state}  |  {room}\n"
        f"  Kris: {position}  |  Facing: {direction}  |  Camera: {camera}\n"
        f"  Control gate: {control}  |  Detector: {source} ({confidence})"
        f"  |  Scene: {scene}"
    )

THEMES = {
    "dark": {
        "bg": "#10141d",
        "panel": "#181e2a",
        "panel_alt": "#202838",
        "field": "#0c1119",
        "text": "#e7edf7",
        "muted": "#9ba8bd",
        "border": "#354056",
        "accent": "#4c8dff",
        "accent_active": "#6aa2ff",
        "cell": "#293447",
        "cell_repeat": "#3b4a61",
        "cell_hot": "#65465f",
        "cell_outline": "#46546b",
        "grid": "#1d2634",
        "path": "#52d47d",
        "wall": "#ff626b",
        "interactable": "#f0c94b",
        "interactable_outline": "#8c6c10",
        "warp": "#b184f4",
        "warp_outline": "#7147a9",
        "visible_region": "#42658f",
        "visible_current": "#4c8dff",
        "camera": "#8fc2ff",
        "hypothesis": "#54d6d0",
        "player": "#55a7ff",
        "player_outline": "#d8ecff",
    },
    "light": {
        "bg": "#eef1f6",
        "panel": "#ffffff",
        "panel_alt": "#e6ebf3",
        "field": "#ffffff",
        "text": "#182033",
        "muted": "#5f6b7c",
        "border": "#aeb8c8",
        "accent": "#2868d8",
        "accent_active": "#1d56b8",
        "cell": "#dfe5ed",
        "cell_repeat": "#c9d6e6",
        "cell_hot": "#e4c2cd",
        "cell_outline": "#b9c4d2",
        "grid": "#e5e9ef",
        "path": "#248847",
        "wall": "#d73542",
        "interactable": "#d8aa16",
        "interactable_outline": "#765b06",
        "warp": "#8250c4",
        "warp_outline": "#4d287e",
        "visible_region": "#91a8c4",
        "visible_current": "#2868d8",
        "camera": "#0e5fca",
        "hypothesis": "#087f7a",
        "player": "#2377df",
        "player_outline": "#0d3f81",
    },
}


@dataclass
class RoomMap:
    cells: set[tuple[int, int]] = field(default_factory=set)
    visits: dict[tuple[int, int], int] = field(default_factory=dict)
    open_edges: set[tuple[int, int, int, int]] = field(default_factory=set)
    blocked_edges: dict[tuple[int, int, str], int] = field(default_factory=dict)
    interactables: dict[tuple[int, int], dict[str, object]] = field(default_factory=dict)
    screen_regions: dict[tuple[int, int], dict[str, object]] = field(
        default_factory=dict
    )
    view_tiles: dict[tuple[int, int], dict[str, object]] = field(
        default_factory=dict
    )
    # Keys are the representative source cell and destination room. Arrival
    # coordinates are observations attached to the exit, not extra doorways.
    warps: dict[tuple[int, int, str], dict[str, object]] = field(default_factory=dict)


class WallMapModel:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomMap] = {}
        self.current_room: str | None = None
        self.current_cell: tuple[int, int] | None = None
        self.current_direction: str | None = None
        self.current_visible_regions: set[tuple[str, int, int]] = set()
        self.current_camera: tuple[str, float, float, float, float] | None = None

    def room(self, name: str) -> RoomMap:
        return self.rooms.setdefault(name, RoomMap())

    @staticmethod
    def _region_has_character_topology(
        room: RoomMap,
        region: tuple[int, int],
        *,
        views: int,
        interest: float,
    ) -> bool:
        if not any(
            (x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS)
            == region
            for x, y in room.cells
        ):
            return False
        approaches: list[tuple[tuple[int, int], str]] = []
        for (source_x, source_y, direction), failures in room.blocked_edges.items():
            if failures <= 0 or direction not in DIRECTION_VECTORS:
                continue
            dx, dy = DIRECTION_VECTORS[direction]
            target = (source_x + dx, source_y + dy)
            if (
                target[0] // EXPLORATION_REGION_CELLS,
                target[1] // EXPLORATION_REGION_CELLS,
            ) == region:
                approaches.append((target, direction))
        best_directions = 0
        best_targets = 0
        for target, _direction in approaches:
            nearby = [
                (candidate, direction)
                for candidate, direction in approaches
                if max(
                    abs(candidate[0] - target[0]),
                    abs(candidate[1] - target[1]),
                )
                <= 2
            ]
            directions = {direction for _target, direction in nearby}
            targets = {candidate for candidate, _direction in nearby}
            if (len(directions), -len(targets)) > (
                best_directions,
                -best_targets,
            ):
                best_directions = len(directions)
                best_targets = len(targets)
        if (
            best_directions >= CHARACTER_APPROACH_DIRECTIONS
            and best_targets <= 4
        ):
            return True
        return (
            best_directions >= 1
            and best_targets <= CHARACTER_SINGLE_APPROACH_MAX_TARGETS
            and views >= CHARACTER_SINGLE_APPROACH_MIN_VIEWS
            and interest >= CHARACTER_SINGLE_APPROACH_MIN_INTEREST
        )

    def load_memory(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data.get("version")
            if data.get("cell_size") is not None:
                scale = int(data["cell_size"]) // CELL_SIZE
            elif version == 1:
                scale = 2
            else:
                scale = 1

            def coordinate(value: object) -> int:
                return int(value) * scale

            for item in data.get("cells", []):
                room = self.room(str(item["room"]))
                cell = (coordinate(item["x"]), coordinate(item["y"]))
                room.cells.add(cell)
                room.visits[cell] = int(item.get("visits", 1))
            for item in data.get("open_edges", []):
                room = self.room(str(item["room"]))
                source = (coordinate(item["from_x"]), coordinate(item["from_y"]))
                target = (coordinate(item["to_x"]), coordinate(item["to_y"]))
                self._add_open_path(room, source, str(item["direction"]), target)
            for item in data.get("blocked_edges", []):
                self.room(str(item["room"])).blocked_edges[
                    (
                        coordinate(item["x"]),
                        coordinate(item["y"]),
                        str(item["direction"]),
                    )
                ] = int(item.get("failures", 1))
            for item in data.get("interactables", []):
                self.room(str(item["room"])).interactables[
                    (coordinate(item["x"]), coordinate(item["y"]))
                ] = {
                    "name": str(item.get("name") or "interaction"),
                    "status": "confirmed",
                    "instance_id": item.get("instance_id"),
                    "confirmations": int(item.get("confirmations", 1)),
                    "choice_menus": int(item.get("choice_menus", 0)),
                    "classification": str(
                        item.get("classification") or "unknown"
                    ),
                    "usefulness": str(item.get("usefulness") or "unknown"),
                    "last_outcome": str(item.get("last_outcome") or "unknown"),
                    "outcome_counts": dict(item.get("outcome_counts", {}))
                    if isinstance(item.get("outcome_counts"), dict)
                    else {},
                    "approaches": list(item.get("approaches", [])),
                }
            for item in data.get("screen_regions", []):
                hypothesis = item.get("hypothesis")
                if hypothesis == "possible_interactable":
                    hypothesis = None
                room = self.room(str(item["room"]))
                region = (int(item["region_x"]), int(item["region_y"]))
                views = int(item.get("views", 1))
                interest = float(item.get("interest", 0.0))
                if (
                    hypothesis == "possible_character"
                    and not self._region_has_character_topology(
                        room,
                        region,
                        views=views,
                        interest=interest,
                    )
                ):
                    hypothesis = None
                room.screen_regions[region] = {
                    "views": views,
                    "interest": interest,
                    "hypothesis": hypothesis,
                    "inspections": int(item.get("inspections", 0)),
                }
            for item in data.get("warps", []):
                source_room = str(item["from_room"])
                target_room = str(item["to_room"])
                source = (coordinate(item["from_x"]), coordinate(item["from_y"]))
                target = (coordinate(item["to_x"]), coordinate(item["to_y"]))
                action = str(item.get("action") or "event")
                count = int(item.get("count", 1))
                self._add_warp(source_room, source, target_room, target, action, count)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            # The GUI remains usable with live data if old memory is malformed.
            return

    def load_room_views(self, index_path: Path) -> None:
        if not index_path.exists():
            return
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if int(data.get("version", 0)) not in {1, 2}:
                return
            rooms = data.get("rooms")
            if not isinstance(rooms, dict):
                return
            for room_name, room_data in rooms.items():
                if not isinstance(room_data, dict):
                    continue
                tiles = room_data.get("tiles")
                if not isinstance(tiles, dict):
                    continue
                room = self.room(str(room_name))
                for tile in tiles.values():
                    if not isinstance(tile, dict):
                        continue
                    region = (int(tile["region_x"]), int(tile["region_y"]))
                    path = (index_path.parent / str(tile["path"])).resolve()
                    usable = False
                    if path.is_file():
                        try:
                            with Image.open(path) as stored:
                                usable = room_view_image_is_usable(stored)
                        except OSError:
                            pass
                    if usable:
                        room.view_tiles[region] = {
                            "path": str(path),
                            "coverage": float(tile.get("coverage", 1.0)),
                            "pixels_per_world": int(
                                tile.get("pixels_per_world", 1)
                            ),
                            "last_step": int(tile.get("last_step", 0)),
                        }
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return

    @staticmethod
    def _add_open_path(
        room: RoomMap,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        vector = DIRECTION_VECTORS.get(direction)
        if vector is None:
            return
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        forward = delta_x * vector[0] + delta_y * vector[1]
        lateral = abs(delta_x * vector[1] - delta_y * vector[0])
        if forward <= 0 or lateral != 0:
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector[0], current[1] + vector[1])
            room.cells.update((current, following))
            endpoints = (current, following) if current <= following else (following, current)
            room.open_edges.add((*endpoints[0], *endpoints[1]))
            current = following

    def update(self, event: dict) -> None:
        map_updates = [
            update
            for update in event.get("map_updates") or []
            if isinstance(update, dict)
        ]
        for update in map_updates:
            self._apply_map_update(update)
        telemetry = event.get("telemetry")
        if not telemetry:
            return
        raw_room_name = telemetry.get("room_name")
        if not raw_room_name or str(raw_room_name).casefold() == "unknown":
            return
        room_name = str(raw_room_name)
        room_changed = self.current_room != room_name
        self.current_room = room_name
        if room_changed:
            self.current_cell = None
            self.current_direction = None
            self.current_camera = None
        room = self.room(room_name)

        visible = visible_region_coordinates(
            telemetry.get("camera_x"),
            telemetry.get("camera_y"),
            telemetry.get("camera_width"),
            telemetry.get("camera_height"),
            telemetry.get("room_width"),
            telemetry.get("room_height"),
        )
        self.current_visible_regions = {
            (room_name, region_x, region_y) for region_x, region_y in visible
        }
        camera_values = (
            telemetry.get("camera_x"),
            telemetry.get("camera_y"),
            telemetry.get("camera_width"),
            telemetry.get("camera_height"),
        )
        if all(value is not None for value in camera_values):
            try:
                camera_x, camera_y, camera_width, camera_height = (
                    float(value) for value in camera_values
                )
                if camera_width > 0 and camera_height > 0:
                    self.current_camera = (
                        room_name,
                        camera_x,
                        camera_y,
                        camera_width,
                        camera_height,
                    )
            except (TypeError, ValueError):
                pass
        direction = telemetry.get("facing_direction")
        if direction in DIRECTION_VECTORS:
            self.current_direction = str(direction)

        x = telemetry.get("x")
        y = telemetry.get("y")
        if telemetry.get("mode") != "overworld":
            x = telemetry.get("player_x")
            y = telemetry.get("player_y")
        if x is None or y is None:
            return

        cell = (int(float(x) // CELL_SIZE), int(float(y) // CELL_SIZE))
        room.cells.add(cell)
        room.visits[cell] = room.visits.get(cell, 0) + 1
        self.current_cell = cell

    def _apply_map_update(self, update: dict) -> None:
        update_type = str(update.get("type") or "")
        if update_type == "warp":
            self._add_warp(
                str(update["from_room"]),
                tuple(int(value) for value in update["from_cell"]),
                str(update["to_room"]),
                tuple(int(value) for value in update["to_cell"]),
                str(update.get("action") or "event"),
                int(update.get("count", 1)),
            )
            return
        room_name = str(update.get("room") or "")
        if not room_name:
            return
        room = self.room(room_name)
        if update_type == "open_edge":
            source = tuple(int(value) for value in update["from_cell"])
            target = tuple(int(value) for value in update["to_cell"])
            room.cells.update((source, target))
            endpoints = (source, target) if source <= target else (target, source)
            room.open_edges.add((*endpoints[0], *endpoints[1]))
        elif update_type == "blocked":
            cell = tuple(int(value) for value in update["cell"])
            room.blocked_edges[(*cell, str(update["direction"]))] = int(
                update.get("failures", 1)
            )
        elif update_type == "unblocked":
            cell = tuple(int(value) for value in update["cell"])
            room.blocked_edges.pop((*cell, str(update["direction"])), None)
        elif update_type == "interactable":
            cell = tuple(int(value) for value in update["cell"])
            room.cells.add(cell)
            room.interactables[cell] = {
                "name": str(update.get("name") or "interaction"),
                "status": "confirmed",
                "instance_id": update.get("instance_id"),
                "confirmations": int(update.get("confirmations", 1)),
                "choice_menus": int(update.get("choice_menus", 0)),
                "classification": str(
                    update.get("classification") or "unknown"
                ),
                "usefulness": str(update.get("usefulness") or "unknown"),
                "last_outcome": str(update.get("last_outcome") or "unknown"),
                "outcome_counts": dict(update.get("outcome_counts", {}))
                if isinstance(update.get("outcome_counts"), dict)
                else {},
                "approaches": list(update.get("approaches", [])),
            }
        elif update_type == "interaction_outcome":
            cell = tuple(int(value) for value in update["cell"])
            record = room.interactables.setdefault(
                cell,
                {"name": "interaction", "status": "confirmed", "approaches": []},
            )
            record["choice_menus"] = int(update.get("choice_menus", 0))
            record["classification"] = str(
                update.get("classification") or "tested_nonchoice"
            )
            record["usefulness"] = str(update.get("usefulness") or "unknown")
            record["last_outcome"] = str(update.get("last_outcome") or "unknown")
            record["outcome_counts"] = (
                dict(update.get("outcome_counts", {}))
                if isinstance(update.get("outcome_counts"), dict)
                else {}
            )
        elif update_type == "screen_region":
            region = tuple(int(value) for value in update["region"])
            room.screen_regions[region] = {
                "views": int(update.get("views", 1)),
                "interest": float(update.get("interest", 0.0)),
                "hypothesis": update.get("hypothesis"),
                "inspections": int(update.get("inspections", 0)),
            }
        elif update_type == "room_view_tile":
            region = tuple(int(value) for value in update["region"])
            path = Path(str(update.get("path") or ""))
            if path.is_file():
                room.view_tiles[region] = {
                    "path": str(path.resolve()),
                    "coverage": float(update.get("coverage", 1.0)),
                    "pixels_per_world": int(update.get("pixels_per_world", 1)),
                    "last_step": int(update.get("last_step", 0)),
                }

    def _add_warp(
        self,
        source_room: str,
        source: tuple[int, int],
        target_room: str,
        target: tuple[int, int],
        action: str,
        count: int,
    ) -> None:
        source_map = self.room(source_room)
        target_map = self.room(target_room)
        source_map.cells.add(source)
        target_map.cells.add(target)

        nearby = [
            key
            for key in source_map.warps
            if key[2] == target_room
            and max(abs(key[0] - source[0]), abs(key[1] - source[1]))
            <= WARP_CLUSTER_RADIUS
        ]
        if nearby:
            key = min(
                nearby,
                key=lambda candidate: abs(candidate[0] - source[0])
                + abs(candidate[1] - source[1]),
            )
            record = source_map.warps[key]
        else:
            key = (*source, target_room)
            record = {"kind": "exit", "variants": {}}

        variants = record.setdefault("variants", {})
        variant = (*source, action, *target)
        variants[variant] = max(int(count), int(variants.get(variant, 0)))

        source_counts: Counter[tuple[int, int]] = Counter()
        arrival_counts: Counter[tuple[int, int]] = Counter()
        action_counts: Counter[str] = Counter()
        for (source_x, source_y, variant_action, target_x, target_y), variant_count in variants.items():
            source_counts[(source_x, source_y)] += variant_count
            arrival_counts[(target_x, target_y)] += variant_count
            action_counts[variant_action] += variant_count

        representative = min(
            source_counts,
            key=lambda cell: (-source_counts[cell], cell[0], cell[1]),
        )
        arrival = min(
            arrival_counts,
            key=lambda cell: (-arrival_counts[cell], cell[0], cell[1]),
        )
        meaningful_actions = [name for name in action_counts if name != "event"]
        action_pool = meaningful_actions or list(action_counts)
        preferred_action = min(
            action_pool,
            key=lambda name: (-action_counts[name], name),
        )
        record.update(
            {
                "action": preferred_action,
                "count": sum(variants.values()),
                "target_cell": arrival,
                "arrival_samples": dict(arrival_counts),
            }
        )
        new_key = (*representative, target_room)
        if new_key != key:
            source_map.warps.pop(key, None)
        source_map.warps[new_key] = record


class AgentGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.project_root = Path(__file__).resolve().parent.parent
        self.window_memory = self.project_root / "memory" / "window_titles.json"
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.stop_file: Path | None = None
        self.closing = False
        self.close_deadline = 0.0
        self.map_model = WallMapModel()
        self.map_model.load_memory(self.project_root / "memory" / "navigation.json")
        self.map_model.load_room_views(
            self.project_root / "memory" / "room_views" / "index.json"
        )
        self.style = ttk.Style(root)
        self.colors = THEMES["dark"]
        self.output_widgets: list[tk.Text] = []
        self.legend_swatches: dict[str, tk.Label] = {}
        self._last_ai_signature: tuple[str, str, str] | None = None
        self._repeated_ai_decisions = 0

        root.title("Deltarune AI Controller")
        root.geometry("1280x820")
        root.minsize(920, 620)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.live_var = tk.BooleanVar(value=False)
        self.steps_var = tk.StringVar(value="2000")
        self.window_var = tk.StringVar(value="deltarune")
        self.follow_room_var = tk.BooleanVar(value=True)
        self.show_room_view_var = tk.BooleanVar(value=True)
        self.show_navigation_var = tk.BooleanVar(value=True)
        self.show_guesses_var = tk.BooleanVar(value=True)
        self.dark_mode_var = tk.BooleanVar(value=True)
        self.room_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Stopped")
        self.map_detail_var = tk.StringVar(value="No map data yet.")
        self.current_decision_var = tk.StringVar(value="Waiting to start")
        self.current_reason_var = tk.StringVar(
            value="Start the AI to see its latest decision and reasoning here."
        )
        self.current_location_var = tk.StringVar(value="Room: not reported")
        self.current_capture_var = tk.StringVar(value="Scene capture: waiting")
        self._map_transform: tuple[int, int, float, float, float] | None = None
        self._map_images: list[ImageTk.PhotoImage] = []
        self._map_image_cache: dict[tuple[object, ...], ImageTk.PhotoImage] = {}
        self._map_view_state: dict[str, dict[str, float]] = {}
        self._map_pan_anchor: tuple[str, int, int, float, float] | None = None

        self._build_controls()
        self._build_main_area()
        self._apply_theme()
        self._refresh_room_choices(select_current=False)
        self._redraw_map()
        root.after(50, self._poll_events)

    def _build_controls(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(fill="x")
        title_group = ttk.Frame(header)
        title_group.pack(side="left")
        ttk.Label(
            title_group,
            text="Deltarune AI Controller",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text="Observed scene memory, learned navigation, and telemetry",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).pack(side="right", padx=(12, 0))

        controls = ttk.LabelFrame(self.root, text="Run controls", padding=(10, 7))
        controls.pack(fill="x", padx=10)

        ttk.Checkbutton(controls, text="Live input", variable=self.live_var).pack(
            side="left", padx=(0, 8)
        )
        ttk.Label(controls, text="Steps:").pack(side="left")
        ttk.Entry(controls, textvariable=self.steps_var, width=8).pack(
            side="left", padx=(3, 8)
        )
        ttk.Label(controls, text="Game window:").pack(side="left")
        ttk.Entry(controls, textvariable=self.window_var, width=22).pack(
            side="left", padx=(3, 8)
        )
        self.start_button = ttk.Button(
            controls,
            text="Start AI",
            command=self.start,
            style="Accent.TButton",
        )
        self.start_button.pack(side="left", padx=3)
        self.stop_button = ttk.Button(
            controls, text="Stop AI", command=self.stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=3)
        ttk.Button(controls, text="Clear output", command=self._clear_output).pack(
            side="left", padx=3
        )
        ttk.Checkbutton(
            controls,
            text="Dark mode",
            variable=self.dark_mode_var,
            command=self._apply_theme,
        ).pack(side="right", padx=(8, 2))

    def _build_main_area(self) -> None:
        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=10)

        map_frame = ttk.LabelFrame(
            panes,
            text="Remembered room view",
            padding=8,
        )
        map_tools = ttk.Frame(map_frame)
        map_tools.pack(fill="x", pady=(0, 4))
        ttk.Label(map_tools, text="Room:").pack(side="left")
        self.room_box = ttk.Combobox(
            map_tools,
            textvariable=self.room_var,
            state="readonly",
            width=28,
        )
        self.room_box.pack(side="left", padx=4)
        self.room_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_map())
        ttk.Checkbutton(
            map_tools,
            text="Follow current room",
            variable=self.follow_room_var,
        ).pack(side="left", padx=4)
        ttk.Button(
            map_tools,
            text="Clear learned map",
            command=self._clear_wall_map,
        ).pack(side="right", padx=4)
        ttk.Button(
            map_tools,
            text="Rebuild scene images",
            command=self._clear_room_views,
        ).pack(side="right", padx=4)
        layer_tools = ttk.Frame(map_frame)
        layer_tools.pack(fill="x", pady=(0, 5))
        ttk.Label(layer_tools, text="Layers:").pack(side="left")
        for label, variable in (
            ("Remembered scene", self.show_room_view_var),
            ("Navigation evidence", self.show_navigation_var),
            ("AI guesses", self.show_guesses_var),
        ):
            ttk.Checkbutton(
                layer_tools,
                text=label,
                variable=variable,
                command=self._redraw_map,
            ).pack(side="left", padx=(6, 0))
        ttk.Button(
            layer_tools,
            text="Fit room",
            command=self._reset_map_view,
        ).pack(side="right", padx=(6, 0))
        ttk.Label(
            layer_tools,
            text="Wheel: zoom  •  Middle/right drag: move",
            style="Subtitle.TLabel",
        ).pack(side="right", padx=(8, 0))
        self.map_canvas = tk.Canvas(
            map_frame,
            highlightthickness=1,
        )
        self.map_canvas.pack(fill="both", expand=True)
        self.map_canvas.bind("<Configure>", lambda _event: self._redraw_map())
        self.map_canvas.bind("<Button-1>", self._inspect_map_cell)
        self.map_canvas.bind("<MouseWheel>", self._zoom_map)
        self.map_canvas.bind("<ButtonPress-2>", self._start_map_pan)
        self.map_canvas.bind("<B2-Motion>", self._drag_map)
        self.map_canvas.bind("<ButtonRelease-2>", self._end_map_pan)
        self.map_canvas.bind("<ButtonPress-3>", self._start_map_pan)
        self.map_canvas.bind("<B3-Motion>", self._drag_map)
        self.map_canvas.bind("<ButtonRelease-3>", self._end_map_pan)
        self._build_map_legend(map_frame)
        ttk.Label(
            map_frame,
            textvariable=self.map_detail_var,
            wraplength=520,
        ).pack(fill="x", pady=(4, 0))
        panes.add(map_frame, weight=1)

        output_column = ttk.Frame(panes)
        situation = ttk.LabelFrame(
            output_column,
            text="Current situation",
            padding=(10, 8),
        )
        situation.pack(fill="x", pady=(0, 8))
        ttk.Label(
            situation,
            textvariable=self.current_decision_var,
            style="Decision.TLabel",
        ).pack(fill="x", anchor="w")
        ttk.Label(
            situation,
            textvariable=self.current_reason_var,
            style="Reason.TLabel",
            wraplength=500,
            justify="left",
        ).pack(fill="x", anchor="w", pady=(3, 6))
        situation_meta = ttk.Frame(situation, style="Situation.TFrame")
        situation_meta.pack(fill="x")
        ttk.Label(
            situation_meta,
            textvariable=self.current_location_var,
            style="Meta.TLabel",
        ).pack(fill="x", anchor="w")
        ttk.Label(
            situation_meta,
            textvariable=self.current_capture_var,
            style="Meta.TLabel",
        ).pack(fill="x", anchor="w", pady=(2, 0))

        output_panes = ttk.Panedwindow(output_column, orient="vertical")
        output_panes.pack(fill="both", expand=True)
        ai_frame = ttk.LabelFrame(output_panes, text="Decision history", padding=3)
        telemetry_frame = ttk.LabelFrame(
            output_panes, text="Game telemetry", padding=3
        )
        self.ai_output = self._text_with_scrollbar(ai_frame, monospace=False)
        self.telemetry_output = self._text_with_scrollbar(
            telemetry_frame,
            monospace=True,
        )
        output_panes.add(ai_frame, weight=1)
        output_panes.add(telemetry_frame, weight=1)
        panes.add(output_column, weight=1)

    def _build_map_legend(self, parent: ttk.Frame) -> None:
        legend = ttk.LabelFrame(parent, text="Map legend", padding=5)
        legend.pack(fill="x", pady=(5, 0))
        items = [
            ("Remembered camera pixels", "camera"),
            ("Visited (brighter = repeated)", "cell"),
            ("Observed path", "path"),
            ("Blocked edge", "wall"),
            ("Seen on screen", "visible_region"),
            ("Visual guess (?)", "hypothesis"),
            ("Discovered interactable", "interactable"),
            ("Confirmed room exit", "warp"),
            ("Kris", "player"),
        ]
        for index, (label, color) in enumerate(items):
            item = ttk.Frame(legend)
            item.grid(
                row=index // 3,
                column=index % 3,
                sticky="w",
                padx=(0, 14),
                pady=2,
            )
            swatch = tk.Label(item, width=2, relief="solid", borderwidth=1)
            swatch.pack(side="left", padx=(0, 4))
            self.legend_swatches[color] = swatch
            ttk.Label(item, text=label).pack(side="left")

    def _text_with_scrollbar(
        self,
        parent: ttk.Frame,
        *,
        monospace: bool,
    ) -> tk.Text:
        scrollbar = ttk.Scrollbar(parent, orient="vertical")
        text = tk.Text(
            parent,
            wrap="word",
            font=(("Consolas", 9) if monospace else ("Segoe UI", 9)),
            yscrollcommand=scrollbar.set,
            spacing1=2,
            spacing3=3,
        )
        scrollbar.configure(command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self.output_widgets.append(text)
        return text

    def _apply_theme(self) -> None:
        mode = "dark" if self.dark_mode_var.get() else "light"
        self.colors = THEMES[mode]
        colors = self.colors
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.configure(background=colors["bg"])
        self.root.option_add("*TCombobox*Listbox.background", colors["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure(
            "TLabel",
            background=colors["bg"],
            foreground=colors["text"],
        )
        self.style.configure(
            "Header.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 16),
        )
        self.style.configure(
            "Subtitle.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background=colors["panel_alt"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 9),
            padding=(10, 5),
        )
        self.style.configure("Situation.TFrame", background=colors["panel"])
        self.style.configure(
            "Decision.TLabel",
            background=colors["panel"],
            foreground=colors["accent_active"],
            font=("Segoe UI Semibold", 12),
        )
        self.style.configure(
            "Reason.TLabel",
            background=colors["panel"],
            foreground=colors["text"],
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Meta.TLabel",
            background=colors["panel"],
            foreground=colors["muted"],
            font=("Segoe UI", 8),
        )
        self.style.configure(
            "TLabelframe",
            background=colors["panel"],
            bordercolor=colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=colors["panel"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 9),
        )
        self.style.configure(
            "TCheckbutton",
            background=colors["panel"],
            foreground=colors["text"],
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", colors["panel"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "TButton",
            background=colors["panel_alt"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=(9, 5),
        )
        self.style.map(
            "TButton",
            background=[
                ("active", colors["border"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground="#ffffff",
            bordercolor=colors["accent"],
            font=("Segoe UI Semibold", 9),
        )
        self.style.map(
            "Accent.TButton",
            background=[
                ("active", colors["accent_active"]),
                ("disabled", colors["border"]),
            ],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=colors["field"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            insertcolor=colors["text"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["field"],
            background=colors["panel_alt"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
            bordercolor=colors["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["field"])],
            foreground=[("readonly", colors["text"])],
            selectbackground=[("readonly", colors["field"])],
            selectforeground=[("readonly", colors["text"])],
        )
        self.style.configure(
            "TScrollbar",
            background=colors["panel_alt"],
            troughcolor=colors["field"],
            bordercolor=colors["border"],
            arrowcolor=colors["text"],
        )
        self.style.configure("TPanedwindow", background=colors["bg"])

        if hasattr(self, "map_canvas"):
            self.map_canvas.configure(
                background=colors["field"],
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
            )
        for widget in self.output_widgets:
            widget.configure(
                background=colors["field"],
                foreground=colors["text"],
                insertbackground=colors["text"],
                selectbackground=colors["accent"],
                selectforeground="#ffffff",
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
                relief="flat",
                padx=7,
                pady=5,
            )
        for color_key, swatch in self.legend_swatches.items():
            swatch.configure(
                background=colors[color_key],
                highlightbackground=colors["border"],
            )
        if hasattr(self, "map_canvas"):
            self._map_image_cache.clear()
            self._redraw_map()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self._last_ai_signature = None
        self._repeated_ai_decisions = 0
        try:
            steps = int(self.steps_var.get())
            if steps < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid steps", "Steps must be a positive whole number.")
            return

        game_window = self.window_var.get().strip() or "deltarune"
        detected_window = None
        try:
            if self.live_var.get():
                # The GUI owns foreground permission because the user clicked it.
                # Hand focus to Deltarune before the child controller starts.
                detected_window = focus_window(game_window, self.window_memory)
                if not is_window_foreground(detected_window):
                    raise RuntimeError(
                        "Windows did not allow the GUI to focus Deltarune. "
                        "Click Deltarune once, then press Start AI again."
                    )
            else:
                detected_window = find_window(game_window, self.window_memory)
        except RuntimeError as exc:
            messagebox.showerror("Could not focus Deltarune", str(exc))
            return
        if detected_window is not None:
            remember_window(self.window_memory, detected_window)
            self.window_var.set(detected_window.title)
            game_window = detected_window.executable or detected_window.title
            self._append(
                self.ai_output,
                f"Detected window: {detected_window.title} "
                f"({detected_window.executable or 'unknown executable'})\n",
            )
        stop_directory = self.project_root / "memory"
        stop_directory.mkdir(parents=True, exist_ok=True)
        self.stop_file = stop_directory / f"gui-stop-{uuid4().hex}.flag"
        self.stop_file.unlink(missing_ok=True)
        command = [
            sys.executable,
            "-u",
            "-m",
            "deltarune_agent",
            "run",
            "--steps",
            str(steps),
            "--game-window",
            game_window,
            "--event-stream",
            "--stop-file",
            str(self.stop_file),
            "--window-memory",
            str(self.window_memory),
        ]
        if self.live_var.get():
            command.append("--live")

        self._append(self.ai_output, "\n--- Starting AI ---\n")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.process = None
            self.stop_file = None
            messagebox.showerror("Could not start AI", str(exc))
            return

        self.status_var.set("Running (LIVE)" if self.live_var.get() else "Running (dry)")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(
            target=self._read_process,
            args=(self.process,),
            daemon=True,
        ).start()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        if self.stop_file is not None:
            self.stop_file.write_text("stop\n", encoding="utf-8")
        self.status_var.set("Stopping safely...")
        self.stop_button.configure(state="disabled")

    def _read_process(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if line.startswith(EVENT_PREFIX):
                    try:
                        self.events.put(("event", json.loads(line[len(EVENT_PREFIX) :])))
                    except json.JSONDecodeError:
                        self.events.put(("ai", f"Malformed GUI event: {line}"))
                else:
                    self.events.put(("ai", line))
        return_code = process.wait()
        self.events.put(("exit", return_code))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "ai":
                    self._append(self.ai_output, str(payload) + "\n")
                elif kind == "event":
                    self._handle_event(payload)
                elif kind == "exit":
                    self._handle_exit(int(payload))
        except queue.Empty:
            pass
        if self.closing:
            self._finish_close_if_ready()
        else:
            self.root.after(50, self._poll_events)

    def _handle_event(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "runtime_status":
            status = str(payload.get("status") or "")
            message = str(payload.get("message") or "")
            if status == "background":
                self.status_var.set("Running in background")
            elif status == "running":
                self.status_var.set(
                    "Running (LIVE)" if self.live_var.get() else "Running (dry)"
                )
            if message:
                self._append(self.ai_output, f"--- {message} ---\n")
            return
        self.map_model.update(payload)
        self._display_ai_decision(payload)
        telemetry = payload.get("telemetry")
        if telemetry:
            self._append(self.telemetry_output, format_telemetry_event(payload) + "\n\n")
        self._refresh_room_choices(select_current=self.follow_room_var.get())
        self._redraw_map()

    def _display_ai_decision(self, payload: dict[str, object]) -> None:
        category, action, explanation = decision_parts(payload)
        signature = (category, action, explanation)
        self.current_decision_var.set(
            f"Step {int(payload.get('step') or 0):04d}  |  {category}  |  {action}"
        )
        self.current_reason_var.set(explanation)
        telemetry = payload.get("telemetry")
        if isinstance(telemetry, dict):
            room = str(
                telemetry.get("room_name")
                or self.map_model.current_room
                or telemetry.get("room_id")
                or "transition"
            ).removeprefix("room_")
            if telemetry.get("mode") == "overworld":
                x, y = telemetry.get("x"), telemetry.get("y")
            else:
                x, y = telemetry.get("player_x"), telemetry.get("player_y")
            try:
                location = f"Room: {room}  |  Kris: {round(float(x))}, {round(float(y))}"
            except (TypeError, ValueError):
                location = f"Room: {room}  |  Kris: not reported"
        else:
            room = (self.map_model.current_room or "not reported").removeprefix(
                "room_"
            )
            location = f"Room: {room}  |  Telemetry: unavailable"
        self.current_location_var.set(location)
        room_map = self.map_model.rooms.get(self.map_model.current_room or "")
        active_guesses = 0
        if room_map is not None:
            active_guesses = sum(
                bool(record.get("hypothesis"))
                and int(record.get("inspections", 0)) < 2
                for record in room_map.screen_regions.values()
            )
        capture_status = (
            "Scene: live"
            if payload.get("visual_valid", True)
            else "Scene: holding last clean frame"
        )
        self.current_capture_var.set(
            f"{capture_status}  |  AI guesses: {active_guesses}"
        )
        if signature == self._last_ai_signature:
            self._repeated_ai_decisions += 1
            if (
                self._repeated_ai_decisions in {5, 10}
                or self._repeated_ai_decisions % 25 == 0
            ):
                self._append(
                    self.ai_output,
                    f"    ...same plan continues ({self._repeated_ai_decisions} steps)\n",
                )
            return
        self._last_ai_signature = signature
        self._repeated_ai_decisions = 1
        self._append(self.ai_output, format_ai_decision(payload) + "\n\n")

    def _handle_exit(self, return_code: int) -> None:
        self._append(self.ai_output, f"--- AI exited with code {return_code} ---\n")
        self.status_var.set("Stopped" if return_code == 0 else f"Stopped (error {return_code})")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.process = None
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)
            self.stop_file = None

    def _refresh_room_choices(self, select_current: bool) -> None:
        rooms = sorted(self.map_model.rooms)
        self.room_box.configure(values=rooms)
        if select_current and self.map_model.current_room in rooms:
            self.room_var.set(self.map_model.current_room or "")
        elif self.room_var.get() not in rooms:
            preferred = "room_krisroom" if "room_krisroom" in rooms else (rooms[0] if rooms else "")
            self.room_var.set(preferred)

    def _room_view_state(self, room_name: str) -> dict[str, float]:
        return self._map_view_state.setdefault(
            room_name,
            {"zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0},
        )

    def _reset_map_view(self) -> None:
        room_name = self.room_var.get()
        if room_name:
            self._map_view_state[room_name] = {
                "zoom": 1.0,
                "pan_x": 0.0,
                "pan_y": 0.0,
            }
        self._redraw_map()

    def _zoom_map(self, event: tk.Event) -> str:
        room_name = self.room_var.get()
        if not room_name or self._map_transform is None or not event.delta:
            return "break"
        min_x, min_y, old_scale, old_offset_x, old_offset_y = self._map_transform
        world_x = (event.x - old_offset_x) / old_scale + min_x
        world_y = (event.y - old_offset_y) / old_scale + min_y
        state = self._room_view_state(room_name)
        factor = 1.25 if event.delta > 0 else 0.8
        state["zoom"] = min(12.0, max(0.20, state["zoom"] * factor))
        self._redraw_map()
        if self._map_transform is not None:
            new_min_x, new_min_y, new_scale, new_offset_x, new_offset_y = (
                self._map_transform
            )
            state["pan_x"] += event.x - (
                new_offset_x + (world_x - new_min_x) * new_scale
            )
            state["pan_y"] += event.y - (
                new_offset_y + (world_y - new_min_y) * new_scale
            )
            self._redraw_map()
        return "break"

    def _start_map_pan(self, event: tk.Event) -> str:
        room_name = self.room_var.get()
        if not room_name:
            return "break"
        state = self._room_view_state(room_name)
        self._map_pan_anchor = (
            room_name,
            event.x,
            event.y,
            state["pan_x"],
            state["pan_y"],
        )
        self.map_canvas.configure(cursor="fleur")
        return "break"

    def _drag_map(self, event: tk.Event) -> str:
        if self._map_pan_anchor is None:
            return "break"
        room_name, start_x, start_y, pan_x, pan_y = self._map_pan_anchor
        if room_name != self.room_var.get():
            return "break"
        state = self._room_view_state(room_name)
        state["pan_x"] = pan_x + event.x - start_x
        state["pan_y"] = pan_y + event.y - start_y
        self._redraw_map()
        return "break"

    def _end_map_pan(self, _event: tk.Event) -> str:
        self._map_pan_anchor = None
        self.map_canvas.configure(cursor="")
        return "break"

    def _redraw_map(self) -> None:
        canvas = self.map_canvas
        canvas.delete("all")
        self._map_images = []
        room_name = self.room_var.get()
        room = self.map_model.rooms.get(room_name)
        if room is None or (not room.cells and not room.view_tiles):
            canvas.create_text(
                18,
                18,
                anchor="nw",
                text="No learned map data yet.",
                fill=self.colors["muted"],
                font=("Segoe UI", 10),
            )
            self.map_detail_var.set("No map data yet.")
            self._map_transform = None
            return

        points = set(room.cells)
        for source_x, source_y, target_x, target_y in room.open_edges:
            points.update(((source_x, source_y), (target_x, target_y)))
        for x, y, _direction in room.blocked_edges:
            points.add((x, y))
        points.update(room.interactables)
        for x, y, _target_room in room.warps:
            points.add((x, y))
        for region_x, region_y in room.screen_regions:
            region_left = region_x * EXPLORATION_REGION_CELLS
            region_top = region_y * EXPLORATION_REGION_CELLS
            points.update(
                (
                    (region_left, region_top),
                    (
                        region_left + EXPLORATION_REGION_CELLS - 1,
                        region_top + EXPLORATION_REGION_CELLS - 1,
                    ),
                )
            )
        for region_x, region_y in room.view_tiles:
            region_left = region_x * EXPLORATION_REGION_CELLS
            region_top = region_y * EXPLORATION_REGION_CELLS
            points.update(
                (
                    (region_left, region_top),
                    (
                        region_left + EXPLORATION_REGION_CELLS - 1,
                        region_top + EXPLORATION_REGION_CELLS - 1,
                    ),
                )
            )
        if (
            self.map_model.current_camera is not None
            and self.map_model.current_camera[0] == room_name
        ):
            _camera_room, camera_x, camera_y, camera_width, camera_height = (
                self.map_model.current_camera
            )
            points.update(
                (
                    (
                        int(camera_x // CELL_SIZE),
                        int(camera_y // CELL_SIZE),
                    ),
                    (
                        int((camera_x + camera_width - 1e-6) // CELL_SIZE),
                        int((camera_y + camera_height - 1e-6) // CELL_SIZE),
                    ),
                )
            )
        min_x = min(x for x, _y in points)
        max_x = max(x for x, _y in points)
        min_y = min(y for _x, y in points)
        max_y = max(y for _x, y in points)
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 300)
        view_state = self._room_view_state(room_name)
        base_offset_x = 32.0
        base_offset_y = 44.0
        fit_scale = min(
            30.0,
            (width - base_offset_x * 2) / max(1, max_x - min_x + 2),
            (height - base_offset_y - 24) / max(1, max_y - min_y + 2),
        )
        scale = max(0.75, fit_scale * view_state["zoom"])
        offset_x = base_offset_x + view_state["pan_x"]
        offset_y = base_offset_y + view_state["pan_y"]
        self._map_transform = (min_x, min_y, scale, offset_x, offset_y)

        def center(cell: tuple[int, int]) -> tuple[float, float]:
            return (
                offset_x + (cell[0] - min_x + 0.5) * scale,
                offset_y + (cell[1] - min_y + 0.5) * scale,
            )

        canvas.create_text(
            16,
            13,
            anchor="nw",
            text=room_name,
            fill=self.colors["text"],
            font=("Segoe UI Semibold", 11),
        )
        canvas.create_text(
            width - 16,
            15,
            anchor="ne",
            text=(
                "8 px detail grid  •  "
                f"{len({(x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS) for x, y in room.cells})} "
                "exploration regions"
            ),
            fill=self.colors["muted"],
            font=("Segoe UI", 8),
        )

        left = offset_x
        top = offset_y
        right = offset_x + (max_x - min_x + 1) * scale
        bottom = offset_y + (max_y - min_y + 1) * scale
        remembered_view_visible = self.show_room_view_var.get() and bool(
            room.view_tiles
        )
        if remembered_view_visible:
            for (region_x, region_y), record in sorted(room.view_tiles.items()):
                try:
                    rendered_size = max(
                        1,
                        round(EXPLORATION_REGION_CELLS * scale),
                    )
                    tile_path = Path(str(record["path"]))
                    cache_key = (
                        str(tile_path),
                        tile_path.stat().st_mtime_ns,
                        rendered_size,
                        self.dark_mode_var.get(),
                    )
                    displayed = self._map_image_cache.get(cache_key)
                    if displayed is None:
                        with Image.open(tile_path) as source:
                            tile = source.convert("RGBA")
                        if not self.dark_mode_var.get():
                            tile = ImageEnhance.Brightness(tile).enhance(0.92)
                        resampling = (
                            Image.Resampling.LANCZOS
                            if rendered_size < tile.width
                            else Image.Resampling.NEAREST
                        )
                        tile = tile.resize(
                            (rendered_size, rendered_size),
                            resampling,
                        )
                        displayed = ImageTk.PhotoImage(tile)
                        if len(self._map_image_cache) >= 512:
                            self._map_image_cache.clear()
                        self._map_image_cache[cache_key] = displayed
                    self._map_images.append(displayed)
                    image_x = offset_x + (
                        region_x * EXPLORATION_REGION_CELLS - min_x
                    ) * scale
                    image_y = offset_y + (
                        region_y * EXPLORATION_REGION_CELLS - min_y
                    ) * scale
                    canvas.create_image(
                        image_x,
                        image_y,
                        image=displayed,
                        anchor="nw",
                    )
                except (OSError, KeyError, TypeError, ValueError, tk.TclError):
                    continue

        navigation_visible = self.show_navigation_var.get()
        for grid_x in (range(min_x, max_x + 2) if navigation_visible else ()):
            x = offset_x + (grid_x - min_x) * scale
            canvas.create_line(x, top, x, bottom, fill=self.colors["grid"])
        for grid_y in (range(min_y, max_y + 2) if navigation_visible else ()):
            y = offset_y + (grid_y - min_y) * scale
            canvas.create_line(left, y, right, y, fill=self.colors["grid"])

        for x, y in (sorted(room.cells) if navigation_visible else ()):
            cx, cy = center((x, y))
            half = scale * 0.42
            visits = room.visits.get((x, y), 1)
            cell_color = (
                self.colors["cell_hot"]
                if visits >= 20
                else self.colors["cell_repeat"] if visits >= 5 else self.colors["cell"]
            )
            canvas.create_rectangle(
                cx - half,
                cy - half,
                cx + half,
                cy + half,
                fill="" if remembered_view_visible else cell_color,
                outline=self.colors["cell_outline"],
            )
            if remembered_view_visible and visits >= 5:
                radius = min(3.0, scale * 0.16)
                canvas.create_oval(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    fill=cell_color,
                    outline="",
                )
        for source_x, source_y, target_x, target_y in (
            sorted(room.open_edges) if navigation_visible else ()
        ):
            canvas.create_line(
                *center((source_x, source_y)),
                *center((target_x, target_y)),
                fill=self.colors["path"],
                width=max(2, round(scale * 0.16)),
            )
        for (region_x, region_y), record in sorted(room.screen_regions.items()):
            region_left = region_x * EXPLORATION_REGION_CELLS
            region_top = region_y * EXPLORATION_REGION_CELLS
            x1 = offset_x + (region_left - min_x) * scale
            y1 = offset_y + (region_top - min_y) * scale
            x2 = x1 + EXPLORATION_REGION_CELLS * scale
            y2 = y1 + EXPLORATION_REGION_CELLS * scale
            is_current = (
                room_name,
                region_x,
                region_y,
            ) in self.map_model.current_visible_regions
            # Keep the live camera boundary visible even when remembered scene
            # tiles are enabled. Otherwise the detailed background hides which
            # part of the map Kris can currently see.
            if navigation_visible:
                canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    outline=(
                        self.colors["visible_current"]
                        if is_current
                        else self.colors["visible_region"]
                    ),
                    width=2 if is_current else 1,
                    dash=() if is_current else (4, 3),
                )
            if (
                self.show_guesses_var.get()
                and record.get("hypothesis")
                and int(record.get("inspections", 0)) < 2
            ):
                canvas.create_text(
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    text="?",
                    fill=self.colors["hypothesis"],
                    font=("Segoe UI Semibold", max(8, round(scale * 0.52))),
                )
        for (x, y, direction), failures in (
            sorted(room.blocked_edges.items()) if navigation_visible else ()
        ):
            cx, cy = center((x, y))
            half = scale * 0.48
            if direction == "up":
                coords = (cx - half, cy - half, cx + half, cy - half)
            elif direction == "down":
                coords = (cx - half, cy + half, cx + half, cy + half)
            elif direction == "left":
                coords = (cx - half, cy - half, cx - half, cy + half)
            else:
                coords = (cx + half, cy - half, cx + half, cy + half)
            canvas.create_line(
                *coords,
                fill=self.colors["wall"],
                width=min(8, 3 + max(1, failures)),
            )
        for (x, y), record in (
            sorted(room.interactables.items()) if navigation_visible else ()
        ):
            cx, cy = center((x, y))
            radius = max(4.0, scale * 0.27)
            canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                fill=self.colors["interactable"],
                outline=self.colors["interactable_outline"],
                width=2,
            )
            canvas.create_text(
                cx,
                cy,
                text="I",
                fill="#17130a",
                font=("TkDefaultFont", max(7, round(scale * 0.32)), "bold"),
            )
        numbered_exits = list(enumerate(sorted(room.warps.items()), start=1))
        for exit_number, ((x, y, _target_room), _record) in (
            numbered_exits if navigation_visible else ()
        ):
            cx, cy = center((x, y))
            radius = max(5.0, scale * 0.32)
            canvas.create_polygon(
                cx,
                cy - radius,
                cx + radius,
                cy,
                cx,
                cy + radius,
                cx - radius,
                cy,
                fill=self.colors["warp"],
                outline=self.colors["warp_outline"],
            )
            canvas.create_text(
                cx,
                cy,
                text=str(exit_number),
                fill="#ffffff",
                font=("Segoe UI Semibold", max(7, round(scale * 0.28))),
            )
        if (
            self.map_model.current_camera is not None
            and self.map_model.current_camera[0] == room_name
        ):
            _camera_room, camera_x, camera_y, camera_width, camera_height = (
                self.map_model.current_camera
            )
            camera_left = offset_x + (camera_x / CELL_SIZE - min_x) * scale
            camera_top = offset_y + (camera_y / CELL_SIZE - min_y) * scale
            camera_right = camera_left + camera_width / CELL_SIZE * scale
            camera_bottom = camera_top + camera_height / CELL_SIZE * scale
            canvas.create_rectangle(
                camera_left,
                camera_top,
                camera_right,
                camera_bottom,
                outline=self.colors["camera"],
                width=3,
            )
            canvas.create_text(
                camera_left + 5,
                camera_top + 4,
                anchor="nw",
                text="VISIBLE NOW",
                fill=self.colors["camera"],
                font=("Segoe UI Semibold", 8),
            )
        if (
            self.map_model.current_room == room_name
            and self.map_model.current_cell is not None
        ):
            cx, cy = center(self.map_model.current_cell)
            radius = max(3.0, scale * 0.22)
            canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                fill=self.colors["player"],
                outline=self.colors["player_outline"],
                width=2,
            )
            direction = self.map_model.current_direction
            if direction in DIRECTION_VECTORS:
                dx, dy = DIRECTION_VECTORS[direction]
                canvas.create_line(
                    cx,
                    cy,
                    cx + dx * radius * 1.8,
                    cy + dy * radius * 1.8,
                    fill=self.colors["player_outline"],
                    width=2,
                    arrow="last",
                    arrowshape=(6, 7, 3),
                )
        explored_regions = len(
            {
                (
                    x // EXPLORATION_REGION_CELLS,
                    y // EXPLORATION_REGION_CELLS,
                )
                for x, y in room.cells
            }
        )
        exit_summary = ", ".join(
            f"{exit_number} to {target_room.removeprefix('room_')} "
            f"(crossed {record.get('count', 1)}x)"
            for exit_number, ((_x, _y, target_room), record) in numbered_exits
        )
        active_hypotheses = sum(
            bool(record.get("hypothesis"))
            and int(record.get("inspections", 0)) < 2
            for record in room.screen_regions.values()
        )
        tested_hypotheses = sum(
            bool(record.get("hypothesis"))
            and int(record.get("inspections", 0)) >= 2
            for record in room.screen_regions.values()
        )
        camera_summary = ""
        if (
            self.map_model.current_camera is not None
            and self.map_model.current_camera[0] == room_name
        ):
            _camera_room, camera_x, camera_y, camera_width, camera_height = (
                self.map_model.current_camera
            )
            camera_summary = (
                f" Visible now: ({camera_x:.0f}, {camera_y:.0f}) "
                f"{camera_width:.0f}x{camera_height:.0f}."
            )
        self.map_detail_var.set(
            f"{room_name}: {len(room.cells)} detailed map cells across "
            f"{explored_regions} exploration regions, "
            f"{len(room.view_tiles)} remembered scene regions, "
            f"{len(room.screen_regions)} regions seen on screen with "
            f"{active_hypotheses} active and {tested_hypotheses} tested visual guesses, "
            f"{len(room.open_edges)} paths, {len(room.blocked_edges)} wall edges, "
            f"{len(room.interactables)} discovered interactables, "
            f"{len(room.warps)} confirmed exits. "
            f"{('Exits: ' + exit_summary + '. ') if exit_summary else ''}"
            f"{camera_summary} Click a map cell for details."
        )

    def _inspect_map_cell(self, event: tk.Event) -> None:
        room_name = self.room_var.get()
        room = self.map_model.rooms.get(room_name)
        if room is None or self._map_transform is None:
            return
        min_x, min_y, scale, offset_x, offset_y = self._map_transform
        cell = (
            round((event.x - offset_x) / scale + min_x - 0.5),
            round((event.y - offset_y) / scale + min_y - 0.5),
        )
        visits = room.visits.get(cell, 0)
        details = [f"{room_name} cell {cell}", f"visits={visits}"]
        region_key = (
            cell[0] // EXPLORATION_REGION_CELLS,
            cell[1] // EXPLORATION_REGION_CELLS,
        )
        screen_region = room.screen_regions.get(region_key)
        view_tile = room.view_tiles.get(region_key)
        if view_tile:
            details.append(
                "remembered from camera "
                f"({float(view_tile.get('coverage', 1.0)) * 100:.0f}% of region)"
            )
        if screen_region:
            currently_visible = (
                room_name,
                *region_key,
            ) in self.map_model.current_visible_regions
            details.append(
                f"seen on screen {screen_region.get('views', 1)}x"
                f"{' (currently visible)' if currently_visible else ''}"
            )
            if (
                screen_region.get("hypothesis")
                and int(screen_region.get("inspections", 0)) < 2
            ):
                details.append(
                    f"AI guess={str(screen_region['hypothesis']).replace('_', ' ')} "
                    f"(visual interest {float(screen_region.get('interest', 0.0)):.2f}; "
                    "unconfirmed)"
                )
        interactable = room.interactables.get(cell)
        if interactable:
            approach_directions = sorted(
                {
                    str(approach.get("direction"))
                    for approach in interactable.get("approaches", [])
                    if isinstance(approach, dict) and approach.get("direction")
                }
            )
            approaches = (
                ", approached " + "/".join(approach_directions)
                if approach_directions
                else ""
            )
            details.append(
                f"interactable={interactable.get('name')} "
                f"(confirmed x{interactable.get('confirmations', 1)}{approaches})"
            )
            classification = str(interactable.get("classification") or "unknown")
            if classification == "confirmed_npc":
                details.append("story character=confirmed by an observed choice menu")
            elif classification == "tested_nonchoice":
                details.append(
                    "story character=unconfirmed; ordinary interaction is cooled "
                    "down unless stronger evidence appears"
                )
            usefulness = str(interactable.get("usefulness") or "unknown")
            if usefulness == "progress":
                details.append("story memory=useful; changed the game state")
            elif usefulness == "choice_pending":
                details.append("story memory=promising; unresolved choice responses remain")
            elif usefulness == "flavor":
                details.append("story memory=flavor; cooled down unless new evidence appears")
            last_outcome = str(interactable.get("last_outcome") or "")
            if last_outcome and last_outcome != "unknown":
                details.append(f"last result={last_outcome.replace('_', ' ')}")
        walls = [
            f"{direction}×{failures}"
            for (x, y, direction), failures in room.blocked_edges.items()
            if (x, y) == cell
        ]
        if walls:
            details.append("walls=" + ", ".join(sorted(walls)))
        warps = [
            f"confirmed exit to {target_room}; crossed with {record.get('action')} "
            f"{record.get('count')}x; destination spawned Kris near "
            f"{record.get('target_cell')} (not another exit)"
            for (x, y, target_room), record in room.warps.items()
            if (x, y) == cell
        ]
        details.extend(warps)
        if visits == 0 and cell not in room.cells:
            details.append("visible but not yet traversed" if screen_region else "unmapped")
        self.map_detail_var.set(" | ".join(details))

    @staticmethod
    def _append(widget: tk.Text, text: str) -> None:
        widget.insert("end", text)
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > 2000:
            widget.delete("1.0", f"{line_count - 1800}.0")
        widget.see("end")

    def _clear_output(self) -> None:
        self.ai_output.delete("1.0", "end")
        self.telemetry_output.delete("1.0", "end")
        self._last_ai_signature = None
        self._repeated_ai_decisions = 0

    def _clear_wall_map(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showinfo(
                "Stop AI first",
                "Stop the AI before clearing its persistent learned map.",
            )
            return
        if not messagebox.askyesno(
            "Clear learned map",
            "Delete all remembered room images, cells, paths, walls, interactions, "
            "visual guesses, and room warps?",
        ):
            return
        memory_path = self.project_root / "memory" / "navigation.json"
        memory_path.unlink(missing_ok=True)
        memory_path.with_suffix(memory_path.suffix + ".tmp").unlink(missing_ok=True)
        room_views_path = self.project_root / "memory" / "room_views"
        if room_views_path.exists():
            shutil.rmtree(room_views_path)
        self.map_model = WallMapModel()
        self._map_images = []
        self._map_image_cache.clear()
        self._map_view_state.clear()
        self.room_var.set("")
        self._refresh_room_choices(select_current=False)
        self._redraw_map()
        self._append(
            self.ai_output,
            "Learned map and remembered room views cleared. "
            "The next run will start clean.\n",
        )

    def _clear_room_views(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showinfo(
                "Stop AI first",
                "Stop the AI before rebuilding its remembered scene images.",
            )
            return
        if not messagebox.askyesno(
            "Rebuild scene images",
            "Delete only the remembered room pictures? Learned paths, walls, "
            "interactions, and exits will be kept.",
        ):
            return
        room_views_path = self.project_root / "memory" / "room_views"
        if room_views_path.exists():
            shutil.rmtree(room_views_path)
        for room in self.map_model.rooms.values():
            room.view_tiles.clear()
        self._map_images = []
        self._map_image_cache.clear()
        self._redraw_map()
        self._append(
            self.ai_output,
            "Remembered scene images cleared. Navigation knowledge was kept; "
            "new high-resolution images will appear during the next run.\n",
        )

    def _on_close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.root.destroy()
            return
        self.closing = True
        self.close_deadline = time.monotonic() + 3.0
        self.stop()

    def _finish_close_if_ready(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.root.destroy()
            return
        if time.monotonic() >= self.close_deadline:
            self.process.terminate()
            self.root.destroy()
            return
        self.root.after(50, self._poll_events)


def launch_gui() -> None:
    root = tk.Tk()
    AgentGUI(root)
    root.mainloop()
