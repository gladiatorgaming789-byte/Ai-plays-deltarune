from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from math import isfinite, log, sqrt
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from uuid import uuid4

from .reinforcement import ReinforcementMemory, RewardSettings
from .strategy import StrategyGenome, population_genomes


TRAINING_SCHEMA_VERSION = 1
SEGMENT_DECISION_LIMIT = 64
ROUND_ROBIN_SEGMENTS = 2
UCB1_COEFFICIENT = 0.75
MIN_SEGMENTS = 2
MIN_ACTIVE_DECISIONS = 64


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _json_line(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")


@dataclass
class CandidateState:
    candidate_id: str
    label: str
    genome: StrategyGenome
    reinforcement: ReinforcementMemory
    directory: Path
    total_points: float = 0.0
    active_decisions: int = 0
    segments_completed: int = 0
    story_progress: int = 0
    safety_penalties: int = 0
    loop_escapes: int = 0
    room_bounces: int = 0
    room_transitions: int = 0
    budget_overruns: int = 0
    scorer_failures: int = 0
    breakdown: dict[str, float] = field(default_factory=dict)
    disqualification_reasons: list[str] = field(default_factory=list)
    last_shadow_ranking: list[dict[str, object]] = field(default_factory=list)

    @property
    def normalized_score(self) -> float:
        return 100.0 * self.total_points / (self.active_decisions + 64)

    @property
    def bounce_rate(self) -> float:
        return self.room_bounces / max(1, self.room_transitions)

    @property
    def minimum_exposure_met(self) -> bool:
        return (
            self.segments_completed >= MIN_SEGMENTS
            and self.active_decisions >= MIN_ACTIVE_DECISIONS
        )

    @property
    def disqualified(self) -> bool:
        return bool(self.disqualification_reasons)

    def add_points(self, event: str, points: float) -> None:
        self.total_points += float(points)
        self.breakdown[event] = self.breakdown.get(event, 0.0) + float(points)
        if points < 0 and event != "active_decision":
            self.safety_penalties += 1

    def disqualify(self, reason: str) -> None:
        if reason not in self.disqualification_reasons:
            self.disqualification_reasons.append(reason)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.candidate_id,
            "label": self.label,
            "genome": self.genome.to_dict(),
            "active_decisions": self.active_decisions,
            "segments_completed": self.segments_completed,
            "total_points": round(self.total_points, 4),
            "normalized_score": round(self.normalized_score, 4),
            "story_progress": self.story_progress,
            "safety_penalties": self.safety_penalties,
            "loop_escapes": self.loop_escapes,
            "room_bounces": self.room_bounces,
            "room_transitions": self.room_transitions,
            "bounce_rate": round(self.bounce_rate, 4),
            "budget_overruns": self.budget_overruns,
            "scorer_failures": self.scorer_failures,
            "breakdown": {
                key: round(value, 4) for key, value in sorted(self.breakdown.items())
            },
            "minimum_exposure_met": self.minimum_exposure_met,
            "disqualified": self.disqualified,
            "disqualification_reasons": list(self.disqualification_reasons),
            "shadow_ranking": list(self.last_shadow_ranking),
        }


@dataclass
class SegmentState:
    segment_id: str
    index: int
    candidate_id: str
    start_step: int
    start_reason: str
    active_decisions: int = 0
    open_edge_points: float = 0.0
    pending_end_reason: str | None = None

    def as_dict(self, current_step: int) -> dict[str, object]:
        return {
            "id": self.segment_id,
            "index": self.index,
            "candidate_id": self.candidate_id,
            "start_step": self.start_step,
            "start_reason": self.start_reason,
            "age_steps": max(0, int(current_step) - self.start_step),
            "active_decisions": self.active_decisions,
            "open_edge_points": round(self.open_edge_points, 4),
            "pending_end_reason": self.pending_end_reason,
        }


class PopulationCoordinator:
    """Own causal candidate segments while sharing one authoritative policy.

    Candidate rankings are pure calculations over already-collected legal
    option payloads. Only ``active`` is ever attached to the live explorer's
    reinforcement field, so shadow heads cannot alter goals, budgets, maps,
    traces, or keyboard input.
    """

    def __init__(
        self,
        *,
        session_id: str | None,
        baseline_genome: StrategyGenome,
        baseline_reinforcement: ReinforcementMemory,
        candidates_directory: Path,
        events_path: Path,
        reward_settings: RewardSettings,
        known_rooms: Iterable[str] = (),
    ) -> None:
        self.session_id = session_id or uuid4().hex
        self.events_path = events_path
        self.reward_settings = reward_settings
        self.baseline_reinforcement = baseline_reinforcement
        self.candidates: list[CandidateState] = []
        for candidate_id, label, genome in population_genomes(baseline_genome):
            directory = candidates_directory / candidate_id
            directory.mkdir(parents=True, exist_ok=True)
            reinforcement = ReinforcementMemory.load(directory / "reinforcement.json")
            genome.save(directory / "strategy.json")
            self.candidates.append(
                CandidateState(candidate_id, label, genome, reinforcement, directory)
            )
        self._by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        self.active_index = 0
        self.segment_index = 0
        self.current_step = 0
        self.segment = self._new_segment(0, "initial round-robin segment")
        self.explorer = None
        self.known_rooms = {str(room) for room in known_rooms if str(room)}
        self.room_history: list[str] = []
        self.telemetry_decisions = 0
        self.total_decisions = 0
        self._loop_active = False
        self._reset_active = False
        self._ranking_buffer: dict[str, dict[str, float]] = {}
        self._last_options: list[dict[str, object]] = []
        self._handoff_ready_reason: str | None = None
        self._events("training_started", candidate=self.active.candidate_id)

    @property
    def active(self) -> CandidateState:
        return self.candidates[self.active_index]

    def _events(self, event: str, **payload: object) -> None:
        _json_line(
            self.events_path,
            {
                "schema_version": TRAINING_SCHEMA_VERSION,
                "recorded_at": _utc_now(),
                "session_id": self.session_id,
                "event": event,
                "step": self.current_step,
                "segment_id": self.segment.segment_id if hasattr(self, "segment") else None,
                **payload,
            },
        )

    def _new_segment(self, step: int, reason: str) -> SegmentState:
        self.segment_index += 1
        return SegmentState(
            segment_id=f"segment-{self.segment_index:04d}",
            index=self.segment_index,
            candidate_id=self.active.candidate_id,
            start_step=int(step),
            start_reason=reason,
        )

    def bind_explorer(self, explorer: object) -> None:
        self.explorer = explorer
        setattr(explorer, "reinforcement", self.active.reinforcement)
        self.active.reinforcement.clear_trace()

    @staticmethod
    def _option_key(option: object) -> str:
        strategy_key = str(
            getattr(option, "_population_reinforcement_key", "") or ""
        )
        if strategy_key:
            return strategy_key
        budget_key = str(getattr(option, "budget_key", "") or "")
        if budget_key:
            return budget_key
        return f"option:{getattr(option, 'kind', 'unknown')}:{getattr(option, 'option_id', '')}"

    def _learning_delta(self, candidate: CandidateState, key: str) -> float:
        return candidate.reinforcement.score(key, self.reward_settings) - self.baseline_reinforcement.score(
            key, self.reward_settings
        )

    def score_option(
        self,
        option: object,
        *,
        budget_fraction: float,
        candidate: CandidateState | None = None,
    ) -> float:
        owner = candidate or self.active
        key = self._option_key(option)
        try:
            return owner.genome.score(
                base_score=_number(getattr(option, "base_score", 0.0)),
                confidence=_number(getattr(option, "confidence", 0.0)),
                information_value=_number(getattr(option, "information_value", 0.0)),
                novelty=_number(getattr(option, "novelty", 0.0)),
                distance=_number(getattr(option, "distance", 0.0)),
                loop_risk=_number(getattr(option, "loop_risk", 0.0)),
                failure_cost=_number(getattr(option, "failure_cost", 0.0)),
                budget_fraction=budget_fraction,
                reinforcement_delta=self._learning_delta(owner, key),
            )
        except Exception as exc:
            owner.scorer_failures += 1
            owner.disqualify(f"scorer failure: {type(exc).__name__}: {exc}")
            self._events(
                "candidate_disqualified",
                candidate=owner.candidate_id,
                reason=owner.disqualification_reasons[-1],
            )
            return float("-inf")

    def record_legal_options(self, options: Sequence[object]) -> None:
        rankings: dict[str, list[dict[str, object]]] = {}
        for candidate in self.candidates:
            rows: list[dict[str, object]] = []
            for option in options:
                limit = max(0, _integer(getattr(option, "budget_limit", 0)))
                spent = max(0, _integer(getattr(option, "budget_spent", 0)))
                if limit and spent >= limit:
                    continue
                score = self.score_option(
                    option,
                    budget_fraction=(spent / max(1, limit)) if limit else 0.0,
                    candidate=candidate,
                )
                rows.append(
                    {
                        "id": str(getattr(option, "option_id", "")),
                        "kind": str(getattr(option, "kind", "unknown")),
                        "score": score if isfinite(score) else None,
                        "distance": _integer(getattr(option, "distance", 0)),
                    }
                )
            rows.sort(
                key=lambda row: (
                    -_number(row["score"], float("-inf")),
                    _integer(row["distance"]),
                    str(row["id"]),
                )
            )
            candidate.last_shadow_ranking = rows[:8]
            rankings[candidate.candidate_id] = candidate.last_shadow_ranking
        self._last_options = [dict(row) for row in rankings.get(self.active.candidate_id, [])]

    def record_shared_action(
        self,
        action_name: str,
        reason: str,
        *,
        force: bool = False,
    ) -> None:
        shared = [{"id": action_name, "kind": "shared_controller", "score": 0.0, "reason": reason}]
        for candidate in self.candidates:
            candidate.last_shadow_ranking = list(shared)

    def _award(self, event: str, points: float, **details: object) -> None:
        self.active.add_points(event, points)
        self._events(
            "score",
            candidate=self.active.candidate_id,
            score_event=event,
            points=points,
            total_points=round(self.active.total_points, 4),
            **details,
        )

    def _observe_room(self, room: str | None) -> None:
        if not room:
            return
        room = str(room)
        if self.room_history and room != self.room_history[-1]:
            self.active.room_transitions += 1
            self.room_history.append(room)
            if len(self.room_history) >= 3 and self.room_history[-1] == self.room_history[-3]:
                self.active.room_bounces += 1
                self._award("room_bounce", -15.0, rooms=self.room_history[-3:])
        elif not self.room_history:
            self.room_history.append(room)
        if len(self.room_history) > 32:
            del self.room_history[:-32]
        if room not in self.known_rooms:
            self.known_rooms.add(room)
            self._award("new_room", 15.0, room=room)

    def _observe_update(self, update: Mapping[str, object]) -> None:
        kind = str(update.get("type") or "")
        if kind == "story_progress":
            event = str(update.get("event") or "")
            if event != "discovered a new room":
                self.active.story_progress += 1
                self._award("story_progress", 50.0, observed_event=event)
            self.segment.pending_end_reason = f"observed story progress: {event or 'unknown'}"
        elif kind == "choice_outcome":
            successful = bool(update.get("successful"))
            self._award("choice_success" if successful else "choice_failure", 10.0 if successful else -8.0)
        elif kind == "interactable" and _integer(update.get("confirmations")) == 1:
            self._award("first_interactable", 3.0, room=update.get("room"), cell=update.get("cell"))
        elif kind == "interaction_outcome" and str(update.get("last_outcome") or "") in {
            "ordinary_dialogue",
            "no_response",
        }:
            self._award("ordinary_or_no_response", -5.0, outcome=update.get("last_outcome"))
        elif kind == "character_probe" and str(update.get("result") or "").casefold() == "no response":
            self._award("ordinary_or_no_response", -5.0, outcome="no response")
        elif kind == "open_edge" and self.segment.open_edge_points < 10.0:
            points = min(0.25, 10.0 - self.segment.open_edge_points)
            self.segment.open_edge_points += points
            self._award("open_edge", points)
        elif kind == "navigation_goal_contract_end":
            outcome = str(update.get("outcome") or "")
            if outcome == "failed":
                self._award("failed_goal_contract", -4.0, reason=update.get("reason"))
            self.segment.pending_end_reason = f"goal contract {outcome or 'ended'}"
        elif kind == "autonomy_budget_exhausted":
            spent = _integer(update.get("spent"))
            limit = _integer(update.get("limit"))
            if limit >= 0 and spent > limit:
                self.active.budget_overruns += 1
                self.active.disqualify("uncertainty budget overrun")

    def observe_step(
        self,
        *,
        step: int,
        state: str,
        telemetry_present: bool,
        room: str | None,
        player_controlled: bool | None,
        reason: str,
        map_updates: Sequence[Mapping[str, object]],
        safe_overworld: bool,
    ) -> None:
        self.current_step = int(step)
        self.total_decisions += 1
        if telemetry_present:
            self.telemetry_decisions += 1
        active_overworld = state == "overworld" and player_controlled is not False
        if active_overworld:
            self.active.active_decisions += 1
            self.segment.active_decisions += 1
            self._award("active_decision", -0.05)
        self._observe_room(room)
        for update in map_updates:
            if isinstance(update, Mapping):
                self._observe_update(update)

        lowered = reason.casefold()
        loop_now = "loop" in lowered and any(
            marker in lowered
            for marker in (
                "escape",
                "recovery",
                "break",
                "forced",
                "oscillation",
                "commit",
            )
        )
        if loop_now and not self._loop_active:
            self.active.loop_escapes += 1
            self._award("loop_escape", -10.0, reason=reason)
        self._loop_active = loop_now
        reset_now = "broad reset" in lowered
        if reset_now and not self._reset_active:
            self._award("broad_reset", -2.0, reason=reason)
        self._reset_active = reset_now

        if self.segment.active_decisions >= SEGMENT_DECISION_LIMIT:
            self.segment.pending_end_reason = self.segment.pending_end_reason or "64 active overworld decisions"
        self._apply_candidate_safety_gates(self.active)
        if self.active.disqualified:
            self.segment.pending_end_reason = self.segment.pending_end_reason or "candidate disqualified by safety gate"
        if self.segment.pending_end_reason and safe_overworld:
            self._handoff_ready_reason = self.segment.pending_end_reason

    def commit_handoff(self) -> None:
        """Switch only after the owning candidate's action was actually sent."""
        if self._handoff_ready_reason is None:
            return
        reason = self._handoff_ready_reason
        self._handoff_ready_reason = None
        self._handoff(reason)

    def _apply_candidate_safety_gates(self, candidate: CandidateState) -> None:
        if candidate.scorer_failures:
            candidate.disqualify("scorer failure")
        if candidate.budget_overruns:
            candidate.disqualify("uncertainty budget overrun")
        if candidate.loop_escapes >= 8:
            candidate.disqualify("eight navigation loop escapes")
        if candidate.room_bounces >= 4 and candidate.bounce_rate >= (2.0 / 3.0):
            candidate.disqualify("room bounce rate reached two-thirds after four bounces")

    def _select_next_index(self) -> int:
        completed = sum(candidate.segments_completed for candidate in self.candidates)
        round_robin_total = len(self.candidates) * ROUND_ROBIN_SEGMENTS
        if completed < round_robin_total:
            first = completed % len(self.candidates)
            for offset in range(len(self.candidates)):
                index = (first + offset) % len(self.candidates)
                if not self.candidates[index].disqualified:
                    return index
            return first
        log_total = log(max(2, completed))
        ranked = []
        for index, candidate in enumerate(self.candidates):
            if candidate.disqualified:
                continue
            exploitation = candidate.total_points / max(1, candidate.segments_completed)
            exploration = UCB1_COEFFICIENT * sqrt(log_total / max(1, candidate.segments_completed))
            ranked.append((-(exploitation + exploration), candidate.candidate_id, index))
        return min(ranked)[2] if ranked else self.active_index

    def _handoff(self, reason: str) -> None:
        previous = self.active
        previous.segments_completed += 1
        previous.reinforcement.clear_trace()
        self._events(
            "segment_completed",
            candidate=previous.candidate_id,
            reason=reason,
            segment=self.segment.as_dict(self.current_step),
            candidate_score=previous.as_dict(),
        )
        self.active_index = self._select_next_index()
        self.active.reinforcement.clear_trace()
        if self.explorer is not None:
            setattr(self.explorer, "reinforcement", self.active.reinforcement)
        self.segment = self._new_segment(self.current_step + 1, reason)
        for candidate in self.candidates:
            candidate.last_shadow_ranking = []
        self._events(
            "candidate_handoff",
            previous=previous.candidate_id,
            candidate=self.active.candidate_id,
            reason=reason,
        )

    def finish_active_segment(self, reason: str = "run ended") -> None:
        if self.segment.active_decisions <= 0 and self.segment.pending_end_reason is None:
            return
        self.segment.pending_end_reason = self.segment.pending_end_reason or reason
        previous = self.active
        previous.segments_completed += 1
        previous.reinforcement.clear_trace()
        self._events(
            "segment_completed",
            candidate=previous.candidate_id,
            reason=self.segment.pending_end_reason,
            segment=self.segment.as_dict(self.current_step),
            candidate_score=previous.as_dict(),
        )

    def telemetry_coverage(self) -> float:
        return self.telemetry_decisions / max(1, self.total_decisions)

    def ranked_candidates(self) -> list[CandidateState]:
        return sorted(
            self.candidates,
            key=lambda candidate: (
                candidate.disqualified,
                not candidate.minimum_exposure_met,
                -candidate.normalized_score,
                -candidate.story_progress,
                candidate.safety_penalties,
                candidate.candidate_id,
            ),
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": TRAINING_SCHEMA_VERSION,
            "session_id": self.session_id,
            "active_candidate": self.active.candidate_id,
            "segment": self.segment.as_dict(self.current_step),
            "handoff_pending": self._handoff_ready_reason is not None,
            "shadow_rankings": {
                candidate.candidate_id: list(candidate.last_shadow_ranking)
                for candidate in self.candidates
            },
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "telemetry_coverage": round(self.telemetry_coverage(), 4),
        }

    def flush_candidates(self) -> None:
        for candidate in self.candidates:
            candidate.genome.save(candidate.directory / "strategy.json")
            candidate.reinforcement.flush(force=True)


__all__ = [
    "CandidateState",
    "MIN_ACTIVE_DECISIONS",
    "MIN_SEGMENTS",
    "PopulationCoordinator",
    "ROUND_ROBIN_SEGMENTS",
    "SEGMENT_DECISION_LIMIT",
    "TRAINING_SCHEMA_VERSION",
    "UCB1_COEFFICIENT",
]
