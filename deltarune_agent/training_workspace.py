from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping
from uuid import uuid4

from .population_training import PopulationCoordinator, TRAINING_SCHEMA_VERSION
from .reinforcement import (
    REINFORCEMENT_MEMORY_FILENAME,
    REINFORCEMENT_SETTINGS_FILENAME,
    ReinforcementMemory,
    load_reward_settings,
)
from .strategy import STRATEGY_FILENAME, StrategyGenome


SHARED_MEMORY_NAMES = (
    "navigation.json",
    "visual_states.json",
    "room_views",
    REINFORCEMENT_SETTINGS_FILENAME,
    "window_titles.json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def memory_inventory(directory: Path) -> dict[str, dict[str, object]]:
    directory = Path(directory).resolve()
    if not directory.exists():
        return {}
    inventory: dict[str, dict[str, object]] = {}
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
        relative = path.relative_to(directory).as_posix()
        inventory[relative] = {
            "sha256": _file_hash(path),
            "size_bytes": path.stat().st_size,
        }
    return inventory


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _known_rooms(navigation_path: Path) -> set[str]:
    try:
        payload = json.loads(navigation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    rooms: set[str] = set()
    if isinstance(payload, Mapping):
        for name in ("cells", "warps", "screen_regions", "interactables"):
            records = payload.get(name)
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, Mapping) and record.get("room"):
                    rooms.add(str(record["room"]))
                elif isinstance(record, (list, tuple)) and record:
                    rooms.add(str(record[0]))
    return rooms


@dataclass
class TrainingWorkspace:
    run_directory: Path
    source_memory: Path
    root: Path
    baseline_memory: Path
    shared_memory: Path
    candidates: Path
    baseline_inventory: dict[str, dict[str, object]]
    session_id: str
    baseline_genome: StrategyGenome
    strategy_warning: str | None = None

    @classmethod
    def create(cls, run_directory: Path, source_memory: Path) -> "TrainingWorkspace":
        run_directory = Path(run_directory).resolve()
        source_memory = Path(source_memory).resolve()
        root = run_directory / "training_workspace"
        baseline_memory = root / "baseline_memory"
        shared = root / "shared_memory"
        candidates = root / "candidates"
        if root.exists():
            raise FileExistsError(f"Training workspace already exists: {root}")
        baseline_memory.mkdir(parents=True)
        shared.mkdir(parents=True)
        candidates.mkdir(parents=True)
        baseline_inventory = memory_inventory(source_memory)
        for name in SHARED_MEMORY_NAMES:
            _copy_path(source_memory / name, shared / name)
        baseline_reinforcement = source_memory / REINFORCEMENT_MEMORY_FILENAME
        baseline_strategy = source_memory / STRATEGY_FILENAME
        genome, warning = StrategyGenome.load(baseline_strategy)
        _copy_path(
            baseline_reinforcement,
            baseline_memory / REINFORCEMENT_MEMORY_FILENAME,
        )
        if not (baseline_memory / REINFORCEMENT_MEMORY_FILENAME).is_file():
            ReinforcementMemory(
                baseline_memory / REINFORCEMENT_MEMORY_FILENAME
            ).flush(force=True)
        genome.save(baseline_memory / STRATEGY_FILENAME)
        session_id = uuid4().hex
        for candidate_id in ("balanced", "explorer", "progress", "loop_safe"):
            directory = candidates / candidate_id
            directory.mkdir(parents=True)
            _copy_path(
                baseline_memory / REINFORCEMENT_MEMORY_FILENAME,
                directory / REINFORCEMENT_MEMORY_FILENAME,
            )
        workspace = cls(
            run_directory=run_directory,
            source_memory=source_memory,
            root=root,
            baseline_memory=baseline_memory,
            shared_memory=shared,
            candidates=candidates,
            baseline_inventory=baseline_inventory,
            session_id=session_id,
            baseline_genome=genome,
            strategy_warning=warning,
        )
        workspace.write_baseline_artifacts()
        return workspace

    @property
    def navigation_path(self) -> Path:
        return self.shared_memory / "navigation.json"

    @property
    def visual_memory_path(self) -> Path:
        return self.shared_memory / "visual_states.json"

    @property
    def window_memory_path(self) -> Path:
        return self.shared_memory / "window_titles.json"

    @property
    def events_path(self) -> Path:
        return self.run_directory / "population_events.jsonl"

    def write_baseline_artifacts(self) -> None:
        _write_json(
            self.run_directory / "baseline_fingerprints.json",
            {
                "schema_version": TRAINING_SCHEMA_VERSION,
                "source_memory": str(self.source_memory),
                "captured_at": _utc_now(),
                "inventory": self.baseline_inventory,
            },
        )
        _write_json(
            self.run_directory / "training_manifest.json",
            {
                "schema_version": TRAINING_SCHEMA_VERSION,
                "session_id": self.session_id,
                "status": "running",
                "started_at": _utc_now(),
                "source_memory": str(self.source_memory),
                "workspace": str(self.root),
                "baseline_memory": str(self.baseline_memory),
                "shared_memory": str(self.shared_memory),
                "candidate_ids": ["balanced", "explorer", "progress", "loop_safe"],
                "baseline_strategy": self.baseline_genome.to_dict(),
                "strategy_warning": self.strategy_warning,
            },
        )

    def coordinator(self) -> PopulationCoordinator:
        baseline_reinforcement = ReinforcementMemory.load(
            self.baseline_memory / REINFORCEMENT_MEMORY_FILENAME
        )
        reward_settings = load_reward_settings(
            self.shared_memory / REINFORCEMENT_SETTINGS_FILENAME
        )
        return PopulationCoordinator(
            session_id=self.session_id,
            baseline_genome=self.baseline_genome,
            baseline_reinforcement=baseline_reinforcement,
            candidates_directory=self.candidates,
            events_path=self.events_path,
            reward_settings=reward_settings,
            known_rooms=_known_rooms(self.navigation_path),
        )

    def finalize(
        self,
        coordinator: PopulationCoordinator,
        *,
        stop_reason: str,
        telemetry_diagnostics: Mapping[str, object],
        speed_diagnostics: Mapping[str, object],
        input_cleanup_succeeded: bool,
        doctor_payload: Mapping[str, object] | None,
    ) -> dict[str, object]:
        coordinator.finish_active_segment(stop_reason)
        coordinator.flush_candidates()
        received = int(telemetry_diagnostics.get("received_packets") or 0)
        valid = int(telemetry_diagnostics.get("valid_packets") or 0)
        invalid = int(telemetry_diagnostics.get("invalid_packets") or 0)
        invalid_rate = invalid / max(1, valid + invalid)
        telemetry_coverage = coordinator.telemetry_coverage()
        verification = str(speed_diagnostics.get("verification_state") or "")
        requested = str(speed_diagnostics.get("requested") or "")
        speed_ok = verification == "matched" or (
            requested in {"1", "1.0"} and verification == "not_required"
        )
        severity = doctor_payload.get("severity_counts") if isinstance(doctor_payload, Mapping) else None
        critical_count = int(severity.get("critical") or 0) if isinstance(severity, Mapping) else 1
        global_checks = {
            "clean_stop": stop_reason in {"step_limit", "gui_stop"},
            "all_candidates_exposed": all(
                candidate.minimum_exposure_met for candidate in coordinator.candidates
            ),
            "telemetry_coverage": telemetry_coverage >= 0.90,
            "invalid_packet_rate": invalid_rate < 0.05,
            "speed_verification": speed_ok,
            "input_cleanup": bool(input_cleanup_succeeded),
            "run_doctor": critical_count == 0,
        }
        eligible = all(global_checks.values())
        ranked = coordinator.ranked_candidates()
        eligible_candidates = [
            candidate
            for candidate in ranked
            if candidate.minimum_exposure_met and not candidate.disqualified
        ]
        winner = eligible_candidates[0] if eligible and eligible_candidates else None
        explanation = (
            (
                f"{winner.label} had the best normalized score ({winner.normalized_score:.3f}), "
                f"with {winner.story_progress} story-progress event(s) and "
                f"{winner.safety_penalties} safety penalty event(s)."
            )
            if winner is not None
            else "No winner can be recommended until every global and candidate safety gate passes."
        )
        result: dict[str, object] = {
            "schema_version": TRAINING_SCHEMA_VERSION,
            "session_id": self.session_id,
            "eligible_for_promotion": winner is not None,
            "recommended_winner": winner.candidate_id if winner is not None else None,
            "winner_explanation": explanation,
            "global_checks": global_checks,
            "measurements": {
                "telemetry_coverage": round(telemetry_coverage, 6),
                "received_packets": received,
                "valid_packets": valid,
                "invalid_packets": invalid,
                "invalid_packet_rate": round(invalid_rate, 6),
                "speed_verification": verification,
                "run_doctor_critical_findings": critical_count,
            },
            "candidates": [candidate.as_dict() for candidate in ranked],
        }
        _write_json(self.run_directory / "training_scores.json", result)
        manifest_path = self.run_directory / "training_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "status": "review_ready" if winner is not None else "ineligible",
                "ended_at": _utc_now(),
                "stop_reason": stop_reason,
                "eligibility": result,
            }
        )
        _write_json(manifest_path, manifest)
        return result


def _load_training_manifest(run_directory: Path) -> dict[str, object]:
    path = Path(run_directory) / "training_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("training_manifest.json must contain an object")
    return payload


def _verified_overlay(source: Path, destination: Path) -> None:
    _copy_path(source, destination)
    source_inventory = memory_inventory(source) if source.is_dir() else {
        source.name: {"sha256": _file_hash(source), "size_bytes": source.stat().st_size}
    }
    if source.is_dir():
        destination_inventory = memory_inventory(destination)
        for relative, record in source_inventory.items():
            if destination_inventory.get(relative) != record:
                raise OSError(f"Staged copy verification failed for {relative}")
    elif not destination.is_file() or _file_hash(destination) != _file_hash(source):
        raise OSError(f"Staged copy verification failed for {destination.name}")


def promote_training_run(run_directory: Path, profile_memory: Path) -> dict[str, object]:
    """Promote one reviewed winner through a verified same-volume transaction."""

    run_directory = Path(run_directory).resolve()
    profile_memory = Path(profile_memory).resolve()
    manifest = _load_training_manifest(run_directory)
    eligibility = manifest.get("eligibility")
    if not isinstance(eligibility, Mapping) or not eligibility.get("eligible_for_promotion"):
        raise ValueError("This training run has no eligible recommended winner.")
    winner = str(eligibility.get("recommended_winner") or "")
    if not winner:
        raise ValueError("The eligible training run did not record a winner.")
    baseline_payload = json.loads(
        (run_directory / "baseline_fingerprints.json").read_text(encoding="utf-8")
    )
    baseline = baseline_payload.get("inventory") if isinstance(baseline_payload, Mapping) else None
    if not isinstance(baseline, Mapping):
        raise ValueError("The training baseline inventory is missing.")
    current = memory_inventory(profile_memory)
    if current != dict(baseline):
        raise RuntimeError(
            "Promotion refused because the active profile memory changed after training began."
        )

    workspace = run_directory / "training_workspace"
    shared = workspace / "shared_memory"
    candidate = workspace / "candidates" / winner
    for required in (
        shared / "navigation.json",
        shared / "visual_states.json",
        candidate / REINFORCEMENT_MEMORY_FILENAME,
        candidate / STRATEGY_FILENAME,
    ):
        if not required.is_file():
            raise FileNotFoundError(f"Promotion artifact is missing: {required}")

    parent = profile_memory.parent
    transaction_id = uuid4().hex
    staging = parent / f".{profile_memory.name}.{transaction_id}.promoting"
    backups_root = parent / ".training-backups"
    backup = backups_root / f"{profile_memory.name}-{transaction_id}"
    if staging.exists() or backup.exists():
        raise FileExistsError("Promotion staging path already exists.")
    shutil.copytree(profile_memory, staging)
    try:
        _verified_overlay(shared / "navigation.json", staging / "navigation.json")
        _verified_overlay(shared / "visual_states.json", staging / "visual_states.json")
        if (shared / "room_views").is_dir():
            shutil.rmtree(staging / "room_views", ignore_errors=True)
            _verified_overlay(shared / "room_views", staging / "room_views")
        _verified_overlay(
            candidate / REINFORCEMENT_MEMORY_FILENAME,
            staging / REINFORCEMENT_MEMORY_FILENAME,
        )
        _verified_overlay(candidate / STRATEGY_FILENAME, staging / STRATEGY_FILENAME)
        history_path = staging / "training_history.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (OSError, UnicodeError, json.JSONDecodeError):
            history = []
        audit = {
            "schema_version": TRAINING_SCHEMA_VERSION,
            "promoted_at": _utc_now(),
            "session_id": manifest.get("session_id"),
            "run_directory": str(run_directory),
            "winner": winner,
            "winner_explanation": eligibility.get("winner_explanation"),
            "transaction_id": transaction_id,
        }
        history.append(audit)
        _write_json(history_path, history)
        _write_json(staging / "promotion.json", audit)
        staged_inventory = memory_inventory(staging)
        if not staged_inventory:
            raise OSError("Promotion staging verification produced an empty inventory.")

        backups_root.mkdir(parents=True, exist_ok=True)
        os.replace(profile_memory, backup)
        try:
            os.replace(staging, profile_memory)
        except BaseException:
            os.replace(backup, profile_memory)
            raise
        if memory_inventory(profile_memory) != staged_inventory:
            failed = parent / f".{profile_memory.name}.{transaction_id}.failed"
            os.replace(profile_memory, failed)
            os.replace(backup, profile_memory)
            raise OSError("Promotion verification failed after replacement; rollback completed.")
        audit["backup_directory"] = str(backup)
        _write_json(run_directory / "promotion.json", audit)
        manifest["status"] = "promoted"
        manifest["promotion"] = audit
        _write_json(run_directory / "training_manifest.json", manifest)
        return audit
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


__all__ = [
    "SHARED_MEMORY_NAMES",
    "TrainingWorkspace",
    "memory_inventory",
    "promote_training_run",
]
