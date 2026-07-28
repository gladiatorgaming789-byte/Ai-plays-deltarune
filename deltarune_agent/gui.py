from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from math import floor
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

from .map_guesses import VisualGuessEntry, visual_guess_entries
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
    post_window_key,
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
VISUAL_GUESS_STORY_INSPECTIONS = 3

WARP_ROLE_BADGES = {
    "progression": ("P", "warp_progression", "observed progression route"),
    "new_area": ("N", "warp_new_area", "route to a newly observed area"),
    "likely_optional": ("O", "warp_optional", "likely optional branch"),
    "return/backtrack": ("R", "warp_return", "observed return/backtrack"),
    "loop_suppressed": ("L", "warp_loop", "suppressed loop route"),
    "unknown": ("?", "warp", "role not learned yet"),
}


def warp_role_badge(role: object) -> tuple[str, str, str]:
    return WARP_ROLE_BADGES.get(
        str(role or "unknown"),
        WARP_ROLE_BADGES["unknown"],
    )

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


def format_speed_status(speed: object) -> str:
    if not isinstance(speed, dict):
        return "Game: unknown | AI: 1x | waiting for speed telemetry"

    game = speed.get("game_multiplier")
    effective = speed.get("effective_multiplier", 1)

    def multiplier_label(value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "unknown"
        return f"{number:g}x"

    if bool(speed.get("synchronized")):
        state = "synchronized"
    elif speed.get("source") == "manual":
        state = "manual override"
    else:
        state = "safe 1x fallback"
    return (
        f"Game: {multiplier_label(game)} | "
        f"AI: {multiplier_label(effective)} | {state}"
    )


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
        return (
            "OBJECT GUESS",
            "Moving toward a compact obstacle learned from one collision side. It may be useful scenery, but it is not called a character until more sides support that guess.",
        )
    if "room completion:" in reason_lower:
        if "visible exit" in reason_lower:
            return (
                "EXIT SEARCH",
                "This room has been explored for a while, so the strongest untested localized opening is being checked.",
            )
        return (
            "EXIT SEARCH",
            "This room has been explored for a while, so one learned boundary section is being tested for a transition.",
        )
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
    context = payload.get("decision_context")
    if isinstance(context, dict) and context.get("kind") == "visual_guess":
        label = str(context.get("label") or "Unconfirmed visual lead")
        lead_id = str(context.get("id") or "unidentified lead")
        evidence = str(context.get("evidence") or "limited visible evidence")
        anchor = context.get("anchor_cell")
        location = ""
        if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
            location = f" near map cell ({anchor[0]}, {anchor[1]})"
        try:
            score = f"{float(context.get('confidence', 0.0)):.0%} evidence score"
        except (TypeError, ValueError):
            score = "unscored evidence"
        explanation = (
            f"{explanation} Lead {lead_id}: {label}{location}; {evidence}; {score}."
        )
    return category, ACTION_LABELS.get(action, action.replace("_", " ").title()), explanation


def format_ai_decision(payload: dict[str, object]) -> str:
    category, action_label, explanation = decision_parts(payload)
    telemetry = payload.get("telemetry")
    location = "Unknown location"
    if isinstance(telemetry, dict):
        room = str(telemetry.get("room_name") or telemetry.get("room_id") or "transition")
        room = room.removeprefix("room_")
        x = telemetry.get("player_foot_x")
        y = telemetry.get("player_foot_y")
        if x is None or y is None:
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
    x = telemetry.get("player_foot_x")
    y = telemetry.get("player_foot_y")
    if x is None or y is None:
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
    direction = (
        telemetry.get("player_facing_direction")
        or telemetry.get("facing_direction")
        or "not reported"
    )
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
    lines = [
        f"Step {int(payload.get('step') or 0):04d}  |  {state}  |  {room}\n"
        f"  Kris: {position}  |  Facing: {direction}  |  Camera: {camera}\n"
        f"  Control gate: {control}  |  Detector: {source} ({confidence})"
        f"  |  Scene: {scene}"
    ]

    version = telemetry.get("version")
    sequence = telemetry.get("packet_sequence")
    parts = telemetry.get("packet_parts")
    if version is not None or sequence is not None or parts:
        part_text = (
            ", ".join(str(value) for value in parts)
            if isinstance(parts, (list, tuple))
            else "not reported"
        )
        lines.append(
            f"  Packet: v{version if version is not None else '?'}"
            f"  #{sequence if sequence is not None else '?'}"
            f"  |  Parts: {part_text}"
        )

    delta_x = telemetry.get("sample_delta_x")
    delta_y = telemetry.get("sample_delta_y")
    interval_ms = telemetry.get("sample_interval_ms")
    if delta_x is not None or delta_y is not None or interval_ms is not None:
        try:
            delta = f"({float(delta_x or 0):.1f}, {float(delta_y or 0):.1f})"
        except (TypeError, ValueError):
            delta = "not reported"
        try:
            interval = f"{float(interval_ms):.1f} ms"
        except (TypeError, ValueError):
            interval = "not reported"
        lines.append(
            f"  Motion sample: delta {delta} in {interval}"
            f"  |  velocity h={telemetry.get('hspeed', '?')}"
            f" v={telemetry.get('vspeed', '?')}"
        )

    sprite = telemetry.get("player_sprite_name") or telemetry.get("sprite_name")
    player_bounds = tuple(
        telemetry.get(field)
        for field in (
            "player_bbox_left",
            "player_bbox_top",
            "player_bbox_right",
            "player_bbox_bottom",
        )
    )
    if sprite or any(value is not None for value in player_bounds):
        bounds_text = (
            f"({player_bounds[0]}, {player_bounds[1]})-"
            f"({player_bounds[2]}, {player_bounds[3]})"
            if all(value is not None for value in player_bounds)
            else "not reported"
        )
        lines.append(
            f"  Render: {sprite or 'not reported'}"
            f"  |  frame {telemetry.get('image_index', '?')}"
            f"  |  player bounds {bounds_text}"
            f"  |  FPS {telemetry.get('fps', '?')}"
        )
    return "\n".join(lines)

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
        "path": "#3fa96b",
        "wall": "#ff626b",
        "interactable": "#f0c94b",
        "interactable_outline": "#8c6c10",
        "warp": "#b184f4",
        "warp_outline": "#7147a9",
        "warp_progression": "#57d38c",
        "warp_new_area": "#56c7e8",
        "warp_optional": "#6f9cff",
        "warp_return": "#b29acb",
        "warp_loop": "#ff6f78",
        "visible_region": "#42658f",
        "visible_current": "#4c8dff",
        "camera": "#8fc2ff",
        "hypothesis": "#54d6d0",
        "guess_exit": "#f5c451",
        "guess_character": "#c88cff",
        "guess_object": "#57d3e3",
        "selection": "#ffffff",
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
        "path": "#397a50",
        "wall": "#d73542",
        "interactable": "#d8aa16",
        "interactable_outline": "#765b06",
        "warp": "#8250c4",
        "warp_outline": "#4d287e",
        "warp_progression": "#198754",
        "warp_new_area": "#087f9c",
        "warp_optional": "#3468ce",
        "warp_return": "#73578f",
        "warp_loop": "#c9303e",
        "visible_region": "#91a8c4",
        "visible_current": "#2868d8",
        "camera": "#0e5fca",
        "hypothesis": "#087f7a",
        "guess_exit": "#a66b00",
        "guess_character": "#7135a5",
        "guess_object": "#087b88",
        "selection": "#101820",
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


@dataclass(frozen=True)
class MapTransform:
    """One shared conversion for scenes, navigation, guesses, and clicks."""

    min_x: int
    min_y: int
    scale: float
    offset_x: float
    offset_y: float

    def cell_boundary(self, cell: tuple[float, float]) -> tuple[float, float]:
        return (
            self.offset_x + (cell[0] - self.min_x) * self.scale,
            self.offset_y + (cell[1] - self.min_y) * self.scale,
        )

    def cell_center(self, cell: tuple[float, float]) -> tuple[float, float]:
        return self.cell_boundary((cell[0] + 0.5, cell[1] + 0.5))

    def world_point(self, point: tuple[float, float]) -> tuple[float, float]:
        return self.cell_boundary((point[0] / CELL_SIZE, point[1] / CELL_SIZE))

    def region_box(self, region: tuple[int, int]) -> tuple[float, float, float, float]:
        left, top = self.cell_boundary(
            (
                region[0] * EXPLORATION_REGION_CELLS,
                region[1] * EXPLORATION_REGION_CELLS,
            )
        )
        right, bottom = self.cell_boundary(
            (
                (region[0] + 1) * EXPLORATION_REGION_CELLS,
                (region[1] + 1) * EXPLORATION_REGION_CELLS,
            )
        )
        return left, top, right, bottom

    def world_box(
        self,
        box: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        left, top = self.world_point((box[0], box[1]))
        right, bottom = self.world_point((box[2], box[3]))
        return left, top, right, bottom

    def canvas_cell(self, point: tuple[float, float]) -> tuple[int, int]:
        return (
            floor((point[0] - self.offset_x) / self.scale + self.min_x),
            floor((point[1] - self.offset_y) / self.scale + self.min_y),
        )


class WallMapModel:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomMap] = {}
        self.current_room: str | None = None
        self.current_cell: tuple[int, int] | None = None
        self.current_world_position: tuple[float, float] | None = None
        self.current_display_position: tuple[float, float] | None = None
        self.current_direction: str | None = None
        self.current_visible_regions: set[tuple[str, int, int]] = set()
        self.current_camera: tuple[str, float, float, float, float] | None = None
        self.current_guess_region: tuple[str, int, int] | None = None
        self.current_guess_id: str | None = None

    def room(self, name: str) -> RoomMap:
        return self.rooms.setdefault(name, RoomMap())

    @staticmethod
    def _merge_screen_region(
        record: dict[str, object],
        item: dict[str, object],
    ) -> dict[str, object]:
        converters = {
            "views": int,
            "independent_views": int,
            "appearance_changes": int,
            "interest": float,
            "inspections": int,
            "completed_tests": int,
            "approach_attempts": int,
            "failed_approaches": int,
            "guess_model_version": int,
            "entity_approach_directions": int,
            "obstruction_target_cells": int,
            "guess_misses": int,
            "guess_confidence": float,
            "last_seen_sequence": int,
            "last_seen_step": int,
            "cooldown_until_tick": int,
            "contrast": float,
            "edge_density": float,
            "colorfulness": float,
            "dark_ratio": float,
            "edge_opening_score": float,
            "edge_width_ratio": float,
        }
        for field, converter in converters.items():
            if item.get(field) is not None:
                record[field] = converter(item[field])
        if "hypothesis" in item:
            record["hypothesis"] = item.get("hypothesis")
        for field in (
            "guess_label",
            "guess_id",
            "guess_state",
            "evidence_kind",
            "evidence_summary",
            "edge_hint",
            "visual_summary",
            "retired_reason",
            "last_failure_reason",
            "confirmed_target_room",
        ):
            if item.get(field):
                record[field] = str(item[field])
        for field in (
            "anchor_cell",
            "anchor_world",
            "focus_world",
            "feature_box_world",
            "visual_box_world",
            "passage_box_world",
            "obstruction_box_world",
            "approach_directions",
            "obstruction_cells",
            "evidence_viewpoints",
            "confirmed_at_cell",
            "confirmed_interactable_cell",
        ):
            value = item.get(field)
            if isinstance(value, (list, tuple)):
                record[field] = list(value)
        if item.get("path_continuation") is not None:
            record["path_continuation"] = bool(item["path_continuation"])
        if item.get("choice_retry") is not None:
            record["choice_retry"] = bool(item["choice_retry"])
        record.setdefault("views", 1)
        record.setdefault("interest", 0.0)
        record.setdefault("hypothesis", None)
        record.setdefault("inspections", 0)
        return record

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
                if (
                    hypothesis == "possible_interactable"
                    and int(item.get("guess_model_version", 0)) < 2
                ):
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
                record = self._merge_screen_region({}, item)
                record.update(
                    {
                        "views": views,
                        "interest": interest,
                        "hypothesis": hypothesis,
                    }
                )
                room.screen_regions[region] = record
            portals = [
                item
                for item in data.get("warp_portals", [])
                if isinstance(item, dict)
            ]
            if portals:
                for item in portals:
                    source_footprint = item.get("source_footprint")
                    arrival_footprint = item.get("arrival_footprint")
                    if not isinstance(source_footprint, dict):
                        continue
                    source_center = source_footprint.get("center")
                    arrival_center = (
                        arrival_footprint.get("center")
                        if isinstance(arrival_footprint, dict)
                        else None
                    )
                    if (
                        not isinstance(source_center, (list, tuple))
                        or len(source_center) != 2
                        or not isinstance(arrival_center, (list, tuple))
                        or len(arrival_center) != 2
                    ):
                        continue
                    metadata = dict(item)
                    for footprint_name in (
                        "source_footprint",
                        "arrival_footprint",
                    ):
                        footprint = metadata.get(footprint_name)
                        if not isinstance(footprint, dict):
                            continue
                        footprint = dict(footprint)
                        center_value = footprint.get("center")
                        bounds_value = footprint.get("bounds")
                        if isinstance(center_value, (list, tuple)) and len(center_value) == 2:
                            footprint["center"] = [
                                coordinate(center_value[0]),
                                coordinate(center_value[1]),
                            ]
                        if isinstance(bounds_value, (list, tuple)) and len(bounds_value) == 4:
                            footprint["bounds"] = [
                                coordinate(value) for value in bounds_value
                            ]
                        metadata[footprint_name] = footprint
                    self._add_warp(
                        str(item["from_room"]),
                        (
                            coordinate(source_center[0]),
                            coordinate(source_center[1]),
                        ),
                        str(item["to_room"]),
                        (
                            coordinate(arrival_center[0]),
                            coordinate(arrival_center[1]),
                        ),
                        str(item.get("action") or "event"),
                        int(item.get("crossings", 1)),
                        metadata=metadata,
                    )
            else:
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
            if int(data.get("version", 0)) not in {1, 2, 3}:
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
                            "mtime_ns": path.stat().st_mtime_ns,
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
        context = event.get("decision_context")
        self.current_guess_region = None
        self.current_guess_id = None
        if isinstance(context, dict) and context.get("kind") == "visual_guess":
            if context.get("id"):
                self.current_guess_id = str(context["id"])
            region = context.get("region")
            room = context.get("room")
            if room and isinstance(region, (list, tuple)) and len(region) == 2:
                try:
                    self.current_guess_region = (
                        str(room),
                        int(region[0]),
                        int(region[1]),
                    )
                except (TypeError, ValueError):
                    pass
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
            self.current_world_position = None
            self.current_display_position = None
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
        direction = (
            telemetry.get("player_facing_direction")
            or telemetry.get("facing_direction")
        )
        if direction in DIRECTION_VECTORS:
            self.current_direction = str(direction)

        x = telemetry.get("x")
        y = telemetry.get("y")
        if telemetry.get("mode") != "overworld":
            x = telemetry.get("player_x")
            y = telemetry.get("player_y")
        if x is None or y is None:
            return

        world_position = (float(x), float(y))
        self.current_world_position = world_position
        foot = (telemetry.get("player_foot_x"), telemetry.get("player_foot_y"))
        if all(value is not None for value in foot):
            try:
                self.current_display_position = tuple(float(value) for value in foot)
            except (TypeError, ValueError):
                self.current_display_position = world_position
        else:
            prefix = "" if telemetry.get("mode") == "overworld" else "player_"
            bounds = tuple(
                telemetry.get(prefix + field)
                for field in ("bbox_left", "bbox_top", "bbox_right", "bbox_bottom")
            )
            if all(value is not None for value in bounds):
                try:
                    left, _top, right, bottom = (float(value) for value in bounds)
                    self.current_display_position = ((left + right) / 2, bottom)
                except (TypeError, ValueError):
                    self.current_display_position = world_position
            else:
                self.current_display_position = world_position
        planning_position = self.current_display_position or world_position
        cell = (
            int(planning_position[0] // CELL_SIZE),
            int(planning_position[1] // CELL_SIZE),
        )
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
                metadata=update,
            )
            return
        if update_type == "warp_role":
            portal_id = str(update.get("portal_id") or "")
            if not portal_id:
                return
            for room in self.rooms.values():
                for record in room.warps.values():
                    if record.get("portal_id") != portal_id:
                        continue
                    record.update(
                        {
                            "role": str(update.get("role") or "unknown"),
                            "role_confidence": float(
                                update.get("role_confidence", 0.25)
                            ),
                            "role_basis": list(update.get("role_basis", [])),
                            "crossings": int(update.get("crossings", record.get("count", 1))),
                            "source_footprint": update.get("source_footprint"),
                            "arrival_footprint": update.get("arrival_footprint"),
                            "aperture": update.get("aperture"),
                        }
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
            record = room.screen_regions.setdefault(region, {})
            self._merge_screen_region(record, update)
        elif update_type == "room_view_tile":
            region = tuple(int(value) for value in update["region"])
            path = Path(str(update.get("path") or ""))
            if path.is_file():
                room.view_tiles[region] = {
                    "path": str(path.resolve()),
                    "mtime_ns": path.stat().st_mtime_ns,
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
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        source_map = self.room(source_room)
        target_map = self.room(target_room)
        source_map.cells.add(source)
        target_map.cells.add(target)

        nearby = [
            key
            for key in source_map.warps
            if key[2] == target_room
            and (
                not metadata
                or not metadata.get("portal_id")
                or source_map.warps[key].get("portal_id")
                in {None, metadata.get("portal_id")}
            )
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

        if metadata:
            portal_variants = metadata.get("variants")
            if isinstance(portal_variants, list):
                record["observed_variants"] = list(portal_variants)
            basis = metadata.get(
                "role_basis",
                metadata.get("basis", record.get("role_basis", [])),
            )
            record.update(
                {
                    "portal_id": metadata.get("portal_id")
                    or metadata.get("id")
                    or record.get("portal_id"),
                    "role": str(metadata.get("role") or record.get("role") or "unknown"),
                    "role_confidence": float(
                        metadata.get(
                            "role_confidence",
                            metadata.get("confidence", record.get("role_confidence", 0.25)),
                        )
                    ),
                    "role_basis": list(basis)
                    if isinstance(basis, (list, tuple))
                    else [],
                    "source_footprint": metadata.get("source_footprint")
                    or record.get("source_footprint"),
                    "arrival_footprint": metadata.get("arrival_footprint")
                    or record.get("arrival_footprint"),
                    "aperture": metadata.get("aperture")
                    or record.get("aperture"),
                }
            )

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
        self.popup_menus: list[tk.Menu] = []
        self.legend_swatches: dict[str, tk.Label] = {}
        self._last_ai_signature: tuple[str, str, str] | None = None
        self._repeated_ai_decisions = 0

        root.title("Deltarune AI Controller")
        root.geometry("1480x900")
        root.minsize(1050, 680)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.live_var = tk.BooleanVar(value=False)
        self.steps_var = tk.StringVar(value="2000")
        self.window_var = tk.StringVar(value="deltarune")
        self.speed_var = tk.StringVar(value="Auto")
        self.speed_status_var = tk.StringVar(
            value="Game: unknown | AI: 1x | waiting for speed telemetry"
        )
        self.follow_room_var = tk.BooleanVar(value=True)
        self.show_room_view_var = tk.BooleanVar(value=True)
        self.show_navigation_var = tk.BooleanVar(value=True)
        self.show_visits_var = tk.BooleanVar(value=False)
        self.show_grid_var = tk.BooleanVar(value=False)
        self.show_objects_var = tk.BooleanVar(value=True)
        self.show_current_view_var = tk.BooleanVar(value=True)
        self.show_guesses_var = tk.BooleanVar(value=True)
        self.show_guess_sources_var = tk.BooleanVar(value=False)
        self.dark_mode_var = tk.BooleanVar(value=True)
        self.room_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Stopped")
        self.map_detail_var = tk.StringVar(value="No map data yet.")
        self.map_title_var = tk.StringVar(value="No room selected")
        self.map_stats_var = tk.StringVar(value="Waiting for map data")
        self.map_zoom_var = tk.StringVar(value="100%")
        self.current_decision_var = tk.StringVar(value="Waiting to start")
        self.current_reason_var = tk.StringVar(
            value="Start the AI to see its latest decision and reasoning here."
        )
        self.current_location_var = tk.StringVar(value="Room: not reported")
        self.current_capture_var = tk.StringVar(value="Scene capture: waiting")
        self.guess_detail_var = tk.StringVar(
            value="Select a lead to see its full evidence and exact map extent."
        )
        self._map_transform: MapTransform | None = None
        self._map_images: list[ImageTk.PhotoImage] = []
        self._map_image_cache: dict[tuple[object, ...], ImageTk.PhotoImage] = {}
        self._map_view_state: dict[str, dict[str, float]] = {}
        self._map_pan_anchor: tuple[str, int, int, float, float] | None = None
        self._guess_tree_targets: dict[str, VisualGuessEntry] = {}
        self._selected_map_target: tuple[str, tuple[int, int]] | None = None
        self._selected_guess_key: (
            tuple[str, tuple[tuple[int, int], ...]] | None
        ) = None

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
        ttk.Label(controls, text="AI speed:").pack(side="left", padx=(12, 0))
        self.speed_box = ttk.Combobox(
            controls,
            textvariable=self.speed_var,
            values=("Auto",) + tuple(f"{value}x" for value in range(1, 11)),
            state="readonly",
            width=6,
        )
        self.speed_box.pack(side="left", padx=(3, 6))
        for key, label in (
            ("f8", "F8 toggle"),
            ("f9", "F9 −"),
            ("f10", "F10 +"),
        ):
            ttk.Button(
                controls,
                text=label,
                command=lambda key=key: self._send_speed_key(key),
                width=9,
            ).pack(side="left", padx=2)
        ttk.Checkbutton(
            controls,
            text="Dark mode",
            variable=self.dark_mode_var,
            command=self._apply_theme,
        ).pack(side="right", padx=(8, 2))
        ttk.Label(
            controls,
            textvariable=self.speed_status_var,
            style="Subtitle.TLabel",
        ).pack(side="right", padx=(8, 4))

    def _send_speed_key(self, key: str) -> None:
        try:
            game_window = self.window_var.get().strip() or "deltarune"
            window = find_window(game_window, self.window_memory)
            if window is None:
                raise RuntimeError(
                    "No Deltarune window is running. Launch a chapter first."
                )
            remember_window(self.window_memory, window)
            post_window_key(window.hwnd, key, True)
            self.root.after(
                80,
                lambda hwnd=window.hwnd, key=key: self._release_speed_key(
                    hwnd, key
                ),
            )
            self._append(
                self.ai_output,
                f"Sent {key.upper()} to Deltarune; waiting for speed telemetry.\n",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not change game speed", str(exc))

    def _release_speed_key(self, hwnd: int, key: str) -> None:
        try:
            post_window_key(hwnd, key, False)
        except (OSError, ValueError) as exc:
            self._append(
                self.ai_output,
                f"Speed-control key release warning: {exc}\n",
            )

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
        map_tools.columnconfigure(1, weight=1)
        ttk.Label(map_tools, text="Room:").grid(row=0, column=0, sticky="w")
        self.room_box = ttk.Combobox(
            map_tools,
            textvariable=self.room_var,
            state="readonly",
            width=24,
        )
        self.room_box.grid(row=0, column=1, sticky="ew", padx=4)
        self.room_box.bind("<<ComboboxSelected>>", self._select_room_from_ui)
        ttk.Checkbutton(
            map_tools,
            text="Follow current room",
            variable=self.follow_room_var,
            command=self._toggle_follow_room,
        ).grid(row=0, column=2, padx=4)
        ttk.Button(map_tools, text="Fit", command=self._reset_map_view).grid(
            row=0,
            column=3,
            padx=(4, 0),
        )
        layer_tools = ttk.Frame(map_frame)
        layer_tools.pack(fill="x", pady=(0, 5))
        layers_button = ttk.Menubutton(layer_tools, text="Layers")
        layers_menu = tk.Menu(layers_button, tearoff=False)
        for label, variable in (
            ("Remembered scene", self.show_room_view_var),
            ("Walked paths and walls", self.show_navigation_var),
            ("Visit heat", self.show_visits_var),
            ("8 px detail grid", self.show_grid_var),
            ("Learned objects and exits", self.show_objects_var),
            ("AI guesses", self.show_guesses_var),
            ("Guess source buckets (diagnostic)", self.show_guess_sources_var),
            ("Current camera", self.show_current_view_var),
        ):
            layers_menu.add_checkbutton(
                label=label,
                variable=variable,
                command=self._redraw_map,
            )
        layers_button.configure(menu=layers_menu)
        self.popup_menus.append(layers_menu)
        layers_button.pack(side="left")

        data_button = ttk.Menubutton(layer_tools, text="Map data")
        data_menu = tk.Menu(data_button, tearoff=False)
        data_menu.add_command(
            label="Rebuild scene images",
            command=self._clear_room_views,
        )
        data_menu.add_separator()
        data_menu.add_command(label="Clear learned map", command=self._clear_wall_map)
        data_button.configure(menu=data_menu)
        self.popup_menus.append(data_menu)
        data_button.pack(side="left", padx=(6, 0))
        ttk.Label(
            layer_tools,
            text="Wheel: zoom  |  Middle/right drag: move",
            style="Subtitle.TLabel",
        ).pack(side="right", padx=(8, 0))
        map_heading = ttk.Frame(map_frame)
        map_heading.pack(fill="x", pady=(1, 5))
        ttk.Label(
            map_heading,
            textvariable=self.map_title_var,
            style="MapTitle.TLabel",
        ).pack(fill="x", anchor="w")
        ttk.Label(
            map_heading,
            textvariable=self.map_stats_var,
            style="Subtitle.TLabel",
        ).pack(fill="x", anchor="w", pady=(1, 0))

        map_panes = ttk.Panedwindow(map_frame, orient="vertical")
        map_panes.pack(fill="both", expand=True)
        canvas_frame = ttk.Frame(map_panes)
        self.map_canvas = tk.Canvas(canvas_frame, highlightthickness=1)
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

        inspector = ttk.Notebook(map_panes)
        guesses_tab = ttk.Frame(inspector, padding=4)
        selection_tab = ttk.Frame(inspector, padding=8)
        legend_tab = ttk.Frame(inspector, padding=4)
        inspector.add(guesses_tab, text="AI guesses")
        inspector.add(selection_tab, text="Selected area")
        inspector.add(legend_tab, text="Map key")

        guess_columns = ("marker", "guess", "where", "evidence", "status")
        self.guess_tree = ttk.Treeview(
            guesses_tab,
            columns=guess_columns,
            show="headings",
            height=5,
        )
        headings = {
            "marker": "ID",
            "guess": "Current guess",
            "where": "Map anchor",
            "evidence": "Why it exists",
            "status": "Status",
        }
        widths = {
            "marker": 38,
            "guess": 155,
            "where": 80,
            "evidence": 220,
            "status": 120,
        }
        for column in guess_columns:
            self.guess_tree.heading(column, text=headings[column])
            self.guess_tree.column(
                column,
                width=widths[column],
                minwidth=40,
                stretch=column in {"guess", "evidence", "status"},
            )
        guess_scroll_y = ttk.Scrollbar(
            guesses_tab,
            orient="vertical",
            command=self.guess_tree.yview,
        )
        guess_scroll_x = ttk.Scrollbar(
            guesses_tab,
            orient="horizontal",
            command=self.guess_tree.xview,
        )
        self.guess_tree.configure(
            yscrollcommand=guess_scroll_y.set,
            xscrollcommand=guess_scroll_x.set,
        )
        self.guess_tree.grid(row=0, column=0, sticky="nsew")
        guess_scroll_y.grid(row=0, column=1, sticky="ns")
        guess_scroll_x.grid(row=1, column=0, sticky="ew")
        self.guess_detail_label = ttk.Label(
            guesses_tab,
            textvariable=self.guess_detail_var,
            justify="left",
            anchor="nw",
            style="Subtitle.TLabel",
        )
        self.guess_detail_label.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=(2, 4),
            pady=(5, 1),
        )
        guesses_tab.bind(
            "<Configure>",
            lambda event: self.guess_detail_label.configure(
                wraplength=max(220, event.width - 28)
            ),
        )
        guesses_tab.bind("<Configure>", self._resize_guess_columns, add="+")
        guesses_tab.rowconfigure(0, weight=1)
        guesses_tab.columnconfigure(0, weight=1)
        self.guess_tree.bind("<<TreeviewSelect>>", self._select_guess)

        self.map_detail_label = ttk.Label(
            selection_tab,
            textvariable=self.map_detail_var,
            justify="left",
            anchor="nw",
        )
        self.map_detail_label.pack(fill="both", expand=True)
        selection_tab.bind(
            "<Configure>",
            lambda event: self.map_detail_label.configure(
                wraplength=max(180, event.width - 24)
            ),
        )
        self._build_map_legend(legend_tab)

        map_panes.add(canvas_frame, weight=4)
        map_panes.add(inspector, weight=1)
        panes.add(map_frame, weight=3)

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
        self.current_reason_label = ttk.Label(
            situation,
            textvariable=self.current_reason_var,
            style="Reason.TLabel",
            justify="left",
        )
        self.current_reason_label.pack(fill="x", anchor="w", pady=(3, 6))
        output_column.bind(
            "<Configure>",
            lambda event: self.current_reason_label.configure(
                wraplength=max(220, event.width - 36)
            ),
        )
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
        panes.add(output_column, weight=2)

    def _build_map_legend(self, parent: ttk.Frame) -> None:
        legend = ttk.Frame(parent)
        legend.pack(fill="both", expand=True)
        items = [
            ("Camera", "camera"),
            ("Visit heat", "cell_repeat"),
            ("Path", "path"),
            ("Wall", "wall"),
            ("Exit guess", "guess_exit"),
            ("Character guess", "guess_character"),
            ("Object guess", "guess_object"),
            ("Interaction", "interactable"),
            ("Exit: unknown", "warp"),
            ("Exit: progression", "warp_progression"),
            ("Exit: new area", "warp_new_area"),
            ("Exit: optional", "warp_optional"),
            ("Exit: return", "warp_return"),
            ("Exit: loop", "warp_loop"),
            ("Kris", "player"),
        ]
        legend.columnconfigure(0, weight=1)
        legend.columnconfigure(1, weight=1)
        legend.columnconfigure(2, weight=1)
        for index, (label, color) in enumerate(items):
            item = ttk.Frame(legend)
            item.grid(
                row=index // 3,
                column=index % 3,
                sticky="w",
                padx=(0, 10),
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
            "MapTitle.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 11),
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
            "TMenubutton",
            background=colors["panel_alt"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            arrowcolor=colors["text"],
            padding=(9, 5),
        )
        self.style.map(
            "TMenubutton",
            background=[("active", colors["border"])],
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
        self.style.configure(
            "TNotebook",
            background=colors["panel"],
            bordercolor=colors["border"],
            tabmargins=(2, 2, 2, 0),
        )
        self.style.configure(
            "TNotebook.Tab",
            background=colors["panel_alt"],
            foreground=colors["muted"],
            padding=(10, 5),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[
                ("selected", colors["field"]),
                ("active", colors["border"]),
            ],
            foreground=[("selected", colors["text"])],
        )
        self.style.configure(
            "Treeview",
            background=colors["field"],
            fieldbackground=colors["field"],
            foreground=colors["text"],
            rowheight=24,
            bordercolor=colors["border"],
        )
        self.style.map(
            "Treeview",
            background=[("selected", colors["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        self.style.configure(
            "Treeview.Heading",
            background=colors["panel_alt"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 8),
        )

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
        for menu in self.popup_menus:
            menu.configure(
                background=colors["field"],
                foreground=colors["text"],
                activebackground=colors["accent"],
                activeforeground="#ffffff",
                selectcolor=colors["accent_active"],
                borderwidth=1,
                relief="solid",
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
            "--speed",
            self.speed_var.get().casefold().removesuffix("x"),
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
        map_changed = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "ai":
                    self._append(self.ai_output, str(payload) + "\n")
                elif kind == "event":
                    map_changed = self._handle_event(payload) or map_changed
                elif kind == "exit":
                    self._handle_exit(int(payload))
        except queue.Empty:
            pass
        if map_changed:
            # A busy controller can enqueue several steps between UI polls.
            # Apply all of them first, then render once at the newest state.
            self._refresh_room_choices(
                select_current=self.follow_room_var.get()
            )
            self._redraw_map()
        if self.closing:
            self._finish_close_if_ready()
        else:
            self.root.after(50, self._poll_events)

    def _handle_event(self, payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
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
            return False
        self.speed_status_var.set(format_speed_status(payload.get("speed")))
        self.map_model.update(payload)
        self._display_ai_decision(payload)
        telemetry = payload.get("telemetry")
        if telemetry:
            self._append(self.telemetry_output, format_telemetry_event(payload) + "\n\n")
        return True

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
                x, y = (
                    telemetry.get("player_foot_x"),
                    telemetry.get("player_foot_y"),
                )
                if x is None or y is None:
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
                and str(record.get("guess_state") or "proposed")
                not in {"confirmed", "rejected", "retired"}
                and int(
                    record.get("completed_tests", record.get("inspections", 0))
                ) < VISUAL_GUESS_STORY_INSPECTIONS
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
        self.speed_status_var.set("Game: unknown | AI: stopped")
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)
            self.stop_file = None

    def _refresh_room_choices(self, select_current: bool) -> None:
        rooms = sorted(self.map_model.rooms)
        self.room_box.configure(values=rooms)
        previous = self.room_var.get()
        if select_current and self.map_model.current_room in rooms:
            self.room_var.set(self.map_model.current_room or "")
        elif self.room_var.get() not in rooms:
            preferred = "room_krisroom" if "room_krisroom" in rooms else (rooms[0] if rooms else "")
            self.room_var.set(preferred)
        if self.room_var.get() != previous:
            self._clear_map_selection()

    def _clear_map_selection(self) -> None:
        self._selected_map_target = None
        self._selected_guess_key = None
        self.guess_detail_var.set(
            "Select a lead to see its full evidence and exact map extent."
        )

    def _select_room_from_ui(self, _event: tk.Event | None = None) -> None:
        # A manual room choice is intentional browsing. Leaving Follow checked
        # made the next telemetry packet silently replace what the user chose.
        self.follow_room_var.set(False)
        self._clear_map_selection()
        self._redraw_map()

    def _toggle_follow_room(self) -> None:
        if self.follow_room_var.get() and self.map_model.current_room:
            if self.room_var.get() != self.map_model.current_room:
                self.room_var.set(self.map_model.current_room)
                self._clear_map_selection()
        self._redraw_map()

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
        old_transform = self._map_transform
        world_x = (
            (event.x - old_transform.offset_x) / old_transform.scale
            + old_transform.min_x
        )
        world_y = (
            (event.y - old_transform.offset_y) / old_transform.scale
            + old_transform.min_y
        )
        state = self._room_view_state(room_name)
        factor = 1.25 if event.delta > 0 else 0.8
        state["zoom"] = min(12.0, max(0.20, state["zoom"] * factor))
        self._redraw_map()
        if self._map_transform is not None:
            mapped_x, mapped_y = self._map_transform.cell_boundary(
                (world_x, world_y)
            )
            state["pan_x"] += event.x - mapped_x
            state["pan_y"] += event.y - mapped_y
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
            self.map_title_var.set(room_name or "No room selected")
            self.map_stats_var.set("Waiting for learned room data")
            self._refresh_guess_panel(room_name, room)
            canvas.create_text(
                18,
                18,
                anchor="nw",
                text="No learned map data yet.",
                fill=self.colors["muted"],
                font=("Segoe UI", 10),
            )
            if self._selected_map_target is None:
                self.map_detail_var.set("No map data yet.")
            self._map_transform = None
            return

        points = set(room.cells)
        for source_x, source_y, target_x, target_y in room.open_edges:
            points.update(((source_x, source_y), (target_x, target_y)))
        for x, y, _direction in room.blocked_edges:
            points.add((x, y))
        points.update(room.interactables)
        for (x, y, _target_room), record in room.warps.items():
            points.add((x, y))
            footprint = record.get("source_footprint")
            bounds = footprint.get("bounds") if isinstance(footprint, dict) else None
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                try:
                    min_warp_x, min_warp_y, max_warp_x, max_warp_y = (
                        int(value) for value in bounds
                    )
                    points.update(
                        (
                            (min_warp_x, min_warp_y),
                            (max_warp_x, max_warp_y),
                        )
                    )
                except (TypeError, ValueError):
                    pass
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
            self.show_current_view_var.get()
            and
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
        base_offset_x = 18.0
        base_offset_y = 18.0
        fit_scale = min(
            30.0,
            (width - base_offset_x * 2) / max(1, max_x - min_x + 2),
            (height - base_offset_y * 2) / max(1, max_y - min_y + 2),
        )
        scale = max(0.15, fit_scale * view_state["zoom"])
        offset_x = base_offset_x + view_state["pan_x"]
        offset_y = base_offset_y + view_state["pan_y"]
        transform = MapTransform(min_x, min_y, scale, offset_x, offset_y)
        self._map_transform = transform

        def center(cell: tuple[float, float]) -> tuple[float, float]:
            return transform.cell_center(cell)

        left, top = transform.cell_boundary((min_x, min_y))
        right, bottom = transform.cell_boundary((max_x + 1, max_y + 1))
        remembered_view_visible = self.show_room_view_var.get() and bool(
            room.view_tiles
        )
        if remembered_view_visible:
            for (region_x, region_y), record in sorted(room.view_tiles.items()):
                try:
                    tile_left, tile_top, tile_right, tile_bottom = (
                        transform.region_box((region_x, region_y))
                    )
                    image_x = round(tile_left)
                    image_y = round(tile_top)
                    image_right = round(tile_right)
                    image_bottom = round(tile_bottom)
                    rendered_size = (
                        max(1, image_right - image_x),
                        max(1, image_bottom - image_y),
                    )
                    tile_path = Path(str(record["path"]))
                    cache_key = (
                        str(tile_path),
                        int(record.get("mtime_ns") or tile_path.stat().st_mtime_ns),
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
                            if max(rendered_size) < tile.width
                            else Image.Resampling.NEAREST
                        )
                        tile = tile.resize(
                            rendered_size,
                            resampling,
                        )
                        displayed = ImageTk.PhotoImage(tile)
                        if len(self._map_image_cache) >= 512:
                            self._map_image_cache.clear()
                        self._map_image_cache[cache_key] = displayed
                    self._map_images.append(displayed)
                    canvas.create_image(
                        image_x,
                        image_y,
                        image=displayed,
                        anchor="nw",
                    )
                except (OSError, KeyError, TypeError, ValueError, tk.TclError):
                    continue

        navigation_visible = self.show_navigation_var.get()
        grid_visible = self.show_grid_var.get() and scale >= 5
        for grid_x in (range(min_x, max_x + 2) if grid_visible else ()):
            x = offset_x + (grid_x - min_x) * scale
            canvas.create_line(x, top, x, bottom, fill=self.colors["grid"])
        for grid_y in (range(min_y, max_y + 2) if grid_visible else ()):
            y = offset_y + (grid_y - min_y) * scale
            canvas.create_line(left, y, right, y, fill=self.colors["grid"])

        visit_heat_visible = self.show_visits_var.get() or not remembered_view_visible
        for x, y in (sorted(room.cells) if visit_heat_visible else ()):
            cx, cy = center((x, y))
            visits = room.visits.get((x, y), 1)
            cell_color = (
                self.colors["cell_hot"]
                if visits >= 20
                else self.colors["cell_repeat"] if visits >= 5 else self.colors["cell"]
            )
            if remembered_view_visible:
                radius = min(4.0, max(1.5, scale * 0.13))
                canvas.create_oval(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    fill=cell_color,
                    outline="",
                )
            else:
                half = scale * 0.43
                canvas.create_rectangle(
                    cx - half,
                    cy - half,
                    cx + half,
                    cy + half,
                    fill=cell_color,
                    outline=self.colors["cell_outline"] if scale >= 6 else "",
                )
        for source_x, source_y, target_x, target_y in (
            sorted(room.open_edges) if navigation_visible else ()
        ):
            canvas.create_line(
                *center((source_x, source_y)),
                *center((target_x, target_y)),
                fill=self.colors["path"],
                width=min(3, max(1, round(scale * 0.07))),
            )
        guesses = visual_guess_entries(
            room_name,
            room,
            self.map_model.current_visible_regions,
        )
        self._refresh_guess_panel(room_name, room, guesses)
        if self.show_guesses_var.get():
            occupied_markers: list[tuple[float, float, float]] = []

            def place_guess_marker(
                desired: tuple[float, float],
                radius: float,
                edge_hint: str | None,
            ) -> tuple[float, float]:
                outward = {
                    "top": (0.0, -1.0),
                    "right": (1.0, 0.0),
                    "bottom": (0.0, 1.0),
                    "left": (-1.0, 0.0),
                }.get(edge_hint, (1.0, -1.0))
                step = radius * 2.35
                candidates = [
                    desired,
                    (desired[0] + outward[0] * step, desired[1] + outward[1] * step),
                    (desired[0] + step, desired[1] - step),
                    (desired[0] - step, desired[1] - step),
                    (desired[0] + step, desired[1] + step),
                    (desired[0] - step, desired[1] + step),
                    (desired[0] + step * 2, desired[1]),
                    (desired[0] - step * 2, desired[1]),
                ]
                margin = radius + 3
                for candidate_x, candidate_y in candidates:
                    candidate_x = min(width - margin, max(margin, candidate_x))
                    candidate_y = min(height - margin, max(margin, candidate_y))
                    if all(
                        (candidate_x - used_x) ** 2 + (candidate_y - used_y) ** 2
                        >= (radius + used_radius + 4) ** 2
                        for used_x, used_y, used_radius in occupied_markers
                    ):
                        occupied_markers.append((candidate_x, candidate_y, radius))
                        return candidate_x, candidate_y
                candidate_x, candidate_y = candidates[-1]
                occupied_markers.append((candidate_x, candidate_y, radius))
                return candidate_x, candidate_y

            for guess in guesses:
                color_key = {
                    "possible_character": "guess_character",
                    "possible_interactable": "guess_object",
                    "possible_exit": "guess_exit",
                }.get(guess.hypothesis, "hypothesis")
                color = self.colors[color_key]
                pursued = (
                    (
                        self.map_model.current_guess_id is not None
                        and guess.stable_id == self.map_model.current_guess_id
                    )
                    or (
                    self.map_model.current_guess_region is not None
                    and self.map_model.current_guess_region[0] == room_name
                    and (
                        self.map_model.current_guess_region[1],
                        self.map_model.current_guess_region[2],
                    ) in guess.regions
                    )
                )
                if self.show_guess_sources_var.get():
                    for region_x, region_y in guess.regions:
                        x1, y1, x2, y2 = transform.region_box(
                            (region_x, region_y)
                        )
                        canvas.create_rectangle(
                            x1,
                            y1,
                            x2,
                            y2,
                            outline=self.colors["muted"],
                            width=1,
                            dash=(3, 4),
                        )
                if guess.feature_box_world is not None:
                    box_left, box_top, box_right, box_bottom = transform.world_box(
                        guess.feature_box_world
                    )
                    if box_right - box_left < 3:
                        center_x = (box_left + box_right) / 2
                        box_left, box_right = center_x - 1.5, center_x + 1.5
                    if box_bottom - box_top < 3:
                        center_y = (box_top + box_bottom) / 2
                        box_top, box_bottom = center_y - 1.5, center_y + 1.5
                    canvas.create_rectangle(
                        box_left,
                        box_top,
                        box_right,
                        box_bottom,
                        outline=color,
                        width=3 if pursued else 2,
                        dash=(7, 3) if guess.hypothesis == "possible_exit" else (),
                    )
                desired_x, desired_y = transform.world_point(guess.anchor_world)
                radius = min(14.0, max(8.0, scale * 0.34))
                cx, cy = place_guess_marker(
                    (desired_x, desired_y),
                    radius,
                    guess.edge_hint,
                )
                if abs(cx - desired_x) > 2 or abs(cy - desired_y) > 2:
                    canvas.create_line(
                        desired_x,
                        desired_y,
                        cx,
                        cy,
                        fill=color,
                        width=1,
                        dash=(3, 2),
                    )
                if pursued:
                    canvas.create_oval(
                        cx - radius - 4,
                        cy - radius - 4,
                        cx + radius + 4,
                        cy + radius + 4,
                        outline=self.colors["selection"],
                        width=3,
                    )
                canvas.create_oval(
                    cx - radius,
                    cy - radius,
                    cx + radius,
                    cy + radius,
                    fill=color,
                    outline=self.colors["field"],
                    width=2,
                )
                canvas.create_text(
                    cx,
                    cy,
                    text=guess.marker,
                    fill="#111722" if self.dark_mode_var.get() else "#ffffff",
                    font=("Segoe UI Semibold", max(7, round(radius * 0.72))),
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
                width=min(5, 2 + max(1, failures // 2)),
            )
        objects_visible = self.show_objects_var.get()
        for (x, y), record in (
            sorted(room.interactables.items()) if objects_visible else ()
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
        occupied_exit_labels: list[tuple[float, float]] = []
        for exit_number, ((x, y, _target_room), record) in (
            numbered_exits if objects_visible else ()
        ):
            badge, color_key, _role_description = warp_role_badge(
                record.get("role")
            )
            color = self.colors[color_key]
            footprint = record.get("source_footprint")
            bounds = (
                footprint.get("bounds")
                if isinstance(footprint, dict)
                else None
            )
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                try:
                    min_exit_x, min_exit_y, max_exit_x, max_exit_y = (
                        int(value) for value in bounds
                    )
                except (TypeError, ValueError):
                    min_exit_x = max_exit_x = x
                    min_exit_y = max_exit_y = y
            else:
                min_exit_x = max_exit_x = x
                min_exit_y = max_exit_y = y
            aperture_left, aperture_top = transform.cell_boundary(
                (min_exit_x, min_exit_y)
            )
            aperture_right, aperture_bottom = transform.cell_boundary(
                (max_exit_x + 1, max_exit_y + 1)
            )
            aperture = record.get("aperture")
            aperture_axis = (
                str(aperture.get("axis") or "point")
                if isinstance(aperture, dict)
                else "point"
            )
            thickness = max(3.0, scale * 0.18)
            if aperture_axis == "horizontal":
                mid_y = (aperture_top + aperture_bottom) / 2
                aperture_top, aperture_bottom = (
                    mid_y - thickness,
                    mid_y + thickness,
                )
            elif aperture_axis == "vertical":
                mid_x = (aperture_left + aperture_right) / 2
                aperture_left, aperture_right = (
                    mid_x - thickness,
                    mid_x + thickness,
                )
            canvas.create_rectangle(
                aperture_left,
                aperture_top,
                aperture_right,
                aperture_bottom,
                outline=color,
                width=3,
            )
            source_cx = (aperture_left + aperture_right) / 2
            source_cy = (aperture_top + aperture_bottom) / 2
            dx, dy = DIRECTION_VECTORS.get(
                str(record.get("action") or ""),
                (1, -1),
            )
            radius = max(7.0, min(12.0, scale * 0.34))
            label_x = source_cx + dx * radius * 2.1
            label_y = source_cy + dy * radius * 2.1
            for offset in range(6):
                candidate_x = min(
                    width - radius - 2,
                    max(radius + 2, label_x + (-dy) * offset * radius * 1.8),
                )
                candidate_y = min(
                    height - radius - 2,
                    max(radius + 2, label_y + dx * offset * radius * 1.8),
                )
                if all(
                    (candidate_x - used_x) ** 2
                    + (candidate_y - used_y) ** 2
                    >= (radius * 2.2) ** 2
                    for used_x, used_y in occupied_exit_labels
                ):
                    label_x, label_y = candidate_x, candidate_y
                    break
            occupied_exit_labels.append((label_x, label_y))
            canvas.create_line(
                source_cx,
                source_cy,
                label_x,
                label_y,
                fill=color,
                width=1,
            )
            canvas.create_oval(
                label_x - radius,
                label_y - radius,
                label_x + radius,
                label_y + radius,
                fill=color,
                outline=self.colors["field"],
                width=2,
            )
            canvas.create_text(
                label_x,
                label_y,
                text=f"{badge}{exit_number}",
                fill="#ffffff",
                font=("Segoe UI Semibold", max(7, round(radius * 0.7))),
            )
        if (
            self.show_current_view_var.get()
            and
            self.map_model.current_camera is not None
            and self.map_model.current_camera[0] == room_name
        ):
            _camera_room, camera_x, camera_y, camera_width, camera_height = (
                self.map_model.current_camera
            )
            camera_left, camera_top, camera_right, camera_bottom = (
                transform.world_box(
                    (
                        camera_x,
                        camera_y,
                        camera_x + camera_width,
                        camera_y + camera_height,
                    )
                )
            )
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
            and (
                self.map_model.current_display_position is not None
                or self.map_model.current_cell is not None
            )
        ):
            if self.map_model.current_display_position is not None:
                player_x, player_y = self.map_model.current_display_position
                cx, cy = transform.world_point((player_x, player_y))
            else:
                assert self.map_model.current_cell is not None
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
        if self._selected_map_target is not None:
            selected_room, selected_cell = self._selected_map_target
            if selected_room == room_name:
                cx, cy = center(selected_cell)
                half = max(6.0, scale * 0.48)
                canvas.create_rectangle(
                    cx - half,
                    cy - half,
                    cx + half,
                    cy + half,
                    outline=self.colors["selection"],
                    width=2,
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
        active_hypotheses = sum(
            bool(record.get("hypothesis"))
            and str(record.get("guess_state") or "proposed")
            not in {"confirmed", "rejected", "retired"}
            and int(record.get("completed_tests", record.get("inspections", 0)))
            < VISUAL_GUESS_STORY_INSPECTIONS
            for record in room.screen_regions.values()
        )
        tested_hypotheses = sum(
            bool(record.get("hypothesis"))
            and (
                str(record.get("guess_state") or "proposed")
                in {"confirmed", "rejected", "retired"}
                or int(
                    record.get("completed_tests", record.get("inspections", 0))
                ) >= VISUAL_GUESS_STORY_INSPECTIONS
            )
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
        self.map_title_var.set(f"Viewing map: {room_name}")
        self.map_zoom_var.set(f"{view_state['zoom'] * 100:.0f}%")
        scope = (
            f"  |  AI last observed in {self.map_model.current_room}"
            if self.map_model.current_room
            and self.map_model.current_room != room_name
            else ""
        )
        role_counts = Counter(
            str(record.get("role") or "unknown")
            for record in room.warps.values()
        )
        role_summary = " ".join(
            f"{badge}{role_counts.get(role, 0)}"
            for role, (badge, _color, _description) in WARP_ROLE_BADGES.items()
        )
        self.map_stats_var.set(
            f"{len(room.cells)} cells  |  {explored_regions} regions  |  "
            f"{len(room.view_tiles)} scene tiles  |  zoom {self.map_zoom_var.get()}\n"
            f"{len(guesses)} active guesses  |  {len(room.warps)} confirmed exits "
            f"({role_summary}){scope}"
        )
        if self._selected_map_target is None:
            self.map_detail_var.set(
                "Click the map or select an AI guess for persistent details. "
                f"{active_hypotheses} active leads; {tested_hypotheses} fully tested."
                f"{camera_summary}"
            )

    def _refresh_guess_panel(
        self,
        room_name: str,
        room: RoomMap | None,
        guesses: list[VisualGuessEntry] | None = None,
    ) -> None:
        if not hasattr(self, "guess_tree"):
            return
        guesses = (
            guesses
            if guesses is not None
            else visual_guess_entries(
                room_name,
                room,
                self.map_model.current_visible_regions,
            )
            if room is not None
            else []
        )
        for item in self.guess_tree.get_children():
            self.guess_tree.delete(item)
        self._guess_tree_targets = {}
        restored_marker = None
        for guess in guesses:
            anchor = f"route ({guess.anchor_cell[0]:.0f}, {guess.anchor_cell[1]:.0f})"
            pursued = (
                (
                    self.map_model.current_guess_id is not None
                    and guess.stable_id == self.map_model.current_guess_id
                )
                or (
                self.map_model.current_guess_region is not None
                and self.map_model.current_guess_region[0] == room_name
                and (
                    self.map_model.current_guess_region[1],
                    self.map_model.current_guess_region[2],
                ) in guess.regions
                )
            )
            status = f"pursuing now; {guess.status}" if pursued else guess.status
            self.guess_tree.insert(
                "",
                "end",
                iid=guess.marker,
                values=(
                    guess.marker,
                    guess.label,
                    anchor,
                    guess.evidence,
                    status,
                ),
                tags=("pursued",) if pursued else (),
            )
            self._guess_tree_targets[guess.marker] = guess
            if self._selected_guess_key == (room_name, guess.regions):
                restored_marker = guess.marker
        self.guess_tree.tag_configure(
            "pursued",
            foreground=self.colors["accent_active"],
        )
        if restored_marker is not None:
            self.guess_tree.selection_set(restored_marker)
            self.guess_tree.focus(restored_marker)
            self.guess_tree.see(restored_marker)
        elif self._selected_guess_key is None:
            self.guess_detail_var.set(
                "No active player-observed leads in this room."
                if not guesses
                else f"{len(guesses)} active lead{'s' if len(guesses) != 1 else ''}. "
                "Select one for its complete evidence, lifecycle, and exact extent."
            )

    def _resize_guess_columns(self, event: tk.Event) -> None:
        if not hasattr(self, "guess_tree"):
            return
        available = max(520, int(event.width) - 34)
        marker_width = 42
        anchor_width = 108
        flexible = max(300, available - marker_width - anchor_width)
        widths = {
            "marker": marker_width,
            "where": anchor_width,
            "guess": round(flexible * 0.29),
            "evidence": round(flexible * 0.43),
            "status": round(flexible * 0.28),
        }
        for column, column_width in widths.items():
            self.guess_tree.column(column, width=column_width)

    def _select_guess(self, _event: tk.Event | None = None) -> None:
        selection = self.guess_tree.selection()
        if not selection:
            return
        guess = self._guess_tree_targets.get(selection[0])
        if guess is None:
            return
        room_name = self.room_var.get()
        guess_key = (room_name, guess.regions)
        if guess_key == self._selected_guess_key:
            return
        self._selected_guess_key = guess_key
        cell = (round(guess.anchor_cell[0]), round(guess.anchor_cell[1]))
        self._selected_map_target = (room_name, cell)
        region_text = ", ".join(f"({x}, {y})" for x, y in guess.regions)
        extent_text = "not available"
        if guess.feature_box_world is not None:
            left, top, right, bottom = guess.feature_box_world
            extent_text = (
                f"world ({left:.1f}, {top:.1f}) to ({right:.1f}, {bottom:.1f})"
            )
        self.map_detail_var.set(
            f"{guess.marker} — {guess.label}\n"
            f"Stable lead: {guess.stable_id}. Route anchor: map cell "
            f"({cell[0]}, {cell[1]}). Visual anchor: "
            f"({guess.anchor_world[0]:.1f}, {guess.anchor_world[1]:.1f}).\n"
            f"Feature extent: {extent_text}. Source region"
            f"{'s' if len(guess.regions) != 1 else ''}: {region_text}.\n"
            f"Evidence: {guess.evidence}.\n"
            f"Evidence type: {guess.evidence_kind.replace('_', ' ')}. "
            f"Score: {guess.confidence:.0%} (ranking strength, not certainty).\n"
            f"Status: {guess.status}. The outline follows the whole observed "
            "feature; storage regions are hidden unless the diagnostic layer is enabled."
        )
        self.guess_detail_var.set(
            f"{guess.marker}: {guess.label} — {guess.evidence}. "
            f"Score {guess.confidence:.0%}; {guess.status}."
        )
        if self._map_transform is not None:
            current_x, current_y = self._map_transform.world_point(guess.anchor_world)
            state = self._room_view_state(room_name)
            state["pan_x"] += self.map_canvas.winfo_width() / 2 - current_x
            state["pan_y"] += self.map_canvas.winfo_height() / 2 - current_y
        self._redraw_map()

    def _inspect_map_cell(self, event: tk.Event) -> None:
        room_name = self.room_var.get()
        room = self.map_model.rooms.get(room_name)
        if room is None or self._map_transform is None:
            return
        cell = self._map_transform.canvas_cell((event.x, event.y))
        self._selected_guess_key = None
        self._selected_map_target = (room_name, cell)
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
            matching_guess = next(
                (
                    guess
                    for guess in visual_guess_entries(
                        room_name,
                        room,
                        self.map_model.current_visible_regions,
                    )
                    if region_key in guess.regions
                ),
                None,
            )
            if matching_guess is not None:
                details.append(
                    f"{matching_guess.marker}={matching_guess.label}; "
                    f"{matching_guess.evidence}; evidence score "
                    f"{matching_guess.confidence:.0%}; {matching_guess.status}"
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
            f"{direction} x{failures}"
            for (x, y, direction), failures in room.blocked_edges.items()
            if (x, y) == cell
        ]
        if walls:
            details.append("walls=" + ", ".join(sorted(walls)))
        warps = []
        for (x, y, target_room), record in room.warps.items():
            footprint = record.get("source_footprint")
            bounds = footprint.get("bounds") if isinstance(footprint, dict) else None
            on_aperture = (x, y) == cell
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                try:
                    min_warp_x, min_warp_y, max_warp_x, max_warp_y = (
                        int(value) for value in bounds
                    )
                    on_aperture = (
                        min_warp_x <= cell[0] <= max_warp_x
                        and min_warp_y <= cell[1] <= max_warp_y
                    )
                except (TypeError, ValueError):
                    pass
            if not on_aperture:
                continue
            _badge, _color, role_description = warp_role_badge(record.get("role"))
            try:
                role_score = f"{float(record.get('role_confidence', 0.25)):.0%}"
            except (TypeError, ValueError):
                role_score = "unscored"
            basis = record.get("role_basis")
            basis_text = (
                "; ".join(str(value) for value in basis)
                if isinstance(basis, (list, tuple)) and basis
                else "more observed outcomes are needed"
            )
            warps.append(
                f"confirmed exit to {target_room}; {role_description} ({role_score}); "
                f"because {basis_text}; crossed with {record.get('action')} "
                f"{record.get('count')}x; destination spawned Kris near "
                f"{record.get('target_cell')} (arrival only)"
            )
        details.extend(warps)
        if visits == 0 and cell not in room.cells:
            details.append("visible but not yet traversed" if screen_region else "unmapped")
        self.map_detail_var.set(" | ".join(details))
        self._redraw_map()

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
