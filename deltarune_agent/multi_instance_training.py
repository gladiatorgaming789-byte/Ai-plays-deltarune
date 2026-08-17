"""Independent-process population training for Deltarune.

Each candidate owns a game process, UDP port, save directory, controller
process, learned memory, and run artifacts. No candidate can send input to or
learn from another candidate's game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, TextIO
from uuid import uuid4

from .strategy import StrategyGenome, population_genomes, validate_population_size
from .training_workspace import memory_inventory
from .window import (
    WindowInfo,
    close_window,
    post_window_key,
    tile_windows,
    wait_for_process_window,
)


MULTI_INSTANCE_SCHEMA_VERSION = 1
MULTI_INSTANCE_ARCHITECTURE = "independent_game_processes_v1"
EVENT_PREFIX = "AI_GUI_EVENT\t"
DEFAULT_GAME_ROOT = (
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Steam"
    / "steamapps"
    / "common"
    / "DELTARUNE"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _copy_memory(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    if not source.is_dir():
        return
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        elif child.is_file():
            shutil.copy2(child, target)


def seed_isolated_save(source: Path, save_id: str) -> Path:
    """Copy current Deltarune saves without modifying their originals."""

    source = Path(source)
    destination = source / "ai_training" / save_id
    if destination.exists():
        raise FileExistsError(f"Isolated Deltarune save already exists: {destination}")
    destination.mkdir(parents=True)
    if not source.is_dir():
        return destination
    for child in source.iterdir():
        if child.name.casefold() == "ai_training" or not child.is_file():
            continue
        shutil.copy2(child, destination / child.name)
    return destination


def _file_contains(path: Path, marker: bytes) -> bool:
    overlap = max(0, len(marker) - 1)
    previous = b""
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            combined = previous + block
            if marker in combined:
                return True
            previous = combined[-overlap:] if overlap else b""
    return False


def validate_game_install(game_root: Path, chapter: int) -> tuple[Path, Path]:
    game_root = Path(game_root).expanduser().resolve()
    executable = game_root / "DELTARUNE.exe"
    chapter_directory = game_root / f"chapter{int(chapter)}_windows"
    data_file = chapter_directory / "data.win"
    if not executable.is_file():
        raise FileNotFoundError(f"Deltarune executable was not found: {executable}")
    if not data_file.is_file():
        raise FileNotFoundError(f"Chapter {chapter} data.win was not found: {data_file}")
    if not _file_contains(data_file, b"AI_MULTI_INSTANCE|1|"):
        raise RuntimeError(
            f"Chapter {chapter} does not have multi-instance AI Support installed. "
            "Install the current AI Support DeltaMod package before training."
        )
    return executable, chapter_directory


def allocate_ports(base: int, count: int) -> tuple[int, ...]:
    base = int(base)
    count = int(count)
    if not 1024 <= base <= 65535 or base + count - 1 > 65535:
        raise ValueError("training UDP port range must stay between 1024 and 65535")
    ports = tuple(range(base, base + count))
    probes: list[socket.socket] = []
    try:
        for port in ports:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.bind(("127.0.0.1", port))
            probes.append(probe)
    except OSError as exc:
        raise RuntimeError(f"Training telemetry port {port} is already in use.") from exc
    finally:
        for probe in probes:
            probe.close()
    return ports


@dataclass
class IndependentCandidate:
    candidate_id: str
    label: str
    genome: StrategyGenome
    index: int
    port: int
    save_id: str
    directory: Path
    memory: Path
    runs: Path
    stop_file: Path
    game_process: subprocess.Popen[Any] | None = None
    controller_process: subprocess.Popen[str] | None = None
    window: WindowInfo | None = None
    status: str = "prepared"
    latest_action: str = ""
    latest_reason: str = ""
    latest_room: str = ""
    decisions: int = 0
    rooms: set[str] = field(default_factory=set)
    total_points: float = 0.0
    normalized_score: float = 0.0
    story_progress: int = 0
    safety_penalties: int = 0
    disqualified: bool = False
    disqualification_reasons: list[str] = field(default_factory=list)
    telemetry_coverage: float = 0.0
    invalid_packet_rate: float = 1.0
    speed_verification: str = "missing"
    input_cleanup_succeeded: bool = False
    doctor_critical_findings: int = 1
    exit_code: int | None = None
    summary_path: Path | None = None

    def live_candidate(self) -> dict[str, object]:
        return {
            "id": self.candidate_id,
            "label": self.label,
            "process_id": self.game_process.pid if self.game_process else None,
            "window_title": self.window.title if self.window else "",
            "telemetry_port": self.port,
            "save_id": self.save_id,
            "status": self.status,
            "segments_completed": 1 if self.exit_code is not None else 0,
            "active_decisions": self.decisions,
            "total_points": round(self.total_points, 4),
            "normalized_score": round(self.normalized_score, 4),
            "story_progress": self.story_progress,
            "safety_penalties": self.safety_penalties,
            "minimum_exposure_met": self.exit_code == 0 and self.decisions >= 64,
            "disqualified": self.disqualified,
            "disqualification_reasons": list(self.disqualification_reasons),
            "telemetry_coverage": round(self.telemetry_coverage, 4),
            "invalid_packet_rate": round(self.invalid_packet_rate, 4),
            "speed_verification": self.speed_verification,
            "input_cleanup_succeeded": self.input_cleanup_succeeded,
            "doctor_critical_findings": self.doctor_critical_findings,
            "current_action": self.latest_action,
            "current_reason": self.latest_reason,
            "current_room": self.latest_room,
        }


@dataclass
class MultiInstanceWorkspace:
    run_directory: Path
    source_memory: Path
    session_id: str
    chapter: int
    candidates: list[IndependentCandidate]
    baseline_inventory: dict[str, dict[str, object]]

    @classmethod
    def create(
        cls,
        runs_root: Path,
        source_memory: Path,
        *,
        population_size: int,
        chapter: int,
        ports: tuple[int, ...],
    ) -> "MultiInstanceWorkspace":
        population_size = validate_population_size(population_size)
        if len(ports) != population_size:
            raise ValueError("one telemetry port is required per AI")
        session_id = uuid4().hex
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_directory = Path(runs_root).resolve() / f"{stamp}-multi-{session_id[:8]}"
        run_directory.mkdir(parents=True, exist_ok=False)
        source_memory = Path(source_memory).resolve()
        baseline_inventory = memory_inventory(source_memory)
        baseline_genome, warning = StrategyGenome.load(source_memory / "strategy.json")
        candidates: list[IndependentCandidate] = []
        for index, (candidate_id, label, genome) in enumerate(
            population_genomes(baseline_genome, population_size)
        ):
            directory = run_directory / "instances" / candidate_id
            memory = directory / "memory"
            runs = directory / "runs"
            _copy_memory(source_memory, memory)
            genome.save(memory / "strategy.json")
            runs.mkdir(parents=True)
            save_id = f"{session_id[:8]}-{candidate_id}".replace("_", "-")
            candidates.append(
                IndependentCandidate(
                    candidate_id=candidate_id,
                    label=label,
                    genome=genome,
                    index=index,
                    port=ports[index],
                    save_id=save_id,
                    directory=directory,
                    memory=memory,
                    runs=runs,
                    stop_file=directory / "stop.flag",
                )
            )
        workspace = cls(
            run_directory=run_directory,
            source_memory=source_memory,
            session_id=session_id,
            chapter=int(chapter),
            candidates=candidates,
            baseline_inventory=baseline_inventory,
        )
        _write_json(
            run_directory / "baseline_fingerprints.json",
            {
                "schema_version": MULTI_INSTANCE_SCHEMA_VERSION,
                "source_memory": str(source_memory),
                "captured_at": _utc_now(),
                "inventory": baseline_inventory,
            },
        )
        _write_json(
            run_directory / "training_manifest.json",
            {
                "schema_version": MULTI_INSTANCE_SCHEMA_VERSION,
                "architecture": MULTI_INSTANCE_ARCHITECTURE,
                "session_id": session_id,
                "status": "preparing",
                "started_at": _utc_now(),
                "source_memory": str(source_memory),
                "chapter": int(chapter),
                "population_size": population_size,
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
                "strategy_warning": warning,
                "instances": [
                    {
                        "id": candidate.candidate_id,
                        "label": candidate.label,
                        "port": candidate.port,
                        "save_id": candidate.save_id,
                        "memory": str(candidate.memory),
                        "runs": str(candidate.runs),
                        "genome": candidate.genome.to_dict(),
                    }
                    for candidate in candidates
                ],
            },
        )
        return workspace

    def update_manifest(self, **updates: object) -> None:
        path = self.run_directory / "training_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(updates)
        _write_json(path, payload)


def _reader_thread(
    candidate_id: str,
    stream: TextIO,
    messages: "queue.Queue[tuple[str, str]]",
) -> None:
    try:
        for line in stream:
            messages.put((candidate_id, line.rstrip("\r\n")))
    finally:
        stream.close()


def _emit(payload: Mapping[str, object]) -> None:
    print(EVENT_PREFIX + json.dumps(payload, separators=(",", ":")), flush=True)


def _live_points(candidate: IndependentCandidate) -> None:
    candidate.total_points = len(candidate.rooms) * 15.0 - candidate.decisions * 0.05
    candidate.normalized_score = (
        100.0 * candidate.total_points / (candidate.decisions + 64)
    )


def _summary_score(summary: Mapping[str, object], decisions: int) -> tuple[float, float]:
    def number(key: str) -> float:
        try:
            return float(summary.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    points = (
        50.0 * number("story_progress_events")
        + 15.0 * number("room_discoveries")
        + 10.0 * number("successful_choice_patterns")
        + 3.0 * number("new_interactables_this_run")
        + min(10.0, 0.25 * number("new_open_edges_this_run"))
        - 5.0 * number("flavor_interactions")
        - 8.0 * number("failed_choice_responses")
        - 15.0 * number("rapid_room_returns")
        - 10.0 * number("oscillation_breaks")
        - 4.0 * number("coherence_goal_failures")
        - 2.0 * number("broad_recovery_resets")
        - 0.05 * decisions
    )
    return points, 100.0 * points / (decisions + 64)


def _candidate_snapshot(
    workspace: MultiInstanceWorkspace,
    *,
    eligible: bool = False,
    winner: IndependentCandidate | None = None,
    explanation: str = "",
) -> dict[str, object]:
    ranked = sorted(
        workspace.candidates,
        key=lambda candidate: (
            candidate.disqualified,
            candidate.exit_code != 0,
            candidate.decisions < 64,
            -candidate.normalized_score,
            -candidate.story_progress,
            candidate.safety_penalties,
            candidate.candidate_id,
        ),
    )
    recommendations = {
        candidate.candidate_id: (
            [
                {
                    "id": candidate.latest_action,
                    "kind": "independent action",
                    "score": candidate.normalized_score,
                    "reason": candidate.latest_reason,
                }
            ]
            if candidate.latest_action
            else []
        )
        for candidate in workspace.candidates
    }
    return {
        "schema_version": MULTI_INSTANCE_SCHEMA_VERSION,
        "architecture": MULTI_INSTANCE_ARCHITECTURE,
        "session_id": workspace.session_id,
        "population_size": len(workspace.candidates),
        "active_candidate": "",
        "all_instances_active": any(
            candidate.controller_process is not None
            and candidate.controller_process.poll() is None
            for candidate in workspace.candidates
        ),
        "eligible_for_promotion": eligible,
        "recommended_winner": winner.candidate_id if winner else None,
        "winner_explanation": explanation,
        "candidates": [candidate.live_candidate() for candidate in ranked],
        "recommendations": recommendations,
        # Retained so older GUI builds can still open these artifacts.
        "shadow_rankings": recommendations,
    }


def _handle_worker_event(
    workspace: MultiInstanceWorkspace,
    candidate: IndependentCandidate,
    payload: dict[str, object],
) -> None:
    try:
        candidate.decisions = max(candidate.decisions, int(payload.get("step") or 0) + 1)
    except (TypeError, ValueError):
        pass
    candidate.latest_action = str(payload.get("action") or candidate.latest_action)
    candidate.latest_reason = str(payload.get("reason") or candidate.latest_reason)
    telemetry = payload.get("telemetry")
    if isinstance(telemetry, Mapping):
        room = str(telemetry.get("room_name") or "")
        if room and room.casefold() != "unknown":
            candidate.latest_room = room
            candidate.rooms.add(room)
    _live_points(candidate)
    payload["instance"] = {
        "id": candidate.candidate_id,
        "label": candidate.label,
        "process_id": candidate.game_process.pid if candidate.game_process else None,
        "port": candidate.port,
        "save_id": candidate.save_id,
    }
    payload["training"] = _candidate_snapshot(workspace)
    _emit(payload)


def _worker_arguments(candidate: IndependentCandidate, args: Any) -> list[str]:
    arguments = [
        "-u",
        "-m",
        "deltarune_agent",
        "run",
        "--live",
        "--steps",
        str(int(args.steps)),
        "--game-pid",
        str(candidate.game_process.pid),
        "--background-input",
        "--countdown",
        "0",
        "--telemetry-port",
        str(candidate.port),
        "--memory",
        str(candidate.memory / "navigation.json"),
        "--visual-memory",
        str(candidate.memory / "visual_states.json"),
        "--window-memory",
        str(candidate.memory / "window_titles.json"),
        "--runs-root",
        str(candidate.runs),
        "--stop-file",
        str(candidate.stop_file),
        "--event-stream",
        "--speed",
        str(getattr(args, "speed", "auto")),
        "--seed",
        str(int(getattr(args, "seed", 0)) + candidate.index),
    ]
    interval = getattr(args, "interval", None)
    if interval is not None:
        arguments.extend(("--interval", str(float(interval))))
    return arguments


def _apply_requested_speed(window: WindowInfo, requested: object) -> None:
    text = str(requested or "auto").casefold().removesuffix("x")
    if text == "auto":
        return
    target = int(text)
    if not 1 <= target <= 10:
        raise ValueError("training speed must be auto or 1x-10x")
    for key in (["f9"] * 9 + ["f10"] * (target - 1)):
        post_window_key(window.hwnd, key, True)
        time.sleep(0.055)
        post_window_key(window.hwnd, key, False)
        time.sleep(0.070)


def _launch_instances(
    workspace: MultiInstanceWorkspace,
    args: Any,
    executable: Path,
    chapter_directory: Path,
) -> None:
    save_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "DELTARUNE"
    launched: list[IndependentCandidate] = []
    try:
        for candidate in workspace.candidates:
            seed_isolated_save(save_root, candidate.save_id)
            candidate.status = "launching game"
            candidate.game_process = subprocess.Popen(
                [
                    str(executable),
                    "launcher",
                    f"ai_instance_{candidate.save_id}",
                    f"ai_port_{candidate.port}",
                ],
                cwd=chapter_directory,
            )
            candidate.window = wait_for_process_window(candidate.game_process.pid, timeout=20.0)
            candidate.status = "game ready"
            launched.append(candidate)
        tile_windows([candidate.window for candidate in launched if candidate.window is not None])
        for candidate in launched:
            assert candidate.window is not None
            _apply_requested_speed(candidate.window, getattr(args, "speed", "auto"))
    except BaseException:
        for candidate in launched:
            if candidate.window is not None:
                try:
                    close_window(candidate.window.hwnd)
                except OSError:
                    pass
            if candidate.game_process is not None and candidate.game_process.poll() is None:
                candidate.game_process.terminate()
        raise

    for candidate in workspace.candidates:
        candidate.status = "running"
        candidate.controller_process = subprocess.Popen(
            [sys.executable, *_worker_arguments(candidate, args)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if candidate.controller_process.stdout is None:
            raise RuntimeError(f"{candidate.label} controller output pipe was not created")


def _disqualify(candidate: IndependentCandidate, reason: str) -> None:
    candidate.disqualified = True
    if reason not in candidate.disqualification_reasons:
        candidate.disqualification_reasons.append(reason)


def _validate_candidate_run(
    candidate: IndependentCandidate,
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
    candidate.telemetry_coverage = min(1.0, valid / max(1, candidate.decisions))
    candidate.invalid_packet_rate = invalid / max(1, valid + invalid)
    if candidate.telemetry_coverage < 0.90:
        _disqualify(candidate, "telemetry covered less than 90% of decisions")
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

    candidate.input_cleanup_succeeded = bool(
        summary.get("input_cleanup_succeeded", False)
    )
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
    if not isinstance(run_record, Mapping) or run_record.get("stop_reason") not in {
        "step_limit",
        "gui_stop",
    }:
        _disqualify(candidate, "run did not end through its limit or a safe GUI stop")

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


def _find_summary(candidate: IndependentCandidate) -> Path | None:
    paths = sorted(candidate.runs.glob("*/summary.json"), key=lambda path: path.stat().st_mtime_ns)
    return paths[-1] if paths else None


def _finalize_candidates(workspace: MultiInstanceWorkspace) -> tuple[bool, IndependentCandidate | None, str]:
    for candidate in workspace.candidates:
        candidate.exit_code = (
            candidate.controller_process.returncode
            if candidate.controller_process is not None
            else -1
        )
        candidate.summary_path = _find_summary(candidate)
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
        )
        _validate_candidate_run(candidate, summary)
        candidate.status = "completed" if not candidate.disqualified else "disqualified"

    eligible_candidates = [
        candidate
        for candidate in workspace.candidates
        if not candidate.disqualified
        and candidate.exit_code == 0
        and candidate.decisions >= 64
    ]
    all_exposed = len(eligible_candidates) == len(workspace.candidates)
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
        if all_exposed
        else None
    )
    if winner is None:
        return (
            False,
            None,
            "No winner can be recommended unless every independent AI completes "
            "at least 64 decisions without a critical safety failure.",
        )
    return (
        True,
        winner,
        f"{winner.label} achieved the best independent normalized score "
        f"({winner.normalized_score:.3f}) across {len(workspace.candidates)} "
        "separate Deltarune processes.",
    )


def _shutdown_games(workspace: MultiInstanceWorkspace) -> None:
    for candidate in workspace.candidates:
        if candidate.window is not None:
            try:
                close_window(candidate.window.hwnd)
            except OSError:
                pass
    deadline = time.monotonic() + 5.0
    for candidate in workspace.candidates:
        process = candidate.game_process
        if process is None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.terminate()


def _shutdown_controllers(workspace: MultiInstanceWorkspace) -> None:
    running = [
        candidate
        for candidate in workspace.candidates
        if candidate.controller_process is not None
        and candidate.controller_process.poll() is None
    ]
    for candidate in running:
        candidate.stop_file.write_text("stop\n", encoding="utf-8")
    deadline = time.monotonic() + 10.0
    for candidate in running:
        assert candidate.controller_process is not None
        try:
            candidate.controller_process.wait(
                timeout=max(0.0, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired:
            candidate.controller_process.terminate()


def run_multi_instance_training(args: Any) -> Path:
    """Launch and supervise one fully independent Deltarune per candidate."""

    if not bool(getattr(args, "live", False)):
        raise ValueError("Independent population training requires --live input.")
    if bool(getattr(args, "no_telemetry", False)):
        raise ValueError("Independent population training requires telemetry.")
    population_size = validate_population_size(getattr(args, "population_size", 4))
    ports = allocate_ports(getattr(args, "training_port_base", 42100), population_size)
    game_root = Path(getattr(args, "game_root", None) or DEFAULT_GAME_ROOT)
    chapter = int(getattr(args, "chapter", 1))
    executable, chapter_directory = validate_game_install(game_root, chapter)
    source_memory = Path(getattr(args, "memory", Path("memory/navigation.json"))).parent
    workspace = MultiInstanceWorkspace.create(
        Path(getattr(args, "runs_root", Path("runs"))),
        source_memory,
        population_size=population_size,
        chapter=chapter,
        ports=ports,
    )
    messages: "queue.Queue[tuple[str, str]]" = queue.Queue()
    readers: list[threading.Thread] = []
    stop_requested = False
    try:
        _launch_instances(workspace, args, executable, chapter_directory)
        for candidate in workspace.candidates:
            assert candidate.controller_process is not None
            assert candidate.controller_process.stdout is not None
            reader = threading.Thread(
                target=_reader_thread,
                args=(candidate.candidate_id, candidate.controller_process.stdout, messages),
                daemon=True,
            )
            reader.start()
            readers.append(reader)
        workspace.update_manifest(status="running")
        _emit(
            {
                "kind": "runtime_status",
                "status": "running",
                "message": f"{population_size} independent Deltarune instances are running.",
                "training": _candidate_snapshot(workspace),
            }
        )
        by_id = {candidate.candidate_id: candidate for candidate in workspace.candidates}
        while True:
            if getattr(args, "stop_file", None) is not None and Path(args.stop_file).exists():
                if not stop_requested:
                    stop_requested = True
                    for candidate in workspace.candidates:
                        candidate.stop_file.write_text("stop\n", encoding="utf-8")
            try:
                candidate_id, line = messages.get(timeout=0.05)
            except queue.Empty:
                candidate_id = ""
                line = ""
            if line:
                candidate = by_id[candidate_id]
                if line.startswith(EVENT_PREFIX):
                    try:
                        payload = json.loads(line[len(EVENT_PREFIX) :])
                    except json.JSONDecodeError:
                        print(f"[{candidate.label}] malformed event: {line}", flush=True)
                    else:
                        if isinstance(payload, dict):
                            _handle_worker_event(workspace, candidate, payload)
                else:
                    print(f"[{candidate.label}] {line}", flush=True)
            if all(
                candidate.controller_process is not None
                and candidate.controller_process.poll() is not None
                for candidate in workspace.candidates
            ) and messages.empty() and all(not reader.is_alive() for reader in readers):
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
        _write_json(workspace.run_directory / "training_scores.json", snapshot)
        workspace.update_manifest(
            status="review_ready" if eligible else "ineligible",
            ended_at=_utc_now(),
            stop_reason="gui_stop" if stop_requested else "step_limit",
            eligibility=snapshot,
        )
        _emit({"kind": "training_complete", "training": snapshot})
        return workspace.run_directory
    finally:
        _shutdown_controllers(workspace)
        _shutdown_games(workspace)
        if getattr(args, "stop_file", None) is not None:
            Path(args.stop_file).unlink(missing_ok=True)


def promote_multi_instance_training_run(
    run_directory: Path,
    profile_memory: Path,
) -> dict[str, object]:
    """Promote the complete learned memory of one independent winner."""

    run_directory = Path(run_directory).resolve()
    profile_memory = Path(profile_memory).resolve()
    manifest_path = run_directory / "training_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    eligibility = manifest.get("eligibility") if isinstance(manifest, Mapping) else None
    if (
        manifest.get("architecture") != MULTI_INSTANCE_ARCHITECTURE
        or not isinstance(eligibility, Mapping)
        or not eligibility.get("eligible_for_promotion")
    ):
        raise ValueError("This independent training run has no eligible winner.")
    winner = str(eligibility.get("recommended_winner") or "")
    baseline_payload = json.loads(
        (run_directory / "baseline_fingerprints.json").read_text(encoding="utf-8")
    )
    baseline = baseline_payload.get("inventory") if isinstance(baseline_payload, Mapping) else None
    if not isinstance(baseline, Mapping) or memory_inventory(profile_memory) != dict(baseline):
        raise RuntimeError(
            "Promotion refused because the active profile memory changed after training began."
        )
    winner_memory = run_directory / "instances" / winner / "memory"
    if not winner_memory.is_dir() or not (winner_memory / "strategy.json").is_file():
        raise FileNotFoundError(f"Winner memory is missing: {winner_memory}")

    parent = profile_memory.parent
    transaction_id = uuid4().hex
    staging = parent / f".{profile_memory.name}.{transaction_id}.promoting"
    backup = parent / ".training-backups" / f"{profile_memory.name}-{transaction_id}"
    shutil.copytree(winner_memory, staging)
    if not memory_inventory(staging):
        shutil.rmtree(staging, ignore_errors=True)
        raise OSError("Winner memory staging produced an empty inventory.")
    audit = {
        "schema_version": MULTI_INSTANCE_SCHEMA_VERSION,
        "architecture": MULTI_INSTANCE_ARCHITECTURE,
        "promoted_at": _utc_now(),
        "session_id": manifest.get("session_id"),
        "run_directory": str(run_directory),
        "winner": winner,
        "winner_explanation": eligibility.get("winner_explanation"),
        "transaction_id": transaction_id,
    }
    history_path = staging / "training_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except (OSError, UnicodeError, json.JSONDecodeError):
        history = []
    history.append(audit)
    _write_json(history_path, history)
    _write_json(staging / "promotion.json", audit)
    expected_inventory = memory_inventory(staging)
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(profile_memory, backup)
    try:
        os.replace(staging, profile_memory)
    except BaseException:
        os.replace(backup, profile_memory)
        raise
    if memory_inventory(profile_memory) != expected_inventory:
        failed = parent / f".{profile_memory.name}.{transaction_id}.failed"
        os.replace(profile_memory, failed)
        os.replace(backup, profile_memory)
        raise OSError("Promotion verification failed; rollback completed.")
    audit["backup_directory"] = str(backup)
    _write_json(run_directory / "promotion.json", audit)
    manifest["status"] = "promoted"
    manifest["promotion"] = audit
    _write_json(manifest_path, manifest)
    return audit


__all__ = [
    "MULTI_INSTANCE_ARCHITECTURE",
    "IndependentCandidate",
    "MultiInstanceWorkspace",
    "allocate_ports",
    "promote_multi_instance_training_run",
    "run_multi_instance_training",
    "seed_isolated_save",
    "validate_game_install",
]
