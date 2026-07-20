from collections import deque
from math import hypot
from pathlib import Path

from .actions import ACTIONS, Action
from .observer import Observation
from .perception import GameState, Perception
from .telemetry import TelemetrySample
from .world_model import CELL_SIZE, Warp, WorldModel


DIRECTION_VECTORS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}
MOVEMENT_COMMIT_STEPS = 3
COLLISION_CONFIRM_SAMPLES = 3
EXPLORATION_REGION_CELLS = 4
WARP_SEEK_STEPS = 30
INTERACTION_MEMORY_RADIUS = 6


class StarterPolicy:
    """Deterministic frontier explorer with state-aware collision learning."""

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        self.world = WorldModel.load(memory_path)
        self.memory_warning = self.world.load_warning
        self.fallback_offset = seed % len(DIRECTION_VECTORS)
        self.direction = "down"
        self.direction_steps = 0
        self.last_movement: str | None = None
        self.last_position: tuple[float, float] | None = None
        self.last_room: str | None = None
        self.last_cell: tuple[int, int] | None = None
        self.observed_room: str | None = None
        self.observed_cell: tuple[int, int] | None = None
        self.failed_movement = False
        self.collision_pending = False
        self.stationary_streak = 0
        self.stationary_key: tuple[str, int, int, str] | None = None
        self.awaiting_fresh_telemetry = False
        self.last_movement_sample_at: float | None = None
        self.input_not_registered = False
        self.unregistered_input_streak = 0
        self.unexpected_displacement = False
        self.interaction_tried = False
        self.pending_blocked_direction: str | None = None
        self.visits = self.world.visits
        self.blocked = self.world.blocked
        self.blocked_zones = set(self.blocked)
        self.tried = self.world.tried
        self.tried_regions = {
            (room, x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS, direction)
            for room, x, y, direction in self.tried
        }
        self.open_edges = self.world.open_edges
        self.seen_cells = self.world.seen_cells
        self.seen_regions = {
            (room, x // EXPLORATION_REGION_CELLS, y // EXPLORATION_REGION_CELLS)
            for room, x, y in self.seen_cells
        }
        self.interacted_zones: set[tuple[str, int, int]] = set()
        self.interacted_targets: set[tuple[str, int, int]] = set()
        self.interacted_instances: set[tuple[str, int]] = set()
        self.interactables = self.world.interactables
        for (known_room, target_x, target_y), record in self.interactables.items():
            self.interacted_targets.add((known_room, target_x, target_y))
            for approach in record.get("approaches", []):
                self.interacted_zones.add(
                    (known_room, int(approach["x"]), int(approach["y"]))
                )
            instance_id = record.get("instance_id")
            if isinstance(instance_id, int) and instance_id >= 0:
                self.interacted_instances.add((known_room, instance_id))
        self.interaction_candidate: (
            tuple[str, int, int, str, int | None, str | None, int, int] | None
        ) = None
        self.completed_interaction: tuple[str, int, int, str] | None = None
        self.transitions = self.world.transitions
        self.warps = self.world.warps
        self.room_entry_from: dict[str, str] = {}
        self.recent_cells: deque[tuple[str, int, int]] = deque(maxlen=24)
        self.decision_history: deque[tuple[str, int, int, str]] = deque(maxlen=8)
        self.oscillation_breaks = 0
        self.steps_without_frontier = 0
        self.reason = "starting"
        self.map_updates: list[dict[str, object]] = []

    def choose(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None = None,
    ) -> Action:
        state = perception.state
        if state in {GameState.DIALOGUE, GameState.MENU}:
            self._complete_pending_interaction()
        if state is GameState.OVERWORLD and telemetry and telemetry.mode == "overworld":
            self._observe_room(telemetry)
            self._learn_movement_result(telemetry)
        else:
            self._suspend_movement_learning()
        if state is GameState.DIALOGUE:
            return self._select("confirm", "advance dialogue", telemetry)
        if state is GameState.BATTLE:
            # A deterministic dodge cycle is easier to inspect than random key mashing.
            names = ["left", "up", "right", "down", "confirm"]
            return self._select(names[observation.step % len(names)], "battle cycle", telemetry)
        if state is GameState.MENU:
            return self._select("confirm", "accept current menu choice", telemetry)
        if state is GameState.UNKNOWN:
            name = "confirm" if observation.step % 8 == 0 else "wait"
            return self._select(name, "wait through unknown state", telemetry)

        if telemetry and telemetry.mode == "overworld":
            return self._explore(telemetry)

        # Vision-only fallback when the telemetry patch is unavailable.
        if self.direction_steps <= 0:
            directions = ["down", "right", "up", "left"]
            index = (observation.step // 12 + self.fallback_offset) % len(directions)
            self.direction = directions[index]
            self.direction_steps = 12
        self.direction_steps -= 1
        return self._select(self.direction, "vision-only exploration", telemetry)

    @staticmethod
    def _room_key(telemetry: TelemetrySample) -> str:
        return telemetry.room_name or str(telemetry.room_id)

    @staticmethod
    def _cell(telemetry: TelemetrySample) -> tuple[int, int]:
        return int(telemetry.x // CELL_SIZE), int(telemetry.y // CELL_SIZE)

    @staticmethod
    def _region(cell: tuple[int, int]) -> tuple[int, int]:
        return (
            cell[0] // EXPLORATION_REGION_CELLS,
            cell[1] // EXPLORATION_REGION_CELLS,
        )

    def _observe_room(self, telemetry: TelemetrySample) -> None:
        room = self._room_key(telemetry)
        cell = self._cell(telemetry)
        if self.observed_room is not None and room != self.observed_room:
            source_room = self.observed_room
            self.transitions[(source_room, room)] += 1
            self.room_entry_from[room] = source_room
            if self.observed_cell is not None:
                action = self.last_movement or "event"
                warp = (
                    source_room,
                    *self.observed_cell,
                    action,
                    room,
                    *cell,
                )
                self.warps[warp] += 1
                self.map_updates.append(
                    {
                        "type": "warp",
                        "from_room": source_room,
                        "from_cell": list(self.observed_cell),
                        "action": action,
                        "to_room": room,
                        "to_cell": list(cell),
                        "count": self.warps[warp],
                    }
                )
        self.observed_room = room
        self.observed_cell = cell

    def observe_room_trace(self, samples: list[TelemetrySample]) -> None:
        """Observe every ordered room packet, including multiple warps per step."""
        for sample in samples:
            self._observe_room(sample)

    def _learn_movement_result(self, telemetry: TelemetrySample | None) -> None:
        if not telemetry or not self.last_movement or self.last_position is None:
            return
        room = self._room_key(telemetry)
        if room != self.last_room:
            self.failed_movement = False
            self._reset_collision_evidence()
            self.interaction_tried = False
            self.pending_blocked_direction = None
            self.direction_steps = 0
            return
        if (
            self.last_movement_sample_at is not None
            and telemetry.received_at <= self.last_movement_sample_at
        ):
            self.awaiting_fresh_telemetry = True
            self.failed_movement = False
            self.collision_pending = False
            return
        self.awaiting_fresh_telemetry = False
        delta_x = telemetry.x - self.last_position[0]
        delta_y = telemetry.y - self.last_position[1]
        distance = hypot(delta_x, delta_y)
        stationary = distance < 0.75
        self.input_not_registered = (
            stationary
            and telemetry.facing_direction is not None
            and telemetry.facing_direction != self.last_movement
        )
        if self.input_not_registered:
            self.failed_movement = False
            self.collision_pending = False
            self.stationary_streak = 0
            self.stationary_key = None
            self.unregistered_input_streak += 1
        elif stationary:
            key = (
                room,
                round(telemetry.x),
                round(telemetry.y),
                self.last_movement,
            )
            if key == self.stationary_key:
                self.stationary_streak += 1
            else:
                self.stationary_key = key
                self.stationary_streak = 1
            self.failed_movement = self.stationary_streak >= COLLISION_CONFIRM_SAMPLES
            self.collision_pending = not self.failed_movement
            self.unregistered_input_streak = 0
        else:
            self.failed_movement = False
            self.collision_pending = False
            self.stationary_streak = 0
            self.stationary_key = None
        if not self.failed_movement:
            current_cell = self._cell(telemetry)
            vector_x, vector_y = DIRECTION_VECTORS[self.last_movement]
            forward = delta_x * vector_x + delta_y * vector_y
            lateral = abs(delta_x * vector_y - delta_y * vector_x)
            aligned_with_action = forward > 0.75 and lateral <= max(2.0, forward * 0.35)
            self.unexpected_displacement = distance >= 0.75 and not aligned_with_action
            if (
                aligned_with_action
                and self.last_cell is not None
                and current_cell != self.last_cell
            ):
                self._remember_open_path(
                    room,
                    self.last_cell,
                    self.last_movement,
                    current_cell,
                )
            if aligned_with_action:
                self._forget_contradicted_block(room, self.last_cell, self.last_movement)
                self.unregistered_input_streak = 0
                self.interaction_tried = False
                self.pending_blocked_direction = None

    def _remember_open_path(
        self,
        room: str,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        """Record packet-gap movement as exact adjacent cardinal edges."""
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        forward = delta_x * vector_x + delta_y * vector_y
        lateral = abs(delta_x * vector_y - delta_y * vector_x)
        if forward <= 0 or lateral != 0:
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector_x, current[1] + vector_y)
            edge = (room, *current, direction, *following)
            reverse = (room, *following, OPPOSITE[direction], *current)
            if edge not in self.open_edges:
                self.map_updates.append(
                    {
                        "type": "open_edge",
                        "room": room,
                        "from_cell": list(current),
                        "to_cell": list(following),
                    }
                )
            self.open_edges.add(edge)
            self.open_edges.add(reverse)
            current = following

    def _reset_collision_evidence(self) -> None:
        self.collision_pending = False
        self.stationary_streak = 0
        self.stationary_key = None
        self.awaiting_fresh_telemetry = False
        self.last_movement_sample_at = None

    def _forget_contradicted_block(
        self,
        room: str,
        cell: tuple[int, int] | None,
        direction: str,
    ) -> None:
        if cell is None:
            return
        edge = (room, *cell, direction)
        if self.blocked.pop(edge, None) is not None:
            self.blocked_zones.discard(edge)
            self.map_updates.append(
                {"type": "unblocked", "room": room, "cell": list(cell), "direction": direction}
            )

    def _suspend_movement_learning(self) -> None:
        # Dialogue, menus, cutscenes, and battles can freeze player coordinates.
        # Never interpret those freezes as collisions.
        self.last_movement = None
        self.last_position = None
        self.failed_movement = False
        self._reset_collision_evidence()
        self.input_not_registered = False
        self.unregistered_input_streak = 0
        self.unexpected_displacement = False
        self.interaction_tried = False
        self.pending_blocked_direction = None

    def _complete_pending_interaction(self) -> None:
        if self.interaction_candidate is None:
            return
        (
            room,
            cell_x,
            cell_y,
            direction,
            instance_id,
            name,
            target_x,
            target_y,
        ) = self.interaction_candidate
        self.interacted_zones.add((room, cell_x, cell_y))
        if instance_id is not None:
            self.interacted_instances.add((room, instance_id))
        key = self._matching_interactable(room, (target_x, target_y), instance_id)
        if key is None:
            key = (room, target_x, target_y)
        self.interacted_targets.add(key)
        record = self.interactables.get(key, {})
        approaches = list(record.get("approaches", []))
        approach = {"x": cell_x, "y": cell_y, "direction": direction}
        if approach not in approaches:
            approaches.append(approach)
        record.update(
            {
                "name": name or record.get("name") or "interaction",
                "instance_id": instance_id if instance_id is not None else record.get("instance_id"),
                "confirmations": int(record.get("confirmations", 0)) + 1,
                "approaches": approaches,
            }
        )
        self.interactables[key] = record
        self.map_updates.append(
            {
                "type": "interactable",
                "room": room,
                "cell": [key[1], key[2]],
                "name": record["name"],
                "instance_id": record["instance_id"],
                "confirmations": record["confirmations"],
                "approaches": approaches,
                "status": "confirmed",
            }
        )
        self.completed_interaction = (room, cell_x, cell_y, direction)
        self.interaction_candidate = None

    def _matching_interactable(
        self,
        room: str,
        target: tuple[int, int],
        instance_id: int | None,
    ) -> tuple[str, int, int] | None:
        if instance_id is not None:
            for key, record in self.interactables.items():
                if key[0] == room and record.get("instance_id") == instance_id:
                    return key
            # When telemetry exposes stable instance IDs, nearby objects remain
            # distinct even if their collision boxes are close together.
            return None
        nearby = [
            key
            for key in self.interactables
            if key[0] == room
            and max(abs(key[1] - target[0]), abs(key[2] - target[1]))
            <= INTERACTION_MEMORY_RADIUS
        ]
        if not nearby:
            return None
        return min(
            nearby,
            key=lambda key: abs(key[1] - target[0]) + abs(key[2] - target[1]),
        )

    @staticmethod
    def _interaction_target(
        telemetry: TelemetrySample,
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int]:
        if (
            telemetry.nearest_interactable_x is not None
            and telemetry.nearest_interactable_y is not None
            and telemetry.nearest_interactable_distance is not None
            and telemetry.nearest_interactable_distance <= 48
        ):
            return (
                int(telemetry.nearest_interactable_x // CELL_SIZE),
                int(telemetry.nearest_interactable_y // CELL_SIZE),
            )
        dx, dy = DIRECTION_VECTORS[direction]
        return cell[0] + dx, cell[1] + dy

    @staticmethod
    def _nearby_interactable_id(telemetry: TelemetrySample) -> int | None:
        instance_id = telemetry.nearest_interactable_id
        distance = telemetry.nearest_interactable_distance
        if instance_id is None or instance_id < 0 or distance is None or distance > 48:
            return None
        return instance_id

    def _blocked_near(self, room: str, cell: tuple[int, int], direction: str) -> bool:
        # A nearby wall can contain a one-cell doorway. Only the exact attempted
        # edge is treated as blocked; successful movement can still erase it.
        return self.blocked[(room, *cell, direction)] > 0

    def _interacted_near(
        self, room: str, cell: tuple[int, int], direction: str
    ) -> bool:
        dx, dy = DIRECTION_VECTORS[direction]
        target = (cell[0] + dx, cell[1] + dy)
        return any(
            target_room == room
            and max(abs(target_x - target[0]), abs(target_y - target[1]))
            <= INTERACTION_MEMORY_RADIUS
            for target_room, target_x, target_y in self.interacted_targets
        )

    def _remember_blocked(
        self, room: str, cell: tuple[int, int], direction: str
    ) -> None:
        self.blocked[(room, *cell, direction)] += 1
        self.blocked_zones.add((room, *cell, direction))
        self.map_updates.append(
            {
                "type": "blocked",
                "room": room,
                "cell": list(cell),
                "direction": direction,
                "failures": self.blocked[(room, *cell, direction)],
            }
        )

    def _explore(self, telemetry: TelemetrySample) -> Action:
        room = self._room_key(telemetry)
        cell = self._cell(telemetry)
        self.visits[(room, *cell)] += 1
        self.seen_cells.add((room, *cell))
        region_key = (room, *self._region(cell))
        if region_key in self.seen_regions:
            self.steps_without_frontier += 1
        else:
            self.seen_regions.add(region_key)
            self.steps_without_frontier = 0
        recent = (room, *cell)
        if not self.recent_cells or self.recent_cells[-1] != recent:
            self.recent_cells.append(recent)

        if room != self.last_room:
            self.direction_steps = 0
            self.failed_movement = False
            self._reset_collision_evidence()
            self.input_not_registered = False
            self.unregistered_input_streak = 0
            self.unexpected_displacement = False
            self.interaction_tried = False
            self.pending_blocked_direction = None

        if self.completed_interaction is not None:
            interaction_room, cell_x, cell_y, old_direction = self.completed_interaction
            self.completed_interaction = None
            if interaction_room == room:
                # Some doors become passable after dialogue. Test forward once;
                # if it is still blocked, interacted_zones prevents a second Z.
                self.direction = old_direction
                self.direction_steps = 1
                return self._select(
                    self.direction,
                    f"interaction completed; test {self.direction} once",
                    telemetry,
                )

        if self.awaiting_fresh_telemetry and self.last_movement:
            direction = self.last_movement
            self.awaiting_fresh_telemetry = False
            return self._select(
                direction,
                f"await fresh telemetry before judging {direction}",
                telemetry,
            )

        if self.collision_pending and self.last_movement:
            direction = self.last_movement
            self.collision_pending = False
            return self._select(
                direction,
                f"checking blockage {direction} ({self.stationary_streak}/{COLLISION_CONFIRM_SAMPLES})",
                telemetry,
            )

        if self.input_not_registered and self.last_movement:
            old_direction = self.last_movement
            self.input_not_registered = False
            if self.unregistered_input_streak <= 1:
                self.direction = old_direction
                self.direction_steps = 1
                return self._select(
                    self.direction,
                    f"input not reflected; retry {self.direction} once",
                    telemetry,
                )
            self.unregistered_input_streak = 0
            self.direction = self._least_visited_direction(
                room, cell, old_direction, avoid={old_direction}
            )
            self.direction_steps = 1
            return self._select(
                self.direction,
                f"input remained frozen; switch from {old_direction} to {self.direction}",
                telemetry,
            )

        if self.unexpected_displacement:
            # Replan at the observed location without attributing manual movement,
            # knockback, or another external displacement to the attempted key.
            self.unexpected_displacement = False
            self.direction_steps = 0

        if self.interaction_tried and self.pending_blocked_direction:
            blocked_cell = self.last_cell or cell
            old_direction = self.pending_blocked_direction
            self._remember_blocked(room, blocked_cell, old_direction)
            self.interaction_candidate = None
            self.direction = self._least_visited_direction(
                room, cell, old_direction, avoid={old_direction}
            )
            self.direction_steps = 1
            self.failed_movement = False
            self.interaction_tried = False
            self.pending_blocked_direction = None
            return self._select(
                self.direction,
                f"learned obstacle {old_direction}; turn {self.direction}",
                telemetry,
            )

        if self.failed_movement and self.last_movement:
            if not self.interaction_tried:
                known_block = self._blocked_near(room, cell, self.last_movement)
                nearby_instance = self._nearby_interactable_id(telemetry)
                known_interaction = (
                    (room, nearby_instance) in self.interacted_instances
                    if nearby_instance is not None
                    else self._interacted_near(room, cell, self.last_movement)
                )
                if known_block or known_interaction:
                    old_direction = self.last_movement
                    self._remember_blocked(room, cell, old_direction)
                    self.direction = self._least_visited_direction(
                        room, cell, old_direction, avoid={old_direction}
                    )
                    self.direction_steps = 1
                    self.failed_movement = False
                    reason = (
                        f"known blocked {old_direction}"
                        if known_block
                        else f"completed interaction blocks {old_direction}"
                    )
                    return self._select(
                        self.direction,
                        f"{reason}; turn {self.direction}",
                        telemetry,
                    )
                self.interaction_tried = True
                self.pending_blocked_direction = self.last_movement
                target = self._interaction_target(telemetry, cell, self.last_movement)
                self.interaction_candidate = (
                    room,
                    *cell,
                    self.last_movement,
                    nearby_instance,
                    telemetry.nearest_interactable_name,
                    *target,
                )
                return self._select(
                    "confirm",
                    f"blocked {self.last_movement}; try interaction",
                    telemetry,
                )

        if self.direction_steps <= 0:
            self.direction, self.direction_steps, reason = self._plan_exploration(
                room, cell
            )
            stabilized, broke_loop = self._break_oscillation(
                room, cell, self.direction
            )
            if broke_loop:
                self.direction = stabilized
                self.direction_steps = 1
                reason = f"detected repeated corridor loop; escape {self.direction}"
            self.decision_history.append((room, *cell, self.direction))
        else:
            reason = f"continue clear path {self.direction}"
        self.direction_steps -= 1
        return self._select(self.direction, reason, telemetry)

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        frontier_route = self._route_to_nearest_frontier(room, cell)
        current_frontier = any(
            self._direction_is_unexplored(room, cell, direction)
            for direction in DIRECTION_VECTORS
        )
        warp_route = self._route_to_learned_warp(room, cell)
        should_seek_warp = warp_route is not None and (
            self.steps_without_frontier >= WARP_SEEK_STEPS
            or (not current_frontier and frontier_route is None)
        )
        if should_seek_warp:
            direction, warp = warp_route
            return (
                direction,
                1,
                f"follow learned warp to {warp[4]} via {direction} "
                f"from ({warp[1]},{warp[2]})",
            )
        if current_frontier:
            direction = self._least_visited_direction(room, cell, self.direction)
            return direction, MOVEMENT_COMMIT_STEPS, f"explore new edge {direction}"
        if frontier_route is not None:
            # Replan after one sample while following the graph. A longer held
            # commitment can pass straight through the intermediate frontier.
            return frontier_route, 1, f"route to mapped frontier {frontier_route}"
        direction = self._least_visited_direction(room, cell, self.direction)
        return direction, 1, f"no reachable frontier; probe {direction}"

    def _break_oscillation(
        self,
        room: str,
        cell: tuple[int, int],
        proposed: str,
    ) -> tuple[str, bool]:
        """Escape after two complete decisions between the same two endpoints."""
        if len(self.decision_history) < 4:
            return proposed, False
        first, second, third, fourth = list(self.decision_history)[-4:]
        here = (room, *cell)
        if not (
            first[:3] == here
            and third[:3] == here
            and second[:3] == fourth[:3]
            and second[:3] != here
            and first[3] == proposed
            and third[3] == proposed
            and second[3] == OPPOSITE[proposed]
            and fourth[3] == OPPOSITE[proposed]
        ):
            return proposed, False

        alternatives = [
            direction
            for direction in DIRECTION_VECTORS
            if direction not in {proposed, OPPOSITE[proposed]}
            and not self._blocked_near(room, cell, direction)
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        if not alternatives:
            return proposed, False

        def score(direction: str) -> tuple[int, int, int, str]:
            neighbor = self._known_open_neighbor(room, cell, direction)
            dx, dy = DIRECTION_VECTORS[direction]
            target = neighbor or (cell[0] + dx, cell[1] + dy)
            knowledge = (
                0
                if self._direction_is_unexplored(room, cell, direction)
                else 1 if neighbor is not None else 2
            )
            return (
                knowledge,
                self.visits[(room, *target)],
                self._recent_cell_cost(room, target),
                direction,
            )

        self.oscillation_breaks += 1
        return min(alternatives, key=score), True

    def _least_visited_direction(
        self,
        room: str,
        cell: tuple[int, int],
        previous: str,
        avoid: set[str] | None = None,
    ) -> str:
        avoid = avoid or set()
        right = {"up": "right", "right": "down", "down": "left", "left": "up"}
        left = {value: key for key, value in right.items()}
        preference = [previous, right[previous], left[previous], OPPOSITE[previous]]
        available = [
            direction
            for direction in preference
            if direction not in avoid
            and not self._is_entry_warp_direction(room, cell, direction)
        ]
        safe = [
            direction
            for direction in available
            if not self._blocked_near(room, cell, direction)
        ]
        for direction in safe:
            if self._direction_is_unexplored(room, cell, direction):
                return direction

        route_direction = self._route_to_nearest_frontier(
            room, cell, allowed_first=set(safe)
        )
        if route_direction is not None:
            return route_direction

        # If old map history claims every exit is blocked, keep recovery
        # bounded by trying a different direction instead of repeating the one
        # that just failed forever. Fresh movement evidence can then repair the map.
        known_paths = [
            direction
            for direction in safe
            if self._known_open_neighbor(room, cell, direction) is not None
        ]
        candidates = known_paths or safe or available
        if not candidates:
            candidates = [
                direction
                for direction in preference
                if not self._is_entry_warp_direction(room, cell, direction)
            ] or preference
        scored: list[tuple[int, str]] = []
        for rank, direction in enumerate(candidates):
            dx, dy = DIRECTION_VECTORS[direction]
            neighbor = self._known_open_neighbor(room, cell, direction) or (
                cell[0] + dx,
                cell[1] + dy,
            )
            obstacle_cost = (
                self.blocked[(room, *cell, direction)] * 10_000
                + (10_000 if self._blocked_near(room, cell, direction) else 0)
            )
            tried_cost = 200 if (room, *cell, direction) in self.tried else 0
            visit_cost = self.visits[(room, *neighbor)] * 10
            recent_cost = self._recent_cell_cost(room, neighbor)
            reverse_cost = 120 if direction == OPPOSITE[previous] else 0
            scored.append(
                (
                    obstacle_cost
                    + tried_cost
                    + visit_cost
                    + recent_cost
                    + reverse_cost
                    + rank,
                    direction,
                )
            )
        return min(scored)[1]

    def _direction_is_unexplored(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        return (
            not self._region_direction_tried(room, cell, direction)
            and not self._blocked_near(room, cell, direction)
            and self._known_open_neighbor(room, cell, direction) is None
            and not self._known_warp_direction(room, cell, direction)
        )

    def _region_direction_tried(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        region_x, region_y = self._region(cell)
        key = (room, region_x, region_y, direction)
        if key in self.tried_regions:
            return True
        for tried_room, tried_x, tried_y, tried_direction in self.tried:
            if (
                tried_room == room
                and tried_direction == direction
                and self._region((tried_x, tried_y)) == (region_x, region_y)
            ):
                self.tried_regions.add(key)
                return True
        return False

    def _known_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        return any(
            source_room == room
            and (source_x, source_y) == cell
            and action == direction
            for (
                source_room,
                source_x,
                source_y,
                action,
                _target_room,
                _target_x,
                _target_y,
            ) in self.warps
        )

    def _is_entry_warp_direction(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> bool:
        entry_room = self.room_entry_from.get(room)
        if entry_room is None:
            return False
        return any(
            source_room == room
            and (source_x, source_y) == cell
            and action == direction
            and target_room == entry_room
            for (
                source_room,
                source_x,
                source_y,
                action,
                target_room,
                _target_x,
                _target_y,
            ) in self.warps
        )

    def _known_open_neighbor(
        self,
        room: str,
        cell: tuple[int, int],
        direction: str,
    ) -> tuple[int, int] | None:
        for (
            edge_room,
            source_x,
            source_y,
            edge_direction,
            target_x,
            target_y,
        ) in self.open_edges:
            if (
                edge_room == room
                and (source_x, source_y) == cell
                and edge_direction == direction
                and self._edge_matches_direction(cell, direction, (target_x, target_y))
            ):
                return target_x, target_y
        return None

    def _recent_cell_cost(self, room: str, cell: tuple[int, int]) -> int:
        cost = 0
        for age, recent in enumerate(reversed(self.recent_cells)):
            if recent == (room, *cell):
                cost += max(20, 360 - age * 20)
        return cost

    def _adjacency(
        self, room: str
    ) -> dict[tuple[int, int], list[tuple[str, tuple[int, int]]]]:
        adjacency: dict[tuple[int, int], list[tuple[str, tuple[int, int]]]] = {}
        for (
            edge_room,
            source_x,
            source_y,
            direction,
            target_x,
            target_y,
        ) in self.open_edges:
            source = (source_x, source_y)
            target = (target_x, target_y)
            if (
                edge_room == room
                and self._edge_matches_direction(source, direction, target)
                and not self._blocked_near(room, source, direction)
            ):
                adjacency.setdefault(source, []).append((direction, target))
        return adjacency

    def _route_to_nearest_frontier(
        self,
        room: str,
        start: tuple[int, int],
        allowed_first: set[str] | None = None,
    ) -> str | None:
        adjacency = self._adjacency(room)

        queue = deque([(start, None)])
        visited = {start}
        while queue:
            cell, first_direction = queue.popleft()
            if cell != start and any(
                self._direction_is_unexplored(room, cell, direction)
                for direction in DIRECTION_VECTORS
            ):
                return first_direction
            for direction, target in sorted(adjacency.get(cell, [])):
                if cell == start and allowed_first is not None and direction not in allowed_first:
                    continue
                if target in visited:
                    continue
                visited.add(target)
                queue.append((target, first_direction or direction))
        return None

    def _route_to_learned_warp(
        self,
        room: str,
        start: tuple[int, int],
    ) -> tuple[str, Warp] | None:
        """Route only to warp endpoints previously observed during a room change."""
        adjacency = self._adjacency(room)
        candidates: list[tuple[tuple[int, int, int, int], str, Warp]] = []
        for warp, crossings in self.warps.items():
            (
                source_room,
                source_x,
                source_y,
                action,
                target_room,
                _target_x,
                _target_y,
            ) = warp
            if source_room != room or action not in DIRECTION_VECTORS:
                continue
            if self.room_entry_from.get(room) == target_room:
                # This is the doorway used to enter the current room. Treat it
                # as a known backtrack, not as a progression goal.
                continue
            source = (source_x, source_y)
            if source == start:
                if self._blocked_near(room, start, action):
                    continue
                first_direction = action
                distance = 0
            else:
                route = self._route_to_target(adjacency, start, source)
                if route is None:
                    continue
                first_direction, distance = route
            target_cells = sum(
                seen_room == target_room
                for seen_room, _seen_x, _seen_y in self.seen_cells
            )
            backtrack = int(self.room_entry_from.get(room) == target_room)
            score = (backtrack, target_cells, crossings, distance)
            candidates.append((score, first_direction, warp))
        if not candidates:
            return None
        _score, direction, warp = min(candidates, key=lambda candidate: candidate[0])
        return direction, warp

    @staticmethod
    def _route_to_target(
        adjacency: dict[tuple[int, int], list[tuple[str, tuple[int, int]]]],
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> tuple[str, int] | None:
        queue = deque([(start, None, 0)])
        visited = {start}
        while queue:
            cell, first_direction, distance = queue.popleft()
            if cell == goal and first_direction is not None:
                return first_direction, distance
            for direction, target in sorted(adjacency.get(cell, [])):
                if target in visited:
                    continue
                visited.add(target)
                queue.append((target, first_direction or direction, distance + 1))
        return None

    @staticmethod
    def _edge_matches_direction(
        source: tuple[int, int], direction: str, target: tuple[int, int]
    ) -> bool:
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        vector_x, vector_y = DIRECTION_VECTORS[direction]
        forward = delta_x * vector_x + delta_y * vector_y
        lateral = abs(delta_x * vector_y - delta_y * vector_x)
        return forward == 1 and lateral == 0

    def _select(
        self, name: str, reason: str, telemetry: TelemetrySample | None
    ) -> Action:
        self.reason = reason
        if name in DIRECTION_VECTORS and telemetry and telemetry.mode == "overworld":
            self.last_movement = name
            self.last_position = (telemetry.x, telemetry.y)
            self.last_room = self._room_key(telemetry)
            self.last_cell = self._cell(telemetry)
            self.last_movement_sample_at = telemetry.received_at
            self.tried.add((self.last_room, *self.last_cell, name))
            region_x, region_y = self._region(self.last_cell)
            self.tried_regions.add((self.last_room, region_x, region_y, name))
        else:
            self.last_movement = None
        return ACTIONS[name]

    def summary(self) -> dict:
        return {
            "rooms_seen": sorted({room for room, _x, _y in self.seen_cells}),
            "mapped_cells": len(self.seen_cells),
            "explored_regions": len(self.seen_regions),
            "blocked_edges": sum(self.blocked.values()),
            "blocked_zones": len(self.blocked_zones),
            "learned_open_edges": len(self.open_edges),
            "completed_interactions": len(self.interacted_zones),
            "remembered_interaction_targets": len(self.interacted_targets),
            "identified_interactables": len(self.interactables),
            "oscillation_breaks": self.oscillation_breaks,
            "discovered_warps": [
                {
                    "from": source,
                    "at": [source_x, source_y],
                    "action": action,
                    "to": target,
                    "entry": [target_x, target_y],
                    "count": count,
                }
                for (
                    source,
                    source_x,
                    source_y,
                    action,
                    target,
                    target_x,
                    target_y,
                ), count in sorted(self.warps.items())
            ],
            "transitions": [
                {"from": source, "to": target, "count": count}
                for (source, target), count in sorted(self.transitions.items())
            ],
        }

    def save_memory(self) -> None:
        self.world.save()

    def drain_map_updates(self) -> list[dict[str, object]]:
        updates = self.map_updates
        self.map_updates = []
        return updates
