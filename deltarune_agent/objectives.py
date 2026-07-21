from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class ObjectiveKind(str, Enum):
    SURVIVE_BATTLE = "survive_battle"
    RESOLVE_CHOICE = "resolve_choice"
    ADVANCE_DIALOGUE = "advance_dialogue"
    INVESTIGATE_INTERACTION = "investigate_interaction"
    SEEK_EXIT = "seek_exit"
    EXPLORE_FRONTIER = "explore_frontier"
    RECOVER = "recover"
    WAIT = "wait"


@dataclass(frozen=True)
class Objective:
    kind: ObjectiveKind
    description: str
    score: float
    room: str | None = None
    target: tuple[int, int] | None = None
    evidence: tuple[str, ...] = ()

    @property
    def identity(
        self,
    ) -> tuple[ObjectiveKind, str | None, tuple[int, int] | None]:
        """Stable goal identity, excluding changing explanation text."""
        return self.kind, self.room, self.target


@dataclass
class ObjectiveManager:
    current: Objective | None = None
    history: list[Objective] = field(default_factory=list)

    def choose(self, candidates: Iterable[Objective]) -> Objective:
        options = list(candidates)
        if not options:
            selected = Objective(
                ObjectiveKind.WAIT,
                "wait for usable state",
                0.0,
            )
        else:
            selected = max(
                options,
                key=lambda item: (
                    item.score,
                    item.kind.value,
                ),
            )
        changed = (
            self.current is None
            or self.current.identity != selected.identity
        )
        self.current = selected
        if changed:
            self.history.append(selected)
            if len(self.history) > 100:
                self.history = self.history[-100:]
        return selected

    def objective_for_state(
        self,
        state: str,
        delegate_reason: str = "",
        room: str | None = None,
    ) -> Objective:
        reason = delegate_reason.casefold()
        candidates: list[Objective] = []
        if state == "battle":
            candidates.append(
                Objective(
                    ObjectiveKind.SURVIVE_BATTLE,
                    "avoid projectiles",
                    100,
                    room,
                )
            )
        elif state == "menu":
            candidates.append(
                Objective(
                    ObjectiveKind.RESOLVE_CHOICE,
                    "test or reuse a menu response",
                    90,
                    room,
                )
            )
        elif state in {"dialogue", "cutscene"}:
            candidates.append(
                Objective(
                    ObjectiveKind.ADVANCE_DIALOGUE,
                    "advance the current sequence",
                    80,
                    room,
                )
            )
        elif state == "overworld":
            if "interaction" in reason or "character" in reason:
                candidates.append(
                    Objective(
                        ObjectiveKind.INVESTIGATE_INTERACTION,
                        delegate_reason,
                        70,
                        room,
                    )
                )
            if "exit" in reason or "warp" in reason:
                candidates.append(
                    Objective(
                        ObjectiveKind.SEEK_EXIT,
                        delegate_reason,
                        60,
                        room,
                    )
                )
            if (
                "loop" in reason
                or "recover" in reason
                or "stalled" in reason
            ):
                candidates.append(
                    Objective(
                        ObjectiveKind.RECOVER,
                        delegate_reason,
                        75,
                        room,
                    )
                )
            candidates.append(
                Objective(
                    ObjectiveKind.EXPLORE_FRONTIER,
                    delegate_reason or "explore unknown space",
                    50,
                    room,
                )
            )
        else:
            candidates.append(
                Objective(
                    ObjectiveKind.WAIT,
                    "wait through uncertain state",
                    10,
                    room,
                )
            )
        return self.choose(candidates)
