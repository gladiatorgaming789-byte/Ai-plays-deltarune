"""Precision-first visual exit detection.

The legacy screen analyzers are useful at noticing exit-like structure, but some
of them historically promoted one screenshot directly to ``possible_exit``.
That is too eager: a dark seam, floor-colored boundary, window, cabinet, or
other scenery can look exit-like without being traversable.

Exit Detection v2 separates those concepts:

* visual analyzers may propose an exit-like *candidate*;
* repeated independent views measure whether that candidate is stable;
* mapped movement / walkability provides independent traversability evidence;
* only sufficiently supported candidates become semantic ``possible_exit``
  records that the planner may route toward.

The layer uses only observations produced while the agent plays.  It contains
no room names, walkthrough routes, NPC identities, or progression answers.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Mapping

from . import guessing_v3 as v3
from .policy import StarterPolicy
from .run4_explorer import Run4Explorer
from .run13_screen_regions import FLOOR_EVIDENCE_PREFIX
from .run14_screen_regions import DOORWAY_FACADE_PREFIX
from .run15_screen_regions import SCROLLING_FLOOR_CONTACT_PREFIX
from .world_model import WorldModel


EXIT_DETECTION_VERSION = 2
MAX_EXIT_CANDIDATE_VIEWPOINTS = 12

DOORWAY_REQUIRED_VIEWS = 2
DOORWAY_MIN_CONSISTENCY = 0.55
DOORWAY_MIN_OPENING_SCORE = 0.72

EDGE_REQUIRED_VIEWS = 3
EDGE_MIN_CONSISTENCY = 0.70
EDGE_MIN_OPENING_SCORE = 0.68
EDGE_MIN_WIDTH = 0.10
EDGE_MAX_WIDTH = 0.50

EXIT_PERSISTED_FIELDS = (
    "exit_detection_version",
    "exit_candidate_source",
    "exit_candidate_state",
    "exit_candidate_visual_score",
    "exit_candidate_views",
    "exit_candidate_viewpoints",
    "exit_candidate_last_step",
    "exit_candidate_reasons",
    "exit_candidate_promotions",
)

_INSTALLED = False
_ORIGINAL_OBSERVE_SCREEN = None
_ORIGINAL_MAP_UPDATE = None
_ORIGINAL_VISUAL_EXIT_ACTIONABLE = None
_ORIGINAL_SUMMARY = None
_ORIGINAL_WORLD_SAVE = None
_ORIGINAL_WORLD_LOAD = None
_ORIGINAL_BELIEF_SCORES = None


def _safe_float(value: object, default: float = 0.0) -> float:
    return v3._safe_float(value, default)


def _safe_int(value: object, default: int = 0) -> int:
    return v3._safe_int(value, default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, value))


def exit_candidate_source(record: Mapping[str, object]) -> str | None:
    """Classify the *kind of observed evidence*, not whether it is a real exit."""

    if record.get("path_continuation"):
        return "mapped_path_continuation"
    summary = str(record.get("visual_summary") or record.get("feature_summary") or "")
    if summary.startswith(DOORWAY_FACADE_PREFIX):
        return "doorway_facade"
    if summary.startswith(SCROLLING_FLOOR_CONTACT_PREFIX):
        return "scrolling_floor_boundary"
    if summary.startswith(FLOOR_EVIDENCE_PREFIX):
        return "floor_boundary"
    if "dark opening connected to" in summary:
        return "dark_edge_opening"
    if _safe_float(record.get("edge_opening_score")) >= 0.30:
        return "generic_edge_opening"
    return None


def _candidate_viewpoints(record: Mapping[str, object]) -> list[list[int]]:
    value = record.get("exit_candidate_viewpoints")
    if not isinstance(value, list):
        return []
    result: list[list[int]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            continue
        try:
            parsed = [int(point[0]), int(point[1])]
        except (TypeError, ValueError):
            continue
        if parsed not in result:
            result.append(parsed)
    return result[-MAX_EXIT_CANDIDATE_VIEWPOINTS:]


def _latest_viewpoint(record: Mapping[str, object]) -> list[int] | None:
    value = record.get("evidence_viewpoints")
    if not isinstance(value, list) or not value:
        return None
    point = value[-1]
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    try:
        return [int(point[0]), int(point[1])]
    except (TypeError, ValueError):
        return None


def _candidate_visual_score(record: Mapping[str, object], source: str) -> float:
    opening = _clamp(_safe_float(record.get("edge_opening_score")))
    consistency = _clamp(_safe_float(record.get("multi_view_consistency"), 0.5))
    views = max(0, _safe_int(record.get("exit_candidate_views")))
    walkable = bool(record.get("walkable_evidence"))

    if source == "mapped_path_continuation":
        return 1.0
    if source == "doorway_facade":
        return _clamp(0.34 + opening * 0.24 + min(0.18, views * 0.06) + consistency * 0.18)
    if source in {"floor_boundary", "scrolling_floor_boundary"}:
        # Seeing floor touch a room boundary means "route-shaped visual clue".
        # It is deliberately weak until movement mapping supports traversability.
        return _clamp(0.12 + min(0.12, views * 0.03) + consistency * 0.08)
    if source == "dark_edge_opening":
        return _clamp(
            0.18
            + opening * 0.28
            + min(0.18, views * 0.05)
            + consistency * 0.16
            + (0.10 if walkable else 0.0)
        )
    return _clamp(
        0.10
        + opening * 0.20
        + min(0.12, views * 0.04)
        + consistency * 0.10
        + (0.08 if walkable else 0.0)
    )


def evaluate_exit_candidate(record: Mapping[str, object]) -> tuple[str, float, list[str]]:
    """Return candidate state, diagnostic score, and evidence-based reasons."""

    source = exit_candidate_source(record)
    if source is None:
        return "not_exit_candidate", 0.0, ["no independent exit-like evidence"]

    score = _candidate_visual_score(record, source)
    reasons: list[str] = [f"observed source: {source.replace('_', ' ')}"]

    if str(record.get("guess_state") or "") == "confirmed":
        reasons.append("a real room transition already confirmed this visual lead")
        return "confirmed", 1.0, reasons
    if record.get("path_continuation"):
        reasons.append("mapped movement continues toward the candidate")
        return "semantic_ready", max(score, 0.95), reasons

    failures = max(0, _safe_int(record.get("failed_approaches")))
    misses = max(0, _safe_int(record.get("guess_misses")))
    consistency = _clamp(_safe_float(record.get("multi_view_consistency"), 0.5))
    sample_count = max(0, _safe_int(record.get("multi_view_sample_count")))
    views = max(0, _safe_int(record.get("exit_candidate_views")))
    opening = _clamp(_safe_float(record.get("edge_opening_score")))
    width = _clamp(_safe_float(record.get("edge_width_ratio")))
    walkable = bool(record.get("walkable_evidence"))

    if failures:
        reasons.append(f"{failures} failed approach{'es' if failures != 1 else ''}")
    if misses:
        reasons.append(f"{misses} visual miss{'es' if misses != 1 else ''}")
    if sample_count >= 2:
        reasons.append(f"multi-view consistency {consistency:.0%}")

    # Strong contradiction blocks visual-only promotion.  A later mapped path or
    # real transition can still override this because both are stronger evidence.
    if failures >= 2 or misses >= 3 or (sample_count >= 2 and consistency < 0.35):
        reasons.append("current observations contradict a stable traversable exit")
        return "contradicted", score, reasons

    if source in {"floor_boundary", "scrolling_floor_boundary"}:
        reasons.append("visible boundary surface alone does not prove traversability")
        return "needs_path_evidence", score, reasons

    if source == "doorway_facade":
        if views < DOORWAY_REQUIRED_VIEWS:
            reasons.append(
                f"needs {DOORWAY_REQUIRED_VIEWS} independent doorway viewpoints; has {views}"
            )
            return "visual_candidate", score, reasons
        if sample_count < 2 or consistency < DOORWAY_MIN_CONSISTENCY:
            reasons.append("doorway shape has not stayed stable across viewpoints")
            return "visual_candidate", score, reasons
        if opening < DOORWAY_MIN_OPENING_SCORE:
            reasons.append("doorway structure score is below the promotion threshold")
            return "visual_candidate", score, reasons
        reasons.append("doorway structure repeated stably across independent viewpoints")
        return "semantic_ready", score, reasons

    if source == "dark_edge_opening":
        if views < EDGE_REQUIRED_VIEWS:
            reasons.append(
                f"needs {EDGE_REQUIRED_VIEWS} independent edge-opening viewpoints; has {views}"
            )
            return "visual_candidate", score, reasons
        if sample_count < 2 or consistency < EDGE_MIN_CONSISTENCY:
            reasons.append("edge opening has not stayed stable enough across viewpoints")
            return "visual_candidate", score, reasons
        if opening < EDGE_MIN_OPENING_SCORE:
            reasons.append("edge-opening shape score is below the promotion threshold")
            return "visual_candidate", score, reasons
        if width <= 0 or not EDGE_MIN_WIDTH <= width <= EDGE_MAX_WIDTH:
            reasons.append("edge-opening width is too narrow or broad to promote visually")
            return "visual_candidate", score, reasons
        if not walkable:
            reasons.append("no learned walkable approach reaches this region yet")
            return "needs_path_evidence", score, reasons
        reasons.append("stable localized opening plus learned walkable approach")
        return "semantic_ready", score, reasons

    reasons.append("generic edge evidence needs mapped path confirmation")
    return "needs_path_evidence", score, reasons


def exit_record_is_actionable(record: Mapping[str, object]) -> bool:
    if str(record.get("guess_state") or "") == "confirmed":
        return True
    if record.get("path_continuation"):
        return True
    return str(record.get("exit_candidate_state") or "") == "semantic_ready"


def _adjust_exit_belief_scores(
    record: Mapping[str, object],
    scores: Mapping[str, float],
) -> dict[str, float]:
    """Prevent one-frame visual evidence from dominating Guessing v3 beliefs."""

    result = {str(key): float(value) for key, value in scores.items()}
    source = exit_candidate_source(record)
    if source is None or record.get("path_continuation"):
        return result

    state = str(record.get("exit_candidate_state") or "visual_candidate")
    if state in {"semantic_ready", "confirmed"}:
        return result

    opening = _clamp(_safe_float(record.get("edge_opening_score")))
    # The calibrated v3 model normally adds opening_score * 2.0.  Remove that
    # semantic-sized boost and replace it with a candidate-sized clue.
    result["possible_exit"] = max(
        0.05,
        float(result.get("possible_exit", 0.0)) - opening * 2.0,
    )
    candidate_bonus = {
        "doorway_facade": 0.62,
        "dark_edge_opening": 0.42,
        "floor_boundary": 0.14,
        "scrolling_floor_boundary": 0.08,
        "generic_edge_opening": 0.24,
    }.get(source, 0.18)
    result["possible_exit"] += candidate_bonus
    if state == "contradicted":
        result["possible_exit"] *= 0.48
        result["scenery"] = float(result.get("scenery", 0.0)) + 0.75
    elif state == "needs_path_evidence":
        result["scenery"] = float(result.get("scenery", 0.0)) + 0.12
    return result


def _belief_scores_v2(
    record: Mapping[str, object],
    consistency: float,
    sample_count: int,
) -> dict[str, float]:
    assert _ORIGINAL_BELIEF_SCORES is not None
    scores = _ORIGINAL_BELIEF_SCORES(record, consistency, sample_count)
    return _adjust_exit_belief_scores(record, scores)


def _postprocess_current_exit_candidates(
    self: StarterPolicy,
    observation,
) -> None:
    # Only records actually analyzed this step may add candidate-view evidence.
    for key in list(getattr(self, "current_visible_regions", set())):
        record = self.screen_regions.get(key)
        if record is None:
            continue
        if _safe_int(record.get("last_seen_step"), -1) != int(observation.step):
            continue
        source = exit_candidate_source(record)
        if source is None:
            continue

        record["exit_detection_version"] = EXIT_DETECTION_VERSION
        record["exit_candidate_source"] = source
        viewpoints = _candidate_viewpoints(record)
        viewpoint = _latest_viewpoint(record)
        if viewpoint is not None and viewpoint not in viewpoints:
            viewpoints.append(viewpoint)
            viewpoints = viewpoints[-MAX_EXIT_CANDIDATE_VIEWPOINTS:]
        record["exit_candidate_viewpoints"] = viewpoints
        record["exit_candidate_views"] = len(viewpoints)
        record["exit_candidate_last_step"] = int(observation.step)

        state, score, reasons = evaluate_exit_candidate(record)
        previous_state = str(record.get("exit_candidate_state") or "")
        record["exit_candidate_state"] = state
        record["exit_candidate_visual_score"] = round(score, 4)
        record["exit_candidate_reasons"] = reasons[-8:]

        if state in {"semantic_ready", "confirmed"}:
            if record.get("hypothesis") != "possible_exit":
                record["exit_candidate_promotions"] = max(
                    0, _safe_int(record.get("exit_candidate_promotions"))
                ) + 1
            record["hypothesis"] = "possible_exit"
            if str(record.get("guess_state") or "") not in v3.FINAL_GUESS_STATES:
                record["guess_state"] = "proposed"
        elif record.get("hypothesis") == "possible_exit":
            # Keep the candidate and its evidence, but do not let the legacy
            # routing field claim a semantic exit before traversability/stability
            # has earned that conclusion.
            record["hypothesis"] = None
            if str(record.get("guess_state") or "") not in v3.FINAL_GUESS_STATES:
                record["guess_state"] = "proposed"

        # Recompute v3 after candidate gating so beliefs, semantic state, ledger,
        # and legacy routing field all agree before the planner sees the record.
        v3.refresh_guess_record_v3(record, region=(key[1], key[2]))
        if state not in {"semantic_ready", "confirmed"} and record.get("hypothesis") == "possible_exit":
            # Defensive final gate: a future v3 calibration must not bypass the
            # detector's traversability requirement merely through probability.
            record["hypothesis"] = None
            if record.get("guess_semantic_state") == "possible_exit":
                record["guess_semantic_state"] = v3.UNKNOWN_BUT_INTERESTING
                record["guess_label"] = "Exit-like feature; traversability unresolved"

        if previous_state != state or source == "mapped_path_continuation":
            self.map_updates.append(self._screen_region_map_update(key, record))


def _observe_screen_v2(self: StarterPolicy, observation, telemetry) -> None:
    assert _ORIGINAL_OBSERVE_SCREEN is not None
    _ORIGINAL_OBSERVE_SCREEN(self, observation, telemetry)
    if not getattr(observation, "visual_valid", False):
        return
    _postprocess_current_exit_candidates(self, observation)


def _map_update_v2(
    key: tuple[str, int, int],
    record: dict[str, object],
) -> dict[str, object]:
    assert _ORIGINAL_MAP_UPDATE is not None
    update = _ORIGINAL_MAP_UPDATE(key, record)
    for field in EXIT_PERSISTED_FIELDS:
        if record.get(field) is not None:
            update[field] = deepcopy(record[field])
    return update


def _visual_exit_is_actionable_v2(
    self: Run4Explorer,
    key: tuple[str, int, int],
    record: dict[str, object],
) -> bool:
    if not exit_record_is_actionable(record):
        return False
    assert _ORIGINAL_VISUAL_EXIT_ACTIONABLE is not None
    return _ORIGINAL_VISUAL_EXIT_ACTIONABLE(self, key, record)


def _summary_v2(self: Run4Explorer) -> dict[str, object]:
    assert _ORIGINAL_SUMMARY is not None
    summary = _ORIGINAL_SUMMARY(self)
    records = list(self.screen_regions.values())
    candidates = [
        record
        for record in records
        if _safe_int(record.get("exit_detection_version")) == EXIT_DETECTION_VERSION
        and exit_candidate_source(record) is not None
    ]
    states: dict[str, int] = {}
    sources: dict[str, int] = {}
    for record in candidates:
        state = str(record.get("exit_candidate_state") or "unknown")
        source = str(record.get("exit_candidate_source") or "unknown")
        states[state] = states.get(state, 0) + 1
        sources[source] = sources.get(source, 0) + 1
    summary.update(
        {
            "exit_detection_version": EXIT_DETECTION_VERSION,
            "exit_visual_candidates": len(candidates),
            "exit_semantic_ready_candidates": states.get("semantic_ready", 0)
            + states.get("confirmed", 0),
            "exit_contradicted_candidates": states.get("contradicted", 0),
            "exit_candidates_needing_path": states.get("needs_path_evidence", 0),
            "exit_candidate_promotions": sum(
                max(0, _safe_int(record.get("exit_candidate_promotions")))
                for record in candidates
            ),
            "exit_candidate_states": states,
            "exit_candidate_sources": sources,
        }
    )
    return summary


def _sanitize_exit_value(field: str, value: object) -> object | None:
    if field in {"exit_candidate_source", "exit_candidate_state"}:
        return str(value)[:120]
    if field in {
        "exit_detection_version",
        "exit_candidate_views",
        "exit_candidate_last_step",
        "exit_candidate_promotions",
    }:
        return max(0, _safe_int(value))
    if field == "exit_candidate_visual_score":
        return round(_clamp(_safe_float(value)), 4)
    if field == "exit_candidate_viewpoints":
        return _candidate_viewpoints({field: value})
    if field == "exit_candidate_reasons":
        if not isinstance(value, list):
            return None
        return [str(item)[:240] for item in value[-8:]]
    return None


def _world_save_v2(self: WorldModel) -> None:
    assert _ORIGINAL_WORLD_SAVE is not None
    _ORIGINAL_WORLD_SAVE(self)
    if self.path is None or not self.path.is_file():
        return
    try:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        items = data.get("screen_regions")
        if not isinstance(items, list):
            return
        by_key = {
            (
                str(item.get("room")),
                _safe_int(item.get("region_x")),
                _safe_int(item.get("region_y")),
            ): item
            for item in items
            if isinstance(item, dict)
        }
        for key, record in self.screen_regions.items():
            item = by_key.get(key)
            if not isinstance(item, dict):
                continue
            for field in EXIT_PERSISTED_FIELDS:
                if field not in record:
                    continue
                sanitized = _sanitize_exit_value(field, record[field])
                if sanitized is not None:
                    item[field] = sanitized
        temporary = self.path.with_suffix(self.path.suffix + ".exit-v2.tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return


def _world_load_v2(cls, path: Path | None) -> WorldModel:
    assert _ORIGINAL_WORLD_LOAD is not None
    model = _ORIGINAL_WORLD_LOAD(cls, path)
    if path is None or not path.is_file():
        return model
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("screen_regions")
        if not isinstance(items, list):
            return model
        for item in items:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("room") or ""),
                _safe_int(item.get("region_x")),
                _safe_int(item.get("region_y")),
            )
            record = model.screen_regions.get(key)
            if record is None:
                continue
            for field in EXIT_PERSISTED_FIELDS:
                if field not in item:
                    continue
                sanitized = _sanitize_exit_value(field, item[field])
                if sanitized is not None:
                    record[field] = sanitized
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return model
    return model


def install_exit_detection_v2() -> None:
    """Install after Guessing v3 and the final screen analyzer are active."""

    global _INSTALLED
    global _ORIGINAL_OBSERVE_SCREEN, _ORIGINAL_MAP_UPDATE
    global _ORIGINAL_VISUAL_EXIT_ACTIONABLE, _ORIGINAL_SUMMARY
    global _ORIGINAL_WORLD_SAVE, _ORIGINAL_WORLD_LOAD, _ORIGINAL_BELIEF_SCORES
    if _INSTALLED:
        return

    _ORIGINAL_OBSERVE_SCREEN = StarterPolicy._observe_screen
    _ORIGINAL_MAP_UPDATE = StarterPolicy._screen_region_map_update
    _ORIGINAL_VISUAL_EXIT_ACTIONABLE = Run4Explorer._visual_exit_is_actionable
    _ORIGINAL_SUMMARY = Run4Explorer.summary
    _ORIGINAL_WORLD_SAVE = WorldModel.save
    _ORIGINAL_WORLD_LOAD = WorldModel.load.__func__
    _ORIGINAL_BELIEF_SCORES = v3._belief_scores

    v3._belief_scores = _belief_scores_v2
    StarterPolicy._observe_screen = _observe_screen_v2  # type: ignore[method-assign]
    StarterPolicy._screen_region_map_update = staticmethod(_map_update_v2)  # type: ignore[method-assign]
    Run4Explorer._visual_exit_is_actionable = _visual_exit_is_actionable_v2  # type: ignore[method-assign]
    Run4Explorer.summary = _summary_v2  # type: ignore[method-assign]
    WorldModel.save = _world_save_v2  # type: ignore[method-assign]
    WorldModel.load = classmethod(_world_load_v2)  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "EXIT_DETECTION_VERSION",
    "evaluate_exit_candidate",
    "exit_candidate_source",
    "exit_record_is_actionable",
    "install_exit_detection_v2",
]
