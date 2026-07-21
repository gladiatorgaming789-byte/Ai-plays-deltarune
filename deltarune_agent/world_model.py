from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from .navigation_semantics import (
    WARP_PORTAL_CLUSTER_RADIUS,
    PortalCluster,
    Warp,
    canonicalize_warp_observations,
    refresh_portal_classification,
    stable_portal_id,
)


Cell = tuple[str, int, int]
Edge = tuple[str, int, int, str]
OpenEdge = tuple[str, int, int, str, int, int]
InteractableKey = tuple[str, int, int]
ScreenRegionKey = tuple[str, int, int]
CELL_SIZE = 8
EXPLORATION_REGION_CELLS = 4


class WorldModel:
    """Persistent knowledge learned only from observed movement and room changes."""

    VERSION = 3

    def __init__(self, path: Path | None = None):
        self.path = path
        self.visits: Counter[Cell] = Counter()
        self.blocked: Counter[Edge] = Counter()
        self.tried: set[Edge] = set()
        self.open_edges: set[OpenEdge] = set()
        self.seen_cells: set[Cell] = set()
        self.transitions: Counter[tuple[str, str]] = Counter()
        self.room_entry_from: dict[str, str] = {}
        self.suppressed_room_links: set[frozenset[str]] = set()
        self.warps: Counter[Warp] = Counter()
        self.warp_portals: dict[str, dict[str, object]] = {}
        self.exit_probes: Counter[Edge] = Counter()
        self.character_probes: Counter[Edge] = Counter()
        self.interactables: dict[InteractableKey, dict[str, object]] = {}
        self.screen_regions: dict[ScreenRegionKey, dict[str, object]] = {}
        self.choice_trials: list[dict[str, object]] = []
        self.load_warning: str | None = None

    @classmethod
    def load(cls, path: Path | None) -> WorldModel:
        model = cls(path)
        if path is None or not path.exists():
            return model
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = int(data.get("version", 1))
            if version not in {1, 2, cls.VERSION}:
                raise ValueError(f"unsupported world-model version {version!r}")
            stored_cell_size = int(
                data.get("cell_size", 16 if version == 1 else CELL_SIZE)
            )
            if stored_cell_size < CELL_SIZE or stored_cell_size % CELL_SIZE:
                raise ValueError(f"unsupported map cell size {stored_cell_size!r}")
            scale = stored_cell_size // CELL_SIZE

            def coordinate(value: object) -> int:
                return int(value) * scale

            for item in data.get("cells", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                )
                model.visits[key] = int(item["visits"])
                model.seen_cells.add(key)
            for item in data.get("tried_edges", []):
                model.tried.add(
                    (
                        str(item["room"]),
                        coordinate(item["x"]),
                        coordinate(item["y"]),
                        str(item["direction"]),
                    )
                )
            for item in data.get("blocked_edges", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.blocked[key] = int(item["failures"])
            for item in data.get("exit_probes", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.exit_probes[key] = int(item.get("attempts", 1))
            for item in data.get("character_probes", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                    str(item["direction"]),
                )
                model.character_probes[key] = int(item.get("attempts", 1))
            for item in data.get("open_edges", []):
                model._load_open_path(
                    str(item["room"]),
                    (coordinate(item["from_x"]), coordinate(item["from_y"])),
                    str(item["direction"]),
                    (coordinate(item["to_x"]), coordinate(item["to_y"])),
                )
            for item in data.get("warps", []):
                key = (
                    str(item["from_room"]),
                    coordinate(item["from_x"]),
                    coordinate(item["from_y"]),
                    str(item["action"]),
                    str(item["to_room"]),
                    coordinate(item["to_x"]),
                    coordinate(item["to_y"]),
                )
                count = int(item["count"])
                model.warps[key] = count
                model.transitions[(key[0], key[4])] += count
            entries = data.get("room_entry_from", {})
            if isinstance(entries, dict):
                model.room_entry_from = {
                    str(room): str(source)
                    for room, source in entries.items()
                    if str(room) and str(source)
                }
            for item in data.get("suppressed_room_links", []):
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    source, target = (str(value) for value in item)
                    if source and target and source != target:
                        model.suppressed_room_links.add(frozenset((source, target)))
            portal_items = data.get("warp_portals", [])
            if isinstance(portal_items, list):
                for item in portal_items:
                    if isinstance(item, dict):
                        model._load_warp_portal(item, coordinate)
            model.reconcile_warp_portals()
            for item in data.get("interactables", []):
                key = (
                    str(item["room"]),
                    coordinate(item["x"]),
                    coordinate(item["y"]),
                )
                model.interactables[key] = {
                    "name": str(item.get("name") or "interaction"),
                    "instance_id": (
                        int(item["instance_id"])
                        if item.get("instance_id") is not None
                        else None
                    ),
                    "confirmations": int(item.get("confirmations", 1)),
                    "attempts": int(item.get("attempts", item.get("confirmations", 1))),
                    "dialogue_steps": int(item.get("dialogue_steps", 0)),
                    "cutscene_steps": int(item.get("cutscene_steps", 0)),
                    "progressions": int(item.get("progressions", 0)),
                    "last_story_epoch": int(item.get("last_story_epoch", 0)),
                    "choice_menus": int(item.get("choice_menus", 0)),
                    "classification": str(item.get("classification") or "unknown"),
                    "usefulness": str(item.get("usefulness") or "unknown"),
                    "last_outcome": str(item.get("last_outcome") or "unknown"),
                    "outcome_counts": {
                        str(name): int(count)
                        for name, count in (
                            item.get("outcome_counts") or {}
                        ).items()
                    }
                    if isinstance(item.get("outcome_counts"), dict)
                    else {},
                    "approaches": [
                        {
                            "x": coordinate(approach["x"]),
                            "y": coordinate(approach["y"]),
                            "direction": str(approach["direction"]),
                        }
                        for approach in item.get("approaches", [])
                        if isinstance(approach, dict)
                        and str(approach.get("direction"))
                        in {"up", "down", "left", "right"}
                    ],
                }
            for item in data.get("screen_regions", []):
                key = (
                    str(item["room"]),
                    int(item["region_x"]),
                    int(item["region_y"]),
                )
                screen_record: dict[str, object] = {
                    "views": int(item.get("views", 1)),
                    "independent_views": int(
                        item.get("independent_views", item.get("views", 1))
                    ),
                    "interest": float(item.get("interest", 0.0)),
                    "hypothesis": item.get("hypothesis"),
                    "inspections": int(item.get("inspections", 0)),
                    "completed_tests": int(
                        item.get("completed_tests", item.get("inspections", 0))
                    ),
                    "approach_attempts": int(item.get("approach_attempts", 0)),
                    "failed_approaches": int(item.get("failed_approaches", 0)),
                    "guess_model_version": int(
                        item.get("guess_model_version", 0)
                    ),
                    "appearance_changes": int(item.get("appearance_changes", 0)),
                    "motion": float(item.get("motion", 0.0)),
                    "last_interest": float(
                        item.get("last_interest", item.get("interest", 0.0))
                    ),
                    "last_signature": str(item.get("last_signature") or ""),
                    "walkable_evidence": bool(item.get("walkable_evidence", False)),
                    "entity_approach_directions": int(
                        item.get("entity_approach_directions", 0)
                    ),
                    "obstruction_target_cells": int(
                        item.get("obstruction_target_cells", 0)
                    ),
                    "character_probe_version": int(
                        item.get("character_probe_version", 0)
                    ),
                    "path_continuation": bool(item.get("path_continuation", False)),
                    "choice_retry": bool(item.get("choice_retry", False)),
                    "guess_misses": int(item.get("guess_misses", 0)),
                }
                if item.get("retired_reason"):
                    screen_record["retired_reason"] = str(item["retired_reason"])
                for field in (
                    "guess_label",
                    "evidence_kind",
                    "evidence_summary",
                    "edge_hint",
                    "visual_summary",
                    "guess_id",
                    "guess_state",
                    "last_failure_reason",
                    "confirmed_target_room",
                ):
                    if item.get(field):
                        screen_record[field] = str(item[field])
                for field in (
                    "guess_confidence",
                    "contrast",
                    "edge_density",
                    "colorfulness",
                    "dark_ratio",
                    "edge_opening_score",
                    "edge_width_ratio",
                ):
                    if item.get(field) is not None:
                        screen_record[field] = float(item[field])
                if item.get("last_seen_step") is not None:
                    screen_record["last_seen_step"] = int(item["last_seen_step"])
                if item.get("last_seen_sequence") is not None:
                    screen_record["last_seen_sequence"] = int(
                        item["last_seen_sequence"]
                    )
                if item.get("cooldown_until_tick") is not None:
                    screen_record["cooldown_until_tick"] = int(
                        item["cooldown_until_tick"]
                    )
                for field in (
                    "anchor_cell",
                    "anchor_world",
                    "focus_world",
                    "feature_box_world",
                    "visual_box_world",
                    "passage_box_world",
                    "obstruction_box_world",
                    "confirmed_at_cell",
                    "confirmed_interactable_cell",
                ):
                    value = item.get(field)
                    if isinstance(value, (list, tuple)):
                        screen_record[field] = list(value)
                for field in (
                    "approach_directions",
                    "obstruction_cells",
                    "evidence_viewpoints",
                ):
                    value = item.get(field)
                    if isinstance(value, list):
                        screen_record[field] = list(value)
                model.screen_regions[key] = screen_record
            for item in data.get("choice_trials", []):
                if not isinstance(item, dict):
                    continue
                model.choice_trials.append(
                    {
                        "room": str(item.get("room") or "unknown"),
                        "context_x": int(item.get("context_x", -1)),
                        "context_y": int(item.get("context_y", -1)),
                        "signature": str(item.get("signature") or ""),
                        "attempts": [int(value) for value in item.get("attempts", [])],
                        "failures": [int(value) for value in item.get("failures", [])],
                        "successes": [int(value) for value in item.get("successes", [])],
                        "successful_pattern": (
                            int(item["successful_pattern"])
                            if item.get("successful_pattern") is not None
                            else None
                        ),
                    }
                )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            model = cls(path)
            model.load_warning = f"Could not load {path}: {exc}. Starting with empty memory."
        return model

    def _load_open_path(
        self,
        room: str,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        """Migrate only observed cardinal paths into adjacent routing edges."""
        vectors = {
            "up": (0, -1),
            "down": (0, 1),
            "left": (-1, 0),
            "right": (1, 0),
        }
        opposites = {
            "up": "down",
            "down": "up",
            "left": "right",
            "right": "left",
        }
        vector = vectors.get(direction)
        if vector is None:
            return
        dx = target[0] - source[0]
        dy = target[1] - source[1]
        forward = dx * vector[0] + dy * vector[1]
        lateral = abs(dx * vector[1] - dy * vector[0])
        if forward <= 0 or lateral != 0:
            # Old maps could contain diagonal links inferred across packet gaps.
            # Dropping those is safer than teaching the planner a route it never took.
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector[0], current[1] + vector[1])
            self.open_edges.add((room, *current, direction, *following))
            self.open_edges.add((room, *following, opposites[direction], *current))
            current = following

    def record_warp_transition(
        self,
        warp: Warp,
        *,
        destination_was_novel: bool,
        step: int | None = None,
    ) -> str:
        """Record one observed transition and return its stable portal ID.

        This is the authoritative API for new policy integrations: it updates
        both the legacy counters and the richer outcome record.  Existing code
        that still writes ``warps`` directly remains compatible because
        :meth:`reconcile_warp_portals` runs before every save.
        """

        warp = self._validated_warp(warp)
        portal_id = self.portal_id_for_warp(warp, create=True)
        assert portal_id is not None
        record = self.warp_portals[portal_id]

        self.warps[warp] += 1
        self.transitions[(warp[0], warp[4])] += 1
        record["crossings"] = int(record.get("crossings", 0)) + 1
        self._increment_portal_sample(record, "source_samples", warp[1], warp[2])
        self._increment_portal_sample(record, "arrival_samples", warp[5], warp[6])
        self._increment_portal_variant(record, warp)
        if destination_was_novel:
            if not record.get("first_novel_destination"):
                record["first_novel_destination"] = warp[4]
            record["novel_destination_crossings"] = int(
                record.get("novel_destination_crossings", 0)
            ) + 1
            # Novelty is useful map knowledge but intentionally not a story
            # progression outcome.
            record["discovery_only_outcomes"] = int(
                record.get("discovery_only_outcomes", 0)
            ) + 1
        if step is not None:
            observed_step = max(0, int(step))
            if record.get("first_seen_step") is None:
                record["first_seen_step"] = observed_step
            record["last_seen_step"] = observed_step
        self._refresh_portal(record)
        return portal_id

    def portal_id_for_warp(self, warp: Warp, *, create: bool = False) -> str | None:
        """Resolve an exact or nearby same-direction observation to one portal."""

        warp = self._validated_warp(warp)
        candidate = self._matching_portal(warp)
        if candidate is not None or not create:
            return candidate
        legacy_count = max(0, int(self.warps.get(warp, 0)))
        cluster = self._single_warp_cluster(warp, legacy_count)
        portal_id = self._available_portal_id(stable_portal_id(cluster))
        record = self._portal_record_from_cluster(portal_id, cluster)
        self.warp_portals[portal_id] = record
        return portal_id

    def portal_metadata(
        self,
        portal: str | Warp,
        *,
        create: bool = False,
    ) -> dict[str, object] | None:
        """Return outcome metadata by stable ID or legacy warp tuple."""

        if isinstance(portal, str):
            return self.warp_portals.get(portal)
        portal_id = self.portal_id_for_warp(portal, create=create)
        return self.warp_portals.get(portal_id) if portal_id is not None else None

    def record_warp_progress(
        self,
        portal: str | Warp,
        outcome: str,
        *,
        discovery_only: bool = False,
        step: int | None = None,
    ) -> None:
        """Attach a later observed outcome to a previously crossed portal.

        Callers must pass ``discovery_only=True`` when the only outcome was
        entering an unseen room.  Such events are retained but can never yield
        the ``progression`` role.
        """

        record = self._observed_portal_metadata(portal)
        if record is None:
            return
        outcome = str(outcome).strip() or "unspecified progress"
        if discovery_only:
            record["discovery_only_outcomes"] = int(
                record.get("discovery_only_outcomes", 0)
            ) + 1
        else:
            record["non_discovery_progress_outcomes"] = int(
                record.get("non_discovery_progress_outcomes", 0)
            ) + 1
            outcomes = record.setdefault("progress_outcomes", {})
            assert isinstance(outcomes, dict)
            outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
        if step is not None:
            record["last_outcome_step"] = max(0, int(step))
        self._refresh_portal(record)

    def record_warp_return(
        self,
        outbound_portal: str | Warp,
        *,
        dwell_steps: int,
        returned_via: str | Warp | None = None,
        immediate_threshold: int = 20,
        step: int | None = None,
    ) -> None:
        """Record an observed return after crossing a portal.

        The outbound portal accumulates dwell/optional evidence.  When the
        caller supplies the reverse portal, that directed link gains explicit
        ``return/backtrack`` evidence.
        """

        outbound = self._observed_portal_metadata(outbound_portal)
        if outbound is None:
            return
        dwell = max(0, int(dwell_steps))
        outbound["dwell_samples"] = int(outbound.get("dwell_samples", 0)) + 1
        outbound["dwell_steps_total"] = int(
            outbound.get("dwell_steps_total", 0)
        ) + dwell
        outbound["dwell_steps_max"] = max(
            int(outbound.get("dwell_steps_max", 0)),
            dwell,
        )
        if dwell <= max(0, int(immediate_threshold)):
            outbound["immediate_returns"] = int(
                outbound.get("immediate_returns", 0)
            ) + 1
        if step is not None:
            outbound["last_outcome_step"] = max(0, int(step))
        self._refresh_portal(outbound)

        if returned_via is None:
            return
        return_record = self._observed_portal_metadata(returned_via)
        if return_record is None:
            return
        return_record["return_backtracks"] = int(
            return_record.get("return_backtracks", 0)
        ) + 1
        if step is not None:
            return_record["last_outcome_step"] = max(0, int(step))
        self._refresh_portal(return_record)

    def record_warp_suppression(
        self,
        portal: str | Warp,
        reason: str = "observed navigation loop",
        *,
        step: int | None = None,
    ) -> None:
        """Record policy suppression without erasing the observed portal."""

        record = self._observed_portal_metadata(portal)
        if record is None:
            return
        record["loop_suppressions"] = int(record.get("loop_suppressions", 0)) + 1
        reasons = record.setdefault("suppression_reasons", {})
        assert isinstance(reasons, dict)
        reason = str(reason).strip() or "observed navigation loop"
        reasons[reason] = int(reasons.get(reason, 0)) + 1
        if step is not None:
            record["last_outcome_step"] = max(0, int(step))
        self._refresh_portal(record)

    def reconcile_warp_portals(self) -> None:
        """Migrate direct legacy-counter writes into rich portal metadata."""

        for cluster in canonicalize_warp_observations(self.warps):
            representative = cluster.variants[0][0]
            portal_id = self._matching_portal(representative)
            if portal_id is None:
                portal_id = self._available_portal_id(stable_portal_id(cluster))
                self.warp_portals[portal_id] = self._portal_record_from_cluster(
                    portal_id,
                    cluster,
                )
            else:
                self._merge_cluster(self.warp_portals[portal_id], cluster)
        for record in self.warp_portals.values():
            self._refresh_portal(record)

    def _load_warp_portal(self, item: dict[str, object], coordinate) -> None:
        portal_id = str(item.get("id") or "").strip()
        source_room = str(item.get("from_room") or "").strip()
        target_room = str(item.get("to_room") or "").strip()
        action = str(item.get("action") or "").strip()
        if not portal_id or not source_room or not target_room or not action:
            return
        if portal_id in self.warp_portals:
            return

        def count_map(value: object) -> dict[str, int]:
            if not isinstance(value, dict):
                return {}
            return {
                str(name): max(0, int(count))
                for name, count in value.items()
                if str(name) and _is_int(count) and int(count) > 0
            }

        record: dict[str, object] = {
            "id": portal_id,
            "from_room": source_room,
            "to_room": target_room,
            "action": action,
            "role": str(item.get("role") or "unknown"),
            "confidence": _safe_float(item.get("confidence"), 0.25),
            "basis": [str(value) for value in item.get("basis", [])]
            if isinstance(item.get("basis"), list)
            else [],
            "crossings": _safe_nonnegative_int(item.get("crossings")),
            "first_novel_destination": (
                str(item["first_novel_destination"])
                if item.get("first_novel_destination")
                else None
            ),
            "novel_destination_crossings": _safe_nonnegative_int(
                item.get("novel_destination_crossings")
            ),
            "discovery_only_outcomes": _safe_nonnegative_int(
                item.get("discovery_only_outcomes")
            ),
            "non_discovery_progress_outcomes": _safe_nonnegative_int(
                item.get("non_discovery_progress_outcomes")
            ),
            "progress_outcomes": count_map(item.get("progress_outcomes")),
            "immediate_returns": _safe_nonnegative_int(
                item.get("immediate_returns")
            ),
            "return_backtracks": _safe_nonnegative_int(
                item.get("return_backtracks")
            ),
            "dwell_samples": _safe_nonnegative_int(item.get("dwell_samples")),
            "dwell_steps_total": _safe_nonnegative_int(
                item.get("dwell_steps_total")
            ),
            "dwell_steps_max": _safe_nonnegative_int(item.get("dwell_steps_max")),
            "loop_suppressions": _safe_nonnegative_int(
                item.get("loop_suppressions")
            ),
            "suppression_reasons": count_map(item.get("suppression_reasons")),
            "first_seen_step": _optional_nonnegative_int(item.get("first_seen_step")),
            "last_seen_step": _optional_nonnegative_int(item.get("last_seen_step")),
            "last_outcome_step": _optional_nonnegative_int(
                item.get("last_outcome_step")
            ),
            "source_samples": self._load_portal_samples(
                item.get("source_samples"), coordinate
            ),
            "arrival_samples": self._load_portal_samples(
                item.get("arrival_samples"), coordinate
            ),
            "variants": self._load_portal_variants(item.get("variants"), coordinate),
        }
        self.warp_portals[portal_id] = record
        self._refresh_portal(record)

    @staticmethod
    def _load_portal_samples(value: object, coordinate) -> list[dict[str, int]]:
        if not isinstance(value, list):
            return []
        samples: list[dict[str, int]] = []
        for sample in value:
            if not isinstance(sample, dict):
                continue
            try:
                count = max(1, int(sample.get("count", 1)))
                samples.append(
                    {
                        "x": coordinate(sample["x"]),
                        "y": coordinate(sample["y"]),
                        "count": count,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return samples

    @staticmethod
    def _load_portal_variants(value: object, coordinate) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        variants: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                variants.append(
                    {
                        "from_x": coordinate(item["from_x"]),
                        "from_y": coordinate(item["from_y"]),
                        "to_x": coordinate(item["to_x"]),
                        "to_y": coordinate(item["to_y"]),
                        "action": str(item["action"]),
                        "count": max(1, int(item.get("count", 1))),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return variants

    @staticmethod
    def _validated_warp(warp: Warp) -> Warp:
        if len(warp) != 7:
            raise ValueError("warp observations must contain seven fields")
        return (
            str(warp[0]),
            int(warp[1]),
            int(warp[2]),
            str(warp[3]),
            str(warp[4]),
            int(warp[5]),
            int(warp[6]),
        )

    def _matching_portal(self, warp: Warp) -> str | None:
        matches: list[tuple[int, str]] = []
        for portal_id, record in self.warp_portals.items():
            if (
                record.get("from_room") != warp[0]
                or record.get("to_room") != warp[4]
                or record.get("action") != warp[3]
            ):
                continue
            distances = [
                max(abs(int(sample["x"]) - warp[1]), abs(int(sample["y"]) - warp[2]))
                for sample in record.get("source_samples", [])
                if isinstance(sample, dict) and "x" in sample and "y" in sample
            ]
            if distances and min(distances) <= WARP_PORTAL_CLUSTER_RADIUS:
                matches.append((min(distances), portal_id))
                continue
            for variant in record.get("variants", []):
                if not isinstance(variant, dict):
                    continue
                if (
                    int(variant.get("from_x", -10_000)) == warp[1]
                    and int(variant.get("from_y", -10_000)) == warp[2]
                    and int(variant.get("to_x", -10_000)) == warp[5]
                    and int(variant.get("to_y", -10_000)) == warp[6]
                ):
                    matches.append((0, portal_id))
                    break
        return min(matches)[1] if matches else None

    def _observed_portal_metadata(
        self,
        portal: str | Warp,
    ) -> dict[str, object] | None:
        if isinstance(portal, str):
            return self.warp_portals.get(portal)
        warp = self._validated_warp(portal)
        if self.warps.get(warp, 0) <= 0:
            return None
        portal_id = self.portal_id_for_warp(warp, create=True)
        return self.warp_portals.get(portal_id) if portal_id is not None else None

    @staticmethod
    def _single_warp_cluster(warp: Warp, crossings: int) -> PortalCluster:
        count = max(0, crossings)
        return PortalCluster(
            source_room=warp[0],
            target_room=warp[4],
            action=warp[3],
            variants=((warp, count),),
            source_bounds=(warp[1], warp[2], warp[1], warp[2]),
            arrival_bounds=(warp[5], warp[6], warp[5], warp[6]),
            source_center=(warp[1], warp[2]),
            arrival_center=(warp[5], warp[6]),
            crossings=max(0, crossings),
        )

    def _available_portal_id(self, preferred: str) -> str:
        if preferred not in self.warp_portals:
            return preferred
        suffix = 2
        while f"{preferred}_{suffix}" in self.warp_portals:
            suffix += 1
        return f"{preferred}_{suffix}"

    @staticmethod
    def _portal_record_from_cluster(
        portal_id: str,
        cluster: PortalCluster,
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "id": portal_id,
            "from_room": cluster.source_room,
            "to_room": cluster.target_room,
            "action": cluster.action,
            "role": "unknown",
            "confidence": 0.25,
            "basis": [],
            "crossings": cluster.crossings,
            "first_novel_destination": None,
            "novel_destination_crossings": 0,
            "discovery_only_outcomes": 0,
            "non_discovery_progress_outcomes": 0,
            "progress_outcomes": {},
            "immediate_returns": 0,
            "return_backtracks": 0,
            "dwell_samples": 0,
            "dwell_steps_total": 0,
            "dwell_steps_max": 0,
            "loop_suppressions": 0,
            "suppression_reasons": {},
            "first_seen_step": None,
            "last_seen_step": None,
            "last_outcome_step": None,
            "source_samples": [
                {"x": warp[1], "y": warp[2], "count": count}
                for warp, count in cluster.variants
                if count > 0
            ],
            "arrival_samples": [
                {"x": warp[5], "y": warp[6], "count": count}
                for warp, count in cluster.variants
                if count > 0
            ],
            "variants": [
                {
                    "from_x": warp[1],
                    "from_y": warp[2],
                    "to_x": warp[5],
                    "to_y": warp[6],
                    "action": warp[3],
                    "count": count,
                }
                for warp, count in cluster.variants
                if count > 0
            ],
        }
        WorldModel._refresh_portal(record)
        return record

    def _merge_cluster(self, record: dict[str, object], cluster: PortalCluster) -> None:
        record["crossings"] = max(
            int(record.get("crossings", 0)),
            cluster.crossings,
        )
        for warp, count in cluster.variants:
            self._merge_portal_sample(
                record,
                "source_samples",
                warp[1],
                warp[2],
                count,
            )
            self._merge_portal_sample(
                record,
                "arrival_samples",
                warp[5],
                warp[6],
                count,
            )
            self._merge_portal_variant(record, warp, count)
        self._refresh_portal(record)

    @staticmethod
    def _increment_portal_sample(
        record: dict[str, object],
        field: str,
        x: int,
        y: int,
    ) -> None:
        samples = record.setdefault(field, [])
        assert isinstance(samples, list)
        for sample in samples:
            if isinstance(sample, dict) and sample.get("x") == x and sample.get("y") == y:
                sample["count"] = int(sample.get("count", 0)) + 1
                return
        samples.append({"x": x, "y": y, "count": 1})

    @staticmethod
    def _merge_portal_sample(
        record: dict[str, object],
        field: str,
        x: int,
        y: int,
        count: int,
    ) -> None:
        samples = record.setdefault(field, [])
        assert isinstance(samples, list)
        for sample in samples:
            if isinstance(sample, dict) and sample.get("x") == x and sample.get("y") == y:
                sample["count"] = max(int(sample.get("count", 0)), count)
                return
        samples.append({"x": x, "y": y, "count": count})

    @staticmethod
    def _increment_portal_variant(record: dict[str, object], warp: Warp) -> None:
        variants = record.setdefault("variants", [])
        assert isinstance(variants, list)
        for variant in variants:
            if WorldModel._variant_matches(variant, warp):
                assert isinstance(variant, dict)
                variant["count"] = int(variant.get("count", 0)) + 1
                return
        variants.append(WorldModel._variant_record(warp, 1))

    @staticmethod
    def _merge_portal_variant(
        record: dict[str, object],
        warp: Warp,
        count: int,
    ) -> None:
        variants = record.setdefault("variants", [])
        assert isinstance(variants, list)
        for variant in variants:
            if WorldModel._variant_matches(variant, warp):
                assert isinstance(variant, dict)
                variant["count"] = max(int(variant.get("count", 0)), count)
                return
        variants.append(WorldModel._variant_record(warp, count))

    @staticmethod
    def _variant_matches(variant: object, warp: Warp) -> bool:
        return isinstance(variant, dict) and (
            variant.get("from_x") == warp[1]
            and variant.get("from_y") == warp[2]
            and variant.get("action") == warp[3]
            and variant.get("to_x") == warp[5]
            and variant.get("to_y") == warp[6]
        )

    @staticmethod
    def _variant_record(warp: Warp, count: int) -> dict[str, object]:
        return {
            "from_x": warp[1],
            "from_y": warp[2],
            "to_x": warp[5],
            "to_y": warp[6],
            "action": warp[3],
            "count": count,
        }

    @staticmethod
    def _refresh_portal(record: dict[str, object]) -> None:
        source_samples = [
            sample
            for sample in record.get("source_samples", [])
            if isinstance(sample, dict) and "x" in sample and "y" in sample
        ]
        arrival_samples = [
            sample
            for sample in record.get("arrival_samples", [])
            if isinstance(sample, dict) and "x" in sample and "y" in sample
        ]
        record["source_footprint"] = _sample_geometry(source_samples)
        record["arrival_footprint"] = _sample_geometry(arrival_samples)
        source_geometry = record["source_footprint"]
        assert isinstance(source_geometry, dict)
        bounds = source_geometry.get("bounds", [0, 0, 0, 0])
        assert isinstance(bounds, list)
        min_x, min_y, max_x, max_y = (int(value) for value in bounds)
        action = str(record.get("action") or "event")
        if action in {"up", "down"}:
            aperture_axis = "horizontal"
            aperture_span = max_x - min_x + 1
        elif action in {"left", "right"}:
            aperture_axis = "vertical"
            aperture_span = max_y - min_y + 1
        else:
            aperture_axis = "point"
            aperture_span = max(max_x - min_x, max_y - min_y) + 1
        record["aperture"] = {
            "axis": aperture_axis,
            "span_cells": max(1, aperture_span),
            "bounds": [min_x, min_y, max_x, max_y],
        }
        refresh_portal_classification(record)

    def save(self) -> None:
        if self.path is None:
            return
        self.reconcile_warp_portals()
        data = {
            "version": self.VERSION,
            "cell_size": CELL_SIZE,
            "cells": [
                {"room": room, "x": x, "y": y, "visits": count}
                for (room, x, y), count in sorted(self.visits.items())
            ],
            "tried_edges": [
                {"room": room, "x": x, "y": y, "direction": direction}
                for room, x, y, direction in sorted(self.tried)
            ],
            "blocked_edges": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "failures": count,
                }
                for (room, x, y, direction), count in sorted(self.blocked.items())
            ],
            "exit_probes": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "attempts": count,
                }
                for (room, x, y, direction), count in sorted(
                    self.exit_probes.items()
                )
            ],
            "character_probes": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "direction": direction,
                    "attempts": count,
                }
                for (room, x, y, direction), count in sorted(
                    self.character_probes.items()
                )
            ],
            "open_edges": [
                {
                    "room": room,
                    "from_x": source_x,
                    "from_y": source_y,
                    "direction": direction,
                    "to_x": target_x,
                    "to_y": target_y,
                }
                for room, source_x, source_y, direction, target_x, target_y in sorted(
                    self.open_edges
                )
            ],
            "warps": [
                {
                    "from_room": source,
                    "from_x": source_x,
                    "from_y": source_y,
                    "action": action,
                    "to_room": target,
                    "to_x": target_x,
                    "to_y": target_y,
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
            "warp_portals": [
                dict(record)
                for _portal_id, record in sorted(self.warp_portals.items())
            ],
            "room_entry_from": dict(sorted(self.room_entry_from.items())),
            "suppressed_room_links": [
                list(sorted(link))
                for link in sorted(
                    self.suppressed_room_links,
                    key=lambda value: tuple(sorted(value)),
                )
            ],
            "interactables": [
                {
                    "room": room,
                    "x": x,
                    "y": y,
                    "name": record.get("name") or "interaction",
                    "instance_id": record.get("instance_id"),
                    "confirmations": int(record.get("confirmations", 1)),
                    "attempts": int(record.get("attempts", record.get("confirmations", 1))),
                    "dialogue_steps": int(record.get("dialogue_steps", 0)),
                    "cutscene_steps": int(record.get("cutscene_steps", 0)),
                    "progressions": int(record.get("progressions", 0)),
                    "last_story_epoch": int(record.get("last_story_epoch", 0)),
                    "choice_menus": int(record.get("choice_menus", 0)),
                    "classification": str(
                        record.get("classification") or "unknown"
                    ),
                    "usefulness": str(record.get("usefulness") or "unknown"),
                    "last_outcome": str(record.get("last_outcome") or "unknown"),
                    "outcome_counts": dict(record.get("outcome_counts", {}))
                    if isinstance(record.get("outcome_counts"), dict)
                    else {},
                    "approaches": list(record.get("approaches", [])),
                }
                for (room, x, y), record in sorted(self.interactables.items())
            ],
            "screen_regions": [
                {
                    "room": room,
                    "region_x": region_x,
                    "region_y": region_y,
                    "views": int(record.get("views", 1)),
                    "independent_views": int(
                        record.get("independent_views", record.get("views", 1))
                    ),
                    "interest": float(record.get("interest", 0.0)),
                    "hypothesis": record.get("hypothesis"),
                    "inspections": int(record.get("inspections", 0)),
                    "completed_tests": int(
                        record.get(
                            "completed_tests",
                            record.get("inspections", 0),
                        )
                    ),
                    "approach_attempts": int(record.get("approach_attempts", 0)),
                    "failed_approaches": int(record.get("failed_approaches", 0)),
                    "guess_model_version": int(
                        record.get("guess_model_version", 0)
                    ),
                    "appearance_changes": int(record.get("appearance_changes", 0)),
                    "motion": float(record.get("motion", 0.0)),
                    "last_interest": float(
                        record.get("last_interest", record.get("interest", 0.0))
                    ),
                    "last_signature": str(record.get("last_signature") or ""),
                    "walkable_evidence": bool(
                        record.get("walkable_evidence", False)
                    ),
                    "entity_approach_directions": int(
                        record.get("entity_approach_directions", 0)
                    ),
                    "obstruction_target_cells": int(
                        record.get("obstruction_target_cells", 0)
                    ),
                    "character_probe_version": int(
                        record.get("character_probe_version", 0)
                    ),
                    "path_continuation": bool(
                        record.get("path_continuation", False)
                    ),
                    "choice_retry": bool(record.get("choice_retry", False)),
                    "guess_misses": int(record.get("guess_misses", 0)),
                    **(
                        {"retired_reason": str(record["retired_reason"])}
                        if record.get("retired_reason")
                        else {}
                    ),
                    **{
                        field: record[field]
                        for field in (
                            "guess_label",
                            "evidence_kind",
                            "evidence_summary",
                            "edge_hint",
                            "visual_summary",
                            "guess_id",
                            "guess_state",
                            "guess_confidence",
                            "contrast",
                            "edge_density",
                            "colorfulness",
                            "dark_ratio",
                            "edge_opening_score",
                            "edge_width_ratio",
                            "last_seen_sequence",
                            "last_seen_step",
                            "cooldown_until_tick",
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
                            "last_failure_reason",
                            "confirmed_target_room",
                            "confirmed_at_cell",
                            "confirmed_interactable_cell",
                        )
                        if record.get(field) is not None
                    },
                }
                for (room, region_x, region_y), record in sorted(
                    self.screen_regions.items()
                )
            ],
            "choice_trials": [dict(record) for record in self.choice_trials],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_int(value: object) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _sample_geometry(samples: list[dict[str, object]]) -> dict[str, object]:
    if not samples:
        return {
            "bounds": [0, 0, 0, 0],
            "center": [0, 0],
            "sample_count": 0,
            "variant_count": 0,
        }
    weighted: list[tuple[int, int, int]] = []
    for sample in samples:
        try:
            weighted.append(
                (
                    int(sample["x"]),
                    int(sample["y"]),
                    max(1, int(sample.get("count", 1))),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not weighted:
        return {
            "bounds": [0, 0, 0, 0],
            "center": [0, 0],
            "sample_count": 0,
            "variant_count": 0,
        }
    total = sum(count for _x, _y, count in weighted)
    xs = [x for x, _y, _count in weighted]
    ys = [y for _x, y, _count in weighted]
    return {
        "bounds": [min(xs), min(ys), max(xs), max(ys)],
        "center": [
            round(sum(x * count for x, _y, count in weighted) / total),
            round(sum(y * count for _x, y, count in weighted) / total),
        ],
        "sample_count": total,
        "variant_count": len(weighted),
    }
