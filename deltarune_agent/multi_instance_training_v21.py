"""Independent Population Training v2.1 safety and measurement layer.

This module keeps the v2 process-isolation architecture while correcting the
measurement, stopping, output-pressure, experiment-design, and promotion issues
found in the repository-wide audit.  It deliberately reuses the proven v2
workspace/process helpers rather than duplicating save or window-isolation code.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Any, Mapping

from . import multi_instance_training as legacy
from .training_workspace import memory_inventory


MULTI_INSTANCE_ARCHITECTURE = legacy.MULTI_INSTANCE_ARCHITECTURE
MULTI_INSTANCE_SCHEMA_VERSION = 2
SAFE_SUPPORT_MARKER = b"AI_BACKGROUND_AUTOSAVE_V2"
MESSAGE_QUEUE_MAX = 2048
MESSAGE_BATCH_MAX = 256
TRAINING_SNAPSHOT_INTERVAL_SECONDS = 0.25
SAFE_STOP_RESERVE_STEPS = 5000
MIN_ACTIVE_DECISIONS = 64
PROMOTION_QUORUM_FRACTION = 0.75


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def discover_game_root(explicit: Path | None = None) -> Path:
    """Find DELTARUNE without assuming the default C: Steam library."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    env_root = os.environ.get("DELTARUNE_GAME_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(legacy.DEFAULT_GAME_ROOT)

    steam_root = (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Steam"
    )
    library_file = steam_root / "config" / "libraryfolders.vdf"
    if library_file.is_file():
        try:
            text = library_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for value in re.findall(r'"path"\s*"([^"]+)"', text):
            candidates.append(
                Path(value.replace("\\\\", "\\"))
                / "steamapps"
                / "common"
                / "DELTARUNE"
            )

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if (resolved / "DELTARUNE.exe").is_file():
            return resolved
    raise FileNotFoundError(
        "DELTARUNE was not found in the selected/default Steam libraries. "
        "Pass --game-root or set DELTARUNE_GAME_ROOT to the installation folder."
    )


def validate_game_install(game_root: Path, chapter: int) -> tuple[Path, Path]:
    executable, chapter_directory = legacy.validate_game_install(game_root, chapter)
    data_file = chapter_directory / "data.win"
    required = (
        b"AI_MULTI_INSTANCE|1|",
        b"DRTEL|9|",
        b"AI_SPEED_MOD|1|",
        SAFE_SUPPORT_MARKER,
    )
    missing = [marker.decode("ascii") for marker in required if not legacy._file_contains(data_file, marker)]
    if missing:
        raise RuntimeError(
            f"Chapter {chapter} is missing the safe Population Training support markers: "
            f"{', '.join(missing)}. Update/re-import the current AI Support package before training."
        )
    return executable, chapter_directory


def _attr(candidate: legacy.IndependentCandidate, name: str, default: Any) -> Any:
    if not hasattr(candidate, name):
        setattr(candidate, name, default)
    return getattr(candidate, name)


def _event_is_active_decision(payload: Mapping[str, object]) -> bool:
    telemetry = payload.get("telemetry")
    if isinstance(telemetry, Mapping) and telemetry.get("player_controlled") is False:
        return False
    state = str(payload.get("state") or "").casefold()
    action = str(payload.get("action") or "").casefold()
    reason = str(payload.get("reason") or "").casefold()
    if state == "cutscene" or action == "wait":
        return False
    passive_markers = (
        "control locked",
        "transition control locked",
        "automatic sequence",
        "cutscene continuity",
        "wait for control",
    )
    return not any(marker in reason for marker in passive_markers)


def _event_safe_to_stop(payload: Mapping[str, object]) -> bool:
    if str(payload.get("state") or "").casefold() != "overworld":
        return False
    telemetry = payload.get("telemetry")
    return isinstance(telemetry, Mapping) and telemetry.get("player_controlled") is True


def _update_candidate_event(
    candidate: legacy.IndependentCandidate,
    payload: dict[str, object],
) -> None:
    try:
        step = int(payload.get("step") or 0)
    except (TypeError, ValueError):
        step = int(_attr(candidate, "loop_steps", 0))
    candidate.loop_steps = max(int(_attr(candidate, "loop_steps", 0)), step + 1)

    last_event_step = int(_attr(candidate, "_last_event_step", -1))
    new_step = step > last_event_step
    if new_step:
        candidate._last_event_step = step
        if _event_is_active_decision(payload):
            candidate.decisions += 1
            if isinstance(payload.get("telemetry"), Mapping):
                candidate.telemetry_decisions = int(
                    _attr(candidate, "telemetry_decisions", 0)
                ) + 1

    candidate.latest_action = str(payload.get("action") or candidate.latest_action)
    candidate.latest_reason = str(payload.get("reason") or candidate.latest_reason)
    candidate.latest_state = str(payload.get("state") or "")
    candidate.safe_to_stop = _event_safe_to_stop(payload)

    telemetry = payload.get("telemetry")
    if isinstance(telemetry, Mapping):
        room = str(telemetry.get("room_name") or telemetry.get("room_id") or "")
        if room and room.casefold() != "unknown":
            candidate.latest_room = room
            candidate.rooms.add(room)

    room_discoveries = max(0, len(candidate.rooms) - 1)
    candidate.total_points = room_discoveries * 15.0 - candidate.decisions * 0.05
    candidate.normalized_score = 100.0 * candidate.total_points / (
        candidate.decisions + 64
    )


def _candidate_live(candidate: legacy.IndependentCandidate) -> dict[str, object]:
    telemetry_decisions = int(_attr(candidate, "telemetry_decisions", 0))
    coverage = telemetry_decisions / max(1, candidate.decisions)
    candidate.telemetry_coverage = min(1.0, coverage)
    return {
        "id": candidate.candidate_id,
        "label": candidate.label,
        "process_id": candidate.game_process.pid if candidate.game_process else None,
        "window_title": candidate.window.title if candidate.window else "",
        "telemetry_port": candidate.port,
        "save_id": candidate.save_id,
        "status": candidate.status,
        "segments_completed": 1 if candidate.exit_code is not None else 0,
        "active_decisions": candidate.decisions,
        "loop_steps": int(_attr(candidate, "loop_steps", 0)),
        "telemetry_decisions": telemetry_decisions,
        "total_points": round(candidate.total_points, 4),
        "normalized_score": round(candidate.normalized_score, 4),
        "story_progress": candidate.story_progress,
        "safety_penalties": candidate.safety_penalties,
        "minimum_exposure_met": candidate.exit_code == 0 and candidate.decisions >= MIN_ACTIVE_DECISIONS,
        "disqualified": candidate.disqualified,
        "disqualification_reasons": list(candidate.disqualification_reasons),
        "telemetry_coverage": round(candidate.telemetry_coverage, 4),
        "invalid_packet_rate": round(candidate.invalid_packet_rate, 4),
        "speed_verification": candidate.speed_verification,
        "input_cleanup_succeeded": candidate.input_cleanup_succeeded,
        "doctor_critical_findings": candidate.doctor_critical_findings,
        "current_action": candidate.latest_action,
        "current_reason": candidate.latest_reason,
        "current_room": candidate.latest_room,
        "current_state": str(_attr(candidate, "latest_state", "")),
        "safe_to_stop": bool(_attr(candidate, "safe_to_stop", False)),
    }


def _candidate_snapshot(
    workspace: legacy.MultiInstanceWorkspace,
    *,
    eligible: bool = False,
    winner: legacy.IndependentCandidate | None = None,
    explanation: str = "",
) -> dict[str, object]:
    ranked = sorted(
        workspace.candidates,
        key=lambda candidate: (
            candidate.disqualified,
            candidate.exit_code not in {None, 0},
            candidate.decisions < MIN_ACTIVE_DECISIONS,
            -candidate.normalized_score,
            -candidate.story_progress,
            candidate.safety_penalties,
            candidate.candidate_id,
        ),
    )
    recommendations = {
        candidate.candidate_id: (
            [{
                "id": candidate.latest_action,
                "kind": "independent action",
                "score": candidate.normalized_score,
                "reason": candidate.latest_reason,
            }]
            if candidate.latest_action
            else []
        )
        for candidate in workspace.candidates
    }
    active_states = [
        candidate.controller_process is not None
        and candidate.controller_process.poll() is None
        for candidate in workspace.candidates
    ]
    return {
        "schema_version": MULTI_INSTANCE_SCHEMA_VERSION,
        "architecture": MULTI_INSTANCE_ARCHITECTURE,
        "session_id": workspace.session_id,
        "population_size": len(workspace.candidates),
        "active_candidate": "",
        "all_instances_active": bool(active_states) and all(active_states),
        "eligible_for_promotion": eligible,
        "recommended_winner": winner.candidate_id if winner else None,
        "winner_explanation": explanation,
        "candidates": [_candidate_live(candidate) for candidate in ranked],
        "recommendations": recommendations,
        "shadow_rankings": recommendations,
    }


def _worker_arguments(candidate: legacy.IndependentCandidate, args: Any) -> list[str]:
    arguments = legacy._worker_arguments(candidate, args)
    steps_index = arguments.index("--steps") + 1
    arguments[steps_index] = str(int(args.steps) + SAFE_STOP_RESERVE_STEPS)
    # Common-random-number design: compare strategy genomes under the same base
    # random seed instead of confounding genome quality with a different seed.
    seed_index = arguments.index("--seed") + 1
    arguments[seed_index] = str(int(getattr(args, "seed", 0)))
    return arguments


def _summary_score(
    summary: Mapping[str, object],
    decisions: int,
    *,
    room_discoveries: int,
) -> tuple[float, float]:
    def number(*keys: str) -> float:
        for key in keys:
            if key not in summary:
                continue
            try:
                return float(summary.get(key) or 0)
            except (TypeError, ValueError):
                continue
        return 0.0

    failed_choices = number(
        "failed_choice_responses",
        "choice_failures",
        "failed_choices",
    )
    points = (
        50.0 * number("story_progress_events")
        + 15.0 * max(0, int(room_discoveries))
        + 10.0 * number("successful_choice_patterns")
        + 3.0 * number("new_interactables_this_run")
        + min(10.0, 0.25 * number("new_open_edges_this_run"))
        - 5.0 * number("flavor_interactions")
        - 8.0 * failed_choices
        - 15.0 * number("rapid_room_returns")
        - 10.0 * number("oscillation_breaks")
        - 4.0 * number("coherence_goal_failures")
        - 2.0 * number("broad_recovery_resets")
        - 0.05 * decisions
    )
    return points, 100.0 * points / (decisions + 64)


def _disqualify(candidate: legacy.IndependentCandidate, reason: str) -> None:
    legacy._disqualify(candidate, reason)


def _validate_candidate_run(
    candidate: legacy.IndependentCandidate,
    summary: Mapping[str, object],
) -> None:
    def count(key: str) -> int:
        value = summary.get(key)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    telemetry = summary.get("telemetry_diagnostics")
    telemetry = telemetry if isinstance(telemetry, Mapping) else {}
    valid = int(telemetry.get("valid_packets") or 0)
    invalid = int(telemetry.get("invalid_packets") or 0)
    telemetry_decisions = int(_attr(candidate, "telemetry_decisions", 0))
    candidate.telemetry_coverage = min(
        1.0,
        telemetry_decisions / max(1, candidate.decisions),
    )
    candidate.invalid_packet_rate = invalid / max(1, valid + invalid)
    if candidate.telemetry_coverage < 0.90:
        _disqualify(candidate, "telemetry covered less than 90% of active decisions")
    if candidate.invalid_packet_rate >= 0.05:
        _disqualify(candidate, "invalid telemetry packet rate was at least 5%")

    speed = summary.get("speed_synchronization")
    speed = speed if isinstance(speed, Mapping) else {}
    candidate.speed_verification = str(speed.get("verification_state") or "missing")
    requested = str(speed.get("requested") or "")
    speed_ok = candidate.speed_verification == "matched" or (
        requested in {"1", "1.0"}
        and candidate.speed_verification == "not_required"
    )
    if not speed_ok:
        _disqualify(candidate, "game and AI speed were not verified as synchronized")

    candidate.input_cleanup_succeeded = bool(summary.get("input_cleanup_succeeded", False))
    if not candidate.input_cleanup_succeeded:
        _disqualify(candidate, "input cleanup did not succeed")

    loop_escapes = count("oscillation_breaks")
    room_bounces = max(count("rapid_room_returns"), count("session_room_link_bounces"))
    room_transitions = max(count("transitions"), count("warp_crossings_this_run"))
    uncertainty_overruns = count("uncertainty_budget_overruns")
    candidate.safety_penalties = loop_escapes + room_bounces + uncertainty_overruns
    if uncertainty_overruns:
        _disqualify(candidate, "uncertainty budget overrun")
    if loop_escapes >= 8:
        _disqualify(candidate, "eight or more forced navigation-loop escapes")
    if room_bounces >= 4 and room_bounces / max(1, room_transitions) >= 2 / 3:
        _disqualify(candidate, "room-bounce rate was at least two-thirds")

    run_manifest = candidate.summary_path.parent / "run.json" if candidate.summary_path else None
    try:
        run_record = json.loads(run_manifest.read_text(encoding="utf-8")) if run_manifest else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        run_record = {}
    stop_reason = str(run_record.get("stop_reason") or "") if isinstance(run_record, Mapping) else ""
    if stop_reason not in {"step_limit", "gui_stop"}:
        _disqualify(candidate, "run did not end through a recognized clean stop")
    if stop_reason == "gui_stop" and not bool(_attr(candidate, "safe_stop_sent", False)):
        _disqualify(candidate, "GUI stop was not issued from safe overworld control")
    if stop_reason == "step_limit" and not bool(_attr(candidate, "safe_to_stop", False)):
        _disqualify(candidate, "worker exhausted its reserve before safe overworld control returned")

    doctor = candidate.summary_path.parent / "run_doctor.json" if candidate.summary_path else None
    if doctor is None or not doctor.is_file():
        _disqualify(candidate, "Run Doctor report missing")
        return
    try:
        report = json.loads(doctor.read_text(encoding="utf-8"))
        severity = report.get("severity_counts") if isinstance(report, Mapping) else None
        candidate.doctor_critical_findings = (
            int(severity.get("critical") or 0) if isinstance(severity, Mapping) else 1
        )
        if candidate.doctor_critical_findings:
            _disqualify(
                candidate,
                f"Run Doctor reported {candidate.doctor_critical_findings} critical finding(s)",
            )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _disqualify(candidate, "Run Doctor report unreadable")


def _finalize_candidates(
    workspace: legacy.MultiInstanceWorkspace,
) -> tuple[bool, legacy.IndependentCandidate | None, str]:
    for candidate in workspace.candidates:
        candidate.exit_code = (
            candidate.controller_process.returncode
            if candidate.controller_process is not None
            else -1
        )
        candidate.summary_path = legacy._find_summary(candidate)
        if candidate.exit_code != 0:
            _disqualify(candidate, f"controller exited with code {candidate.exit_code}")
        if candidate.summary_path is None:
            _disqualify(candidate, "run summary missing")
            continue
        try:
            summary = json.loads(candidate.summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            _disqualify(candidate, "run summary unreadable")
            continue
        if not isinstance(summary, Mapping):
            _disqualify(candidate, "run summary malformed")
            continue
        candidate.story_progress = int(summary.get("story_progress_events") or 0)
        candidate.total_points, candidate.normalized_score = _summary_score(
            summary,
            candidate.decisions,
            room_discoveries=max(0, len(candidate.rooms) - 1),
        )
        _validate_candidate_run(candidate, summary)
        candidate.status = "completed" if not candidate.disqualified else "disqualified"

    eligible_candidates = [
        candidate
        for candidate in workspace.candidates
        if not candidate.disqualified
        and candidate.exit_code == 0
        and candidate.decisions >= MIN_ACTIVE_DECISIONS
    ]
    required = max(
        2,
        math.ceil(len(workspace.candidates) * PROMOTION_QUORUM_FRACTION),
    )
    quorum_met = len(eligible_candidates) >= required
    winner = (
        sorted(
            eligible_candidates,
            key=lambda candidate: (
                -candidate.normalized_score,
                -candidate.story_progress,
                candidate.safety_penalties,
                candidate.candidate_id,
            ),
        )[0]
        if quorum_met
        else None
    )
    if winner is None:
        return (
            False,
            None,
            f"No winner can be recommended: {len(eligible_candidates)}/{required} "
            "required clean, sufficiently exposed independent AIs passed all gates.",
        )
    return (
        True,
        winner,
        f"{winner.label} achieved the best independent normalized score "
        f"({winner.normalized_score:.3f}) among {len(eligible_candidates)} clean "
        f"candidates; promotion quorum was {required}/{len(workspace.candidates)}.",
    )


def _emit_worker_event(
    candidate: legacy.IndependentCandidate,
    payload: dict[str, object],
) -> None:
    payload["instance"] = {
        "id": candidate.candidate_id,
        "label": candidate.label,
        "process_id": candidate.game_process.pid if candidate.game_process else None,
        "port": candidate.port,
        "save_id": candidate.save_id,
    }
    # Do not attach an O(population) snapshot to every O(population) worker
    # event. Population status is emitted separately at a bounded cadence.
    payload.pop("training", None)
    legacy._emit(payload)


def _request_safe_stops(
    workspace: legacy.MultiInstanceWorkspace,
    *,
    gui_stop: bool,
    target_steps: int,
) -> None:
    for candidate in workspace.candidates:
        process = candidate.controller_process
        if process is None or process.poll() is not None:
            continue
        reached_limit = int(_attr(candidate, "loop_steps", 0)) >= target_steps
        if not gui_stop and not reached_limit:
            continue
        candidate.stop_pending_reason = "gui_stop" if gui_stop else "step_limit"
        if bool(_attr(candidate, "safe_stop_sent", False)):
            continue
        if not bool(_attr(candidate, "safe_to_stop", False)):
            continue
        candidate.stop_file.write_text("stop\n", encoding="utf-8")
        candidate.safe_stop_sent = True
        candidate.status = "stopping safely"


def run_multi_instance_training(args: Any) -> Path:
    """Launch/supervise independent games with v2.1 safety semantics."""

    if not bool(getattr(args, "live", False)):
        raise ValueError("Independent population training requires --live input.")
    if bool(getattr(args, "no_telemetry", False)):
        raise ValueError("Independent population training requires telemetry.")

    population_size = legacy.validate_population_size(
        getattr(args, "population_size", 4)
    )
    ports = legacy.allocate_ports(
        getattr(args, "training_port_base", 42100),
        population_size,
    )
    explicit_root = getattr(args, "game_root", None)
    game_root = discover_game_root(Path(explicit_root) if explicit_root else None)
    chapter = int(getattr(args, "chapter", 1))
    executable, chapter_directory = validate_game_install(game_root, chapter)
    source_memory = Path(
        getattr(args, "memory", Path("memory/navigation.json"))
    ).parent
    workspace = legacy.MultiInstanceWorkspace.create(
        Path(getattr(args, "runs_root", Path("runs"))),
        source_memory,
        population_size=population_size,
        chapter=chapter,
        ports=ports,
    )
    # Stamp the upgraded schema/measurement contract into the top-level record.
    workspace.update_manifest(
        schema_version=MULTI_INSTANCE_SCHEMA_VERSION,
        architecture=MULTI_INSTANCE_ARCHITECTURE,
        measurement_version="2.1",
        game_root=str(game_root),
    )

    messages: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=MESSAGE_QUEUE_MAX)
    readers: list[threading.Thread] = []
    stop_requested = False
    target_steps = int(args.steps)
    last_snapshot_at = 0.0
    original_worker_arguments = legacy._worker_arguments
    legacy._worker_arguments = _worker_arguments
    try:
        legacy._launch_instances(workspace, args, executable, chapter_directory)
        for candidate in workspace.candidates:
            candidate.loop_steps = 0
            candidate.telemetry_decisions = 0
            candidate.safe_to_stop = False
            candidate.safe_stop_sent = False
            candidate._last_event_step = -1
            assert candidate.controller_process is not None
            assert candidate.controller_process.stdout is not None
            reader = threading.Thread(
                target=legacy._reader_thread,
                args=(
                    candidate.candidate_id,
                    candidate.controller_process.stdout,
                    messages,
                ),
                daemon=True,
            )
            reader.start()
            readers.append(reader)

        workspace.update_manifest(status="running")
        legacy._emit(
            {
                "kind": "runtime_status",
                "status": "running",
                "message": (
                    f"{population_size} independent Deltarune instances are running "
                    "with Population Training v2.1 safeguards."
                ),
                "training": _candidate_snapshot(workspace),
            }
        )
        by_id = {
            candidate.candidate_id: candidate for candidate in workspace.candidates
        }

        while True:
            outer_stop = getattr(args, "stop_file", None)
            if outer_stop is not None and Path(outer_stop).exists():
                stop_requested = True

            batch: list[tuple[str, str]] = []
            try:
                batch.append(messages.get(timeout=0.05))
            except queue.Empty:
                pass
            while len(batch) < MESSAGE_BATCH_MAX:
                try:
                    batch.append(messages.get_nowait())
                except queue.Empty:
                    break

            for candidate_id, line in batch:
                if not line:
                    continue
                candidate = by_id[candidate_id]
                if line.startswith(legacy.EVENT_PREFIX):
                    try:
                        payload = json.loads(line[len(legacy.EVENT_PREFIX) :])
                    except json.JSONDecodeError:
                        print(
                            f"[{candidate.label}] malformed event: {line}",
                            flush=True,
                        )
                        continue
                    if isinstance(payload, dict):
                        _update_candidate_event(candidate, payload)
                        _emit_worker_event(candidate, payload)
                else:
                    print(f"[{candidate.label}] {line}", flush=True)

            _request_safe_stops(
                workspace,
                gui_stop=stop_requested,
                target_steps=target_steps,
            )

            now = time.monotonic()
            if now - last_snapshot_at >= TRAINING_SNAPSHOT_INTERVAL_SECONDS:
                last_snapshot_at = now
                legacy._emit(
                    {
                        "kind": "training_status",
                        "training": _candidate_snapshot(workspace),
                    }
                )

            controllers_done = all(
                candidate.controller_process is not None
                and candidate.controller_process.poll() is not None
                for candidate in workspace.candidates
            )
            if (
                controllers_done
                and messages.empty()
                and all(not reader.is_alive() for reader in readers)
            ):
                break

        for reader in readers:
            reader.join(timeout=1.0)

        eligible, winner, explanation = _finalize_candidates(workspace)
        snapshot = _candidate_snapshot(
            workspace,
            eligible=eligible,
            winner=winner,
            explanation=explanation,
        )
        legacy._write_json(
            workspace.run_directory / "training_scores.json",
            snapshot,
        )
        workspace.update_manifest(
            status="review_ready" if eligible else "ineligible",
            ended_at=_utc_now(),
            stop_reason="gui_stop" if stop_requested else "step_limit",
            eligibility=snapshot,
        )
        legacy._emit({"kind": "training_complete", "training": snapshot})
        return workspace.run_directory
    finally:
        legacy._worker_arguments = original_worker_arguments
        legacy._shutdown_controllers(workspace)
        legacy._shutdown_games(workspace)
        if getattr(args, "stop_file", None) is not None:
            Path(args.stop_file).unlink(missing_ok=True)


def promote_multi_instance_training_run(
    run_directory: Path,
    profile_memory: Path,
) -> dict[str, object]:
    """Promote a winner while keeping training-only window metadata out."""

    audit = legacy.promote_multi_instance_training_run(
        run_directory,
        profile_memory,
    )
    profile_memory = Path(profile_memory).resolve()
    backup = Path(str(audit.get("backup_directory") or ""))
    if not backup.is_dir():
        raise OSError("Promotion backup is missing; cannot sanitize runtime metadata safely.")

    try:
        source_window_memory = backup / "window_titles.json"
        destination = profile_memory / "window_titles.json"
        if source_window_memory.is_file():
            shutil.copy2(source_window_memory, destination)
        else:
            destination.unlink(missing_ok=True)
        if not memory_inventory(profile_memory):
            raise OSError("Promotion metadata sanitization produced an empty profile.")
    except BaseException:
        failed = profile_memory.parent / f".{profile_memory.name}.training-metadata-failed"
        if failed.exists():
            shutil.rmtree(failed, ignore_errors=True)
        os.replace(profile_memory, failed)
        os.replace(backup, profile_memory)
        raise

    audit["runtime_metadata_sanitized"] = ["window_titles.json"]
    legacy._write_json(Path(run_directory) / "promotion.json", audit)
    manifest_path = Path(run_directory) / "training_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["promotion"] = audit
    legacy._write_json(manifest_path, manifest)
    return audit


def install_promotion_patch() -> None:
    """Route legacy GUI promotion imports through the v2.1 sanitizer."""

    legacy.promote_multi_instance_training_run = promote_multi_instance_training_run


__all__ = [
    "MIN_ACTIVE_DECISIONS",
    "MULTI_INSTANCE_ARCHITECTURE",
    "PROMOTION_QUORUM_FRACTION",
    "SAFE_SUPPORT_MARKER",
    "discover_game_root",
    "install_promotion_patch",
    "promote_multi_instance_training_run",
    "run_multi_instance_training",
    "validate_game_install",
]
