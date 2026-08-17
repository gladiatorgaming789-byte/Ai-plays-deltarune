from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
from typing import Callable

from .controller import KeyboardController
from .hierarchical_policy import HierarchicalPolicy
from .observer import ScreenObserver
from .perception import (
    CutsceneTracker,
    GameState,
    Perception,
    VisualFeatures,
    VisualStateDetector,
)
from .progress import EpisodeTracker
from .run_artifacts import write_json
from .speed import SpeedSynchronizer
from .strategy import DEFAULT_POPULATION_SIZE, validate_population_size
from .telemetry import TelemetryReceiver, fuse_perception
from .training_workspace import TrainingWorkspace
from .window import (
    client_region,
    find_window,
    focus_window,
    is_window_foreground,
    remember_window,
    wait_for_process_window,
)


def _runtime_status(
    event_stream: bool,
    status: str,
    message: str,
) -> None:
    if event_stream:
        print(
            "AI_GUI_EVENT\t"
            + json.dumps(
                {
                    "kind": "runtime_status",
                    "status": status,
                    "message": message,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
    else:
        print(message, flush=True)


def _attempt_cleanup(
    errors: list[tuple[str, BaseException]],
    label: str,
    operation: Callable[[], object],
) -> object | None:
    try:
        return operation()
    except BaseException as exc:
        errors.append((label, exc))
        return None


def _cleanup_message(errors: list[tuple[str, BaseException]]) -> str:
    return "; ".join(
        f"{label}: {type(exc).__name__}: {exc}" for label, exc in errors
    )


def run(args: argparse.Namespace) -> Path:
    if (
        args.steps < 1
        or (args.interval is not None and args.interval < 0)
        or args.countdown < 0
    ):
        raise ValueError(
            "steps must be positive; interval and countdown must be non-negative"
        )
    training_enabled = bool(getattr(args, "training", False))
    population_size = validate_population_size(
        getattr(args, "population_size", DEFAULT_POPULATION_SIZE)
    )
    if training_enabled and not args.live:
        raise ValueError("Population training requires --live input.")
    if training_enabled and args.no_telemetry:
        raise ValueError("Population training requires telemetry.")

    observer: ScreenObserver | None = None
    detector: VisualStateDetector | None = None
    cutscene_tracker: CutsceneTracker | None = None
    telemetry_receiver: TelemetryReceiver | None = None
    policy: HierarchicalPolicy | None = None
    controller: KeyboardController | None = None
    tracker: EpisodeTracker | None = None
    speed_sync: SpeedSynchronizer | None = None
    training_workspace: TrainingWorkspace | None = None
    training_coordinator = None
    effective_memory = Path(args.memory)
    effective_visual_memory = Path(args.visual_memory)
    effective_window_memory = Path(args.window_memory)
    loop_timings: list[float] = []
    primary_error: BaseException | None = None
    stop_reason = "step_limit"
    stop_detail: str | None = None
    input_cleanup_succeeded = False

    try:
        if training_enabled:
            effective_config = dict(vars(args))
            effective_config["training"] = True
            effective_config["population_size"] = population_size
            tracker = EpisodeTracker(
                root=Path(getattr(args, "runs_root", Path("runs"))),
                config=effective_config,
            )
            training_workspace = TrainingWorkspace.create(
                tracker.directory,
                Path(args.memory).parent,
                population_size=population_size,
            )
            effective_memory = training_workspace.navigation_path
            effective_visual_memory = training_workspace.visual_memory_path
            effective_window_memory = training_workspace.window_memory_path
            training_coordinator = training_workspace.coordinator()
        observer = ScreenObserver(tuple(args.region) if args.region else None)
        detector = VisualStateDetector(effective_visual_memory)
        cutscene_tracker = CutsceneTracker()
        telemetry_receiver = (
            None if args.no_telemetry else TelemetryReceiver(args.telemetry_port)
        )
        policy = HierarchicalPolicy(
            args.seed,
            effective_memory,
            training=training_coordinator,
        )
        controller = KeyboardController(args.live)
        speed_sync = SpeedSynchronizer(getattr(args, "speed", "auto"))
        effective_config = dict(vars(args))
        effective_config["speed"] = speed_sync.requested
        if tracker is None:
            tracker = EpisodeTracker(
                root=Path(getattr(args, "runs_root", Path("runs"))),
                config=effective_config,
            )

        if policy.memory_warning:
            print(f"Memory warning: {policy.memory_warning}")
        if getattr(policy, "strategy_warning", None):
            print(f"Strategy warning: {policy.strategy_warning}")
        if detector.memory_warning:
            print(f"Visual memory warning: {detector.memory_warning}")
        print(
            f"Mode: {'LIVE' if args.live else 'DRY RUN'} | "
            f"output: {tracker.directory}"
        )
        print("Emergency stop: move mouse to upper-left corner or press Ctrl+C.")

        window = None
        if args.live:
            game_pid = getattr(args, "game_pid", None)
            window = (
                wait_for_process_window(game_pid)
                if game_pid is not None
                else focus_window(args.game_window, effective_window_memory)
            )
            remember_window(effective_window_memory, window)
            print(
                f"Selected game window: {window.title} ({window.executable}, "
                f"PID {getattr(window, 'process_id', 0) or game_pid or 'unknown'})"
            )
            for remaining in range(args.countdown, 0, -1):
                print(f"Starting controls in {remaining}...")
                time.sleep(1)
        elif not args.region:
            window = find_window(args.game_window, effective_window_memory)
            if window is not None:
                remember_window(effective_window_memory, window)

        if window and not args.region:
            observer.set_window(window.hwnd, client_region(window.hwnd))
            print(f"Capturing game area: {observer.region}")
        if window is not None:
            controller.set_target_window(window.hwnd)
            if getattr(args, "background_input", False) or getattr(args, "game_pid", None):
                controller.set_background_input(True)

        telemetry_seen = False
        background_input = False
        previous_loop_seconds: float | None = None
        stop_deferred = False
        for step in range(args.steps):
            loop_started = time.monotonic()
            if args.stop_file is not None and args.stop_file.exists():
                if training_enabled and not policy.training_safe_to_stop():
                    if not stop_deferred:
                        print(
                            "Stop requested; finishing the active training consequence before stopping.",
                            flush=True,
                        )
                        stop_deferred = True
                else:
                    stop_reason = "gui_stop"
                    print(
                        "Stop requested by GUI; ending the run safely.",
                        flush=True,
                    )
                    break

            if args.live:
                use_background_input = bool(
                    getattr(args, "background_input", False)
                    or getattr(args, "game_pid", None)
                    or not is_window_foreground(window)
                )
                if use_background_input != background_input:
                    controller.set_background_input(use_background_input)
                    background_input = use_background_input
                    if background_input:
                        _runtime_status(
                            args.event_stream,
                            "background",
                            "Deltarune lost focus; continuing with input "
                            "targeted only to its window.",
                        )
                    else:
                        _runtime_status(
                            args.event_stream,
                            "running",
                            "Deltarune is focused again; using normal "
                            "foreground input.",
                        )

            observation = observer.observe(step)
            telemetry = telemetry_receiver.poll() if telemetry_receiver else None
            if telemetry_receiver is not None:
                speed_sync.update(getattr(telemetry_receiver, "latest_speed", None))
            controller.set_speed_multiplier(speed_sync.effective_multiplier())
            should_check_stale = (
                step >= 15
                or (
                    speed_sync.sample is not None
                    and speed_sync.detected_multiplier() is None
                )
            )
            speed_warning = speed_sync.stale_warning() if should_check_stale else None
            if speed_warning:
                _runtime_status(
                    args.event_stream,
                    "speed_fallback",
                    speed_warning,
                )
            if telemetry_receiver is not None:
                policy.observe_room_trace(
                    telemetry_receiver.drain_overworld_trace()
                )
            if telemetry is not None:
                telemetry_seen = True
            elif telemetry_receiver is not None and step == 15 and not telemetry_seen:
                if training_enabled:
                    raise RuntimeError(
                        "Population training requires live telemetry, but no valid packets were received."
                    )
                print(
                    "Telemetry warning: no packets received; continuing "
                    "with the learned visual model."
                )

            observation = policy.validate_observation(observation, telemetry)
            if observation.visual_valid:
                visual = detector.classify(observation.frame)
            else:
                visual = Perception(
                    GameState.UNKNOWN,
                    0.0,
                    VisualFeatures(0.0, 0.0, 0.0, 0.0, 0.0),
                    "stale-capture",
                )

            perception = cutscene_tracker.update(
                fuse_perception(visual, telemetry),
                telemetry,
                observation.visual_valid,
            )
            action = policy.choose(observation, perception, telemetry)
            observation = replace(
                observation,
                visual_valid=policy.last_visual_valid,
            )

            if telemetry is not None and observation.visual_valid:
                detector.learn_from_telemetry(observation.frame, telemetry.mode)

            cutscene_tracker.note_action(action.name, policy.reason)
            map_updates = policy.drain_map_updates()
            if training_enabled:
                policy.observe_training_step(
                    step=step,
                    perception=perception,
                    telemetry=telemetry,
                    map_updates=map_updates,
                )
            decision_context = policy.decision_context()
            prediction_snapshot = policy.prediction_snapshot()
            base_cooldown = (
                action.cooldown if args.interval is None else args.interval
            )
            speed_state = speed_sync.as_dict(
                action_duration=action.duration,
                cooldown=base_cooldown,
                loop_seconds=previous_loop_seconds,
            )
            recorded_context = dict(decision_context or {})
            recorded_context["speed"] = speed_state
            recorded_context["timing"] = {
                "base_action_duration_seconds": action.duration,
                "base_cooldown_seconds": base_cooldown,
                "effective_action_duration_seconds": speed_state[
                    "effective_action_duration_seconds"
                ],
                "effective_cooldown_seconds": speed_state[
                    "effective_cooldown_seconds"
                ],
                "previous_loop_seconds": previous_loop_seconds,
            }
            location = (
                f" room={telemetry.room_name or telemetry.room_id} "
                f"pos=({telemetry.x:.0f},{telemetry.y:.0f})"
                if telemetry
                else ""
            )
            if not args.event_stream:
                print(
                    f"{step:04d}: {perception.state.value:<9} "
                    f"{perception.confidence:.2f} "
                    f"[{perception.source}] -> {action.name}{location} "
                    f"| {policy.reason}",
                    flush=True,
                )

            tracker.record(
                observation,
                perception,
                telemetry,
                action,
                policy.reason,
                args.live,
                decision_context=recorded_context,
                map_updates=map_updates,
                prediction_snapshot=prediction_snapshot,
            )
            if args.event_stream:
                print(
                    "AI_GUI_EVENT\t"
                    + json.dumps(
                        {
                            "step": step,
                            "state": perception.state.value,
                            "confidence": perception.confidence,
                            "source": perception.source,
                            "action": action.name,
                            "reason": policy.reason,
                            "visual_valid": observation.visual_valid,
                            "telemetry": (
                                telemetry.as_dict() if telemetry else None
                            ),
                            "map_updates": map_updates,
                            "decision_context": recorded_context,
                            "speed": speed_state,
                            "training": prediction_snapshot.get("training"),
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

            controller.execute(action)
            if training_enabled:
                policy.commit_training_handoff()
            time.sleep(speed_sync.scale_delay(base_cooldown))
            previous_loop_seconds = max(0.0, time.monotonic() - loop_started)
            loop_timings.append(previous_loop_seconds)
        else:
            if training_enabled and not policy.training_safe_to_stop():
                stop_reason = "step_limit_unsafe"
                stop_detail = "Step limit was reached before safe overworld control returned."
    except KeyboardInterrupt as exc:
        primary_error = exc
        stop_reason = "keyboard_interrupt"
        stop_detail = "Run stopped by Ctrl+C."
        raise
    except BaseException as exc:
        primary_error = exc
        stop_reason = "error"
        stop_detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        cleanup_errors: list[tuple[str, BaseException]] = []
        telemetry_diagnostics: dict[str, object] = (
            {"disabled": True}
            if args.no_telemetry
            else {"unavailable": True}
        )

        if controller is not None:
            cleanup_count = len(cleanup_errors)
            _attempt_cleanup(cleanup_errors, "release keyboard input", controller.release_all)
            input_cleanup_succeeded = len(cleanup_errors) == cleanup_count

        if telemetry_receiver is not None:
            diagnostics = _attempt_cleanup(
                cleanup_errors,
                "collect telemetry diagnostics",
                telemetry_receiver.diagnostics,
            )
            if isinstance(diagnostics, dict):
                telemetry_diagnostics = diagnostics
            _attempt_cleanup(
                cleanup_errors,
                "close telemetry receiver",
                telemetry_receiver.close,
            )

        if policy is not None:
            _attempt_cleanup(cleanup_errors, "save navigation memory", policy.save_memory)
        if detector is not None:
            _attempt_cleanup(cleanup_errors, "save visual memory", detector.save_memory)

        policy_summary: dict[str, object] = {}
        if policy is not None:
            summary = _attempt_cleanup(
                cleanup_errors,
                "build policy summary",
                policy.summary,
            )
            if isinstance(summary, dict):
                policy_summary = summary
        policy_summary["telemetry_diagnostics"] = telemetry_diagnostics
        policy_summary["input_cleanup_succeeded"] = input_cleanup_succeeded
        if speed_sync is not None:
            policy_summary["speed_synchronization"] = speed_sync.as_dict()
            if loop_timings:
                policy_summary["loop_timing"] = {
                    "samples": len(loop_timings),
                    "minimum_seconds": min(loop_timings),
                    "maximum_seconds": max(loop_timings),
                    "average_seconds": sum(loop_timings) / len(loop_timings),
                }

        if tracker is not None:
            diagnostics_path = tracker.directory / "telemetry_diagnostics.json"
            _attempt_cleanup(
                cleanup_errors,
                "write telemetry diagnostics",
                lambda: write_json(diagnostics_path, telemetry_diagnostics),
            )
            extra_files = {
                name: path
                for name, path in {
                    "visual_states.json": effective_visual_memory,
                    "window_titles.json": effective_window_memory,
                }.items()
                if Path(path).is_file()
            }
            if diagnostics_path.is_file():
                extra_files["telemetry_diagnostics.json"] = diagnostics_path
            if speed_sync is not None:
                speed_path = tracker.directory / "speed_diagnostics.json"
                speed_diagnostics = speed_sync.as_dict()
                if loop_timings:
                    speed_diagnostics["loop_timing"] = {
                        "samples": len(loop_timings),
                        "minimum_seconds": min(loop_timings),
                        "maximum_seconds": max(loop_timings),
                        "average_seconds": sum(loop_timings) / len(loop_timings),
                    }
                _attempt_cleanup(
                    cleanup_errors,
                    "write speed diagnostics",
                    lambda: write_json(speed_path, speed_diagnostics),
                )
                if speed_path.is_file():
                    extra_files["speed_diagnostics.json"] = speed_path
            _attempt_cleanup(
                cleanup_errors,
                "finish run artifacts",
                lambda: tracker.finish(
                    policy_summary,
                    stop_reason=stop_reason,
                    stop_detail=stop_detail,
                    config={
                        **vars(args),
                        "speed": (
                            speed_sync.requested
                            if speed_sync is not None
                            else getattr(args, "speed", "auto")
                        ),
                    },
                    navigation_path=effective_memory,
                    room_views_path=effective_memory.parent / "room_views",
                    extra_files=extra_files,
                ),
            )
            if training_workspace is not None and training_coordinator is not None:
                doctor_payload: dict[str, object] | None = None
                doctor_path = tracker.directory / "run_doctor.json"
                if doctor_path.is_file():
                    try:
                        loaded_doctor = json.loads(doctor_path.read_text(encoding="utf-8"))
                        if isinstance(loaded_doctor, dict):
                            doctor_payload = loaded_doctor
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        doctor_payload = None
                speed_diagnostics = speed_sync.as_dict() if speed_sync is not None else {}
                _attempt_cleanup(
                    cleanup_errors,
                    "finalize population training",
                    lambda: training_workspace.finalize(
                        training_coordinator,
                        stop_reason=stop_reason,
                        telemetry_diagnostics=telemetry_diagnostics,
                        speed_diagnostics=speed_diagnostics,
                        input_cleanup_succeeded=input_cleanup_succeeded,
                        doctor_payload=doctor_payload,
                    ),
                )

        if args.stop_file is not None:
            _attempt_cleanup(
                cleanup_errors,
                "remove GUI stop file",
                lambda: args.stop_file.unlink(missing_ok=True),
            )

        if cleanup_errors:
            detail = _cleanup_message(cleanup_errors)
            if primary_error is None:
                raise RuntimeError(f"Run cleanup failed: {detail}") from cleanup_errors[0][1]
            print(
                f"Cleanup warning after {type(primary_error).__name__}: {detail}",
                file=sys.stderr,
                flush=True,
            )

    if tracker is None:
        raise RuntimeError("The run ended before an episode tracker was created.")
    return tracker.directory
