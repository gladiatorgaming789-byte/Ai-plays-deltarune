from __future__ import annotations

from pathlib import Path

from .run3_explorer import Run3Explorer


ROOM_EXIT_PRIORITY_STEPS = 240
ROOM_EXIT_PRIORITY_STORY_STALL = 180
ROOM_EXIT_PRIORITY_MIN_CELLS = 24
MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT = 2
EXIT_PRIORITY_COMMIT_STEPS = 2


class Run4Explorer(Run3Explorer):
    """Explorer fixes learned from the fourth recorded playthrough.

    The run eventually reached Toriel's house, but spent 1,155 actions in
    Kris's bedroom and another 672 actions testing kitchen and living-room
    scenery. Once a room is well sampled, genuine room-edge exit evidence must
    outrank weak character guesses and exhaustive furniture inspection.
    """

    def __init__(self, seed: int = 0, memory_path: Path | None = None):
        super().__init__(seed, memory_path)
        self.exit_priority_activations = 0
        self.prioritized_exit_steps = 0
        self.retired_weak_character_leads = 0
        self.max_room_navigation_age = 0

    def _room_navigation_age(self, room: str) -> int:
        entered_at = self.room_entered_at.get(room)
        if entered_at is None:
            return 0
        return max(0, self.navigation_tick - entered_at)

    def _room_seen_cell_count(self, room: str) -> int:
        return sum(
            seen_room == room
            for seen_room, _x, _y in self.seen_cells
        )

    def _room_flavor_count(self, room: str) -> int:
        return sum(
            key[0] == room
            and str(record.get("usefulness") or "") == "flavor"
            for key, record in self.interactables.items()
        )

    def _has_exit_lead(self, room: str) -> bool:
        if self._possible_exit_probes(room):
            return True
        return any(
            key[0] == room
            and record.get("hypothesis") == "possible_exit"
            and int(record.get("inspections", 0)) < 3
            for key, record in self.screen_regions.items()
        )

    def _exit_priority_active(self, room: str) -> bool:
        if not self._has_exit_lead(room):
            return False
        room_age = self._room_navigation_age(room)
        seen_cells = self._room_seen_cell_count(room)
        flavor_count = self._room_flavor_count(room)
        return (
            flavor_count >= MAX_FLAVOR_INTERACTIONS_BEFORE_EXIT
            or self.story_stall_steps >= ROOM_EXIT_PRIORITY_STORY_STALL
            or (
                room_age >= ROOM_EXIT_PRIORITY_STEPS
                and seen_cells >= ROOM_EXIT_PRIORITY_MIN_CELLS
            )
        )

    def _region_has_useful_interaction(
        self,
        room: str,
        region_x: int,
        region_y: int,
    ) -> bool:
        for key, record in self.interactables.items():
            if key[0] != room:
                continue
            if self._region((key[1], key[2])) != (region_x, region_y):
                continue
            if str(record.get("usefulness") or "") in {
                "choice_pending",
                "progress",
            }:
                return True
        return False

    def _retire_weak_character_hypotheses(self, room: str) -> None:
        """Stop revisiting scenery after stronger exit evidence exists."""
        for key, record in self.screen_regions.items():
            if key[0] != room or record.get("hypothesis") != "possible_character":
                continue
            if record.get("choice_retry") or self._region_has_useful_interaction(
                room,
                key[1],
                key[2],
            ):
                continue
            inspections = int(record.get("inspections", 0))
            approaches = int(record.get("entity_approach_directions", 0))
            obstruction_targets = int(record.get("obstruction_target_cells", 99))
            weak = (
                inspections >= 1
                or approaches < 2
                or obstruction_targets > 4
            )
            if not weak:
                continue
            record["hypothesis"] = None
            record["inspections"] = max(3, inspections)
            record["retired_reason"] = "exit evidence outranked weak scenery lead"
            self.retired_weak_character_leads += 1
            self.map_updates.append(
                {
                    "type": "screen_region",
                    "room": room,
                    "region": [key[1], key[2]],
                    "views": int(record.get("views", 1)),
                    "interest": round(float(record.get("interest", 0.0)), 3),
                    "hypothesis": None,
                    "inspections": int(record["inspections"]),
                    "retired_reason": record["retired_reason"],
                }
            )

    def _prioritized_exit_plan(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str] | None:
        visual_exit = self._direction_to_visual_hypothesis(
            room,
            cell,
            story_focus=True,
            allowed_hypotheses={"possible_exit"},
        )
        if visual_exit is not None:
            direction, _hypothesis, target_region = visual_exit
            return (
                direction,
                1,
                "room completion: prioritize visible exit passage "
                f"via {direction} toward region {target_region}",
            )

        exit_route = self._route_to_possible_exit(room, cell)
        if exit_route is None:
            return None
        direction, probe = exit_route
        _probe_room, probe_x, probe_y, probe_direction = probe
        if cell == (probe_x, probe_y):
            self.exit_probes[probe] += 1
            return (
                probe_direction,
                EXIT_PRIORITY_COMMIT_STEPS,
                "room completion: commit to room-edge exit "
                f"{probe_direction} at ({probe_x},{probe_y})",
            )
        return (
            direction,
            1,
            "room completion: route to room-edge exit "
            f"{probe_direction} at ({probe_x},{probe_y}) via {direction}",
        )

    def _plan_exploration(
        self,
        room: str,
        cell: tuple[int, int],
    ) -> tuple[str, int, str]:
        room_age = self._room_navigation_age(room)
        self.max_room_navigation_age = max(
            self.max_room_navigation_age,
            room_age,
        )
        if self._exit_priority_active(room):
            self.exit_priority_activations += 1
            self._retire_weak_character_hypotheses(room)
            plan = self._prioritized_exit_plan(room, cell)
            if plan is not None:
                self.prioritized_exit_steps += 1
                return plan
        return super()._plan_exploration(room, cell)

    def summary(self) -> dict:
        summary = super().summary()
        summary["exit_priority_activations"] = self.exit_priority_activations
        summary["prioritized_exit_steps"] = self.prioritized_exit_steps
        summary["retired_weak_character_leads"] = self.retired_weak_character_leads
        summary["max_room_navigation_age"] = self.max_room_navigation_age
        return summary
