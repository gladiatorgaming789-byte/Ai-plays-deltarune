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
from .telemetry import TelemetryReceiver, fuse_perception
from .window import (
    client_region,
    find_window,
    focus_window,
    is_window_foreground,
    remember_window,
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

    observer: ScreenObserver | None = None
    detector: VisualStateDetector | None = None
    cutscene_tracker: CutsceneTracker | None = None
    telemetry_receiver: TelemetryReceiver | None = None
    policy: HierarchicalPolicy | None = None
    controller: KeyboardController | None = None
    tracker: EpisodeTracker | None = None
    primary_error: BaseException | None = None
    stop_reason = "step_limit"
    stop_detail: str | None = None

    try:
        observer = ScreenObserver(tuple(args.region) if args.region else None)
        detector = VisualStateDetector(args.visual_memory)
        cutscene_tracker = CutsceneTracker()
        telemetry_receiver = (
            None if args.no_telemetry else TelemetryReceiver(args.telemetry_port)
        )
        policy = HierarchicalPolicy(args.seed, args.memory)
        controller = KeyboardController(args.live)
        tracker = EpisodeTracker(config=vars(args))

        if policy.memory_warning:
            print(f"Memory warning: {policy.memory_warning}")
        if detector.memory_warning:
            print(f"Visual memory warning: {detector.memory_warning}")
        print(
            f"Mode: {'LIVE' if args.live else 'DRY RUN'} | "
            f"output: {tracker.directory}"
        )
        print("Emergency stop: move mouse to upper-left corner or press Ctrl+C.")

        window = None
        if args.live:
            window = focus_window(args.game_window, args.window_memory)
            remember_window(args.window_memory, window)
            print(f"Focused game window: {window.title} ({window.executable})")
            for remaining in range(args.countdown, 0, -1):
                print(f"Starting controls in {remaining}...")
                time.sleep(1)
        elif not args.region:
            window = find_window(args.game_window, args.window_memory)
            if window is not None:
                remember_window(args.window_memory, window)

        if window and not args.region:
            observer.set_window(window.hwnd, client_region(window.hwnd))
            print(f"Capturing game area: {observer.region}")
        if window is not None:
            controller.set_target_window(window.hwnd)

        telemetry_seen = False
        background_input = False
        for step in range(args.steps):
            if args.stop_file is not None and args.stop_file.exists():
                stop_reason = "gui_stop"
                print(
                    "Stop requested by GUI; ending the run safely.",
                    flush=True,
                )
                break

            if args.live:
                use_background_input = not is_window_foreground(window)
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
                policy.observe_room_trace(
                    telemetry_receiver.drain_overworld_trace()
                )
            if telemetry is not None:
                telemetry_seen = True
            elif telemetry_receiver is not None and step == 15 and not telemetry_seen:
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
            decision_context = policy.decision_context()
            prediction_snapshot = policy.prediction_snapshot()
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
                decision_context=decision_context,
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
                            "decision_context": decision_context,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

            controller.execute(action)
            time.sleep(
                action.cooldown if args.interval is None else args.interval
            )
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
            _attempt_cleanup(cleanup_errors, "release keyboard input", controller.release_all)

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
                    "visual_states.json": args.visual_memory,
                    "window_titles.json": args.window_memory,
                }.items()
                if Path(path).is_file()
            }
            if diagnostics_path.is_file():
                extra_files["telemetry_diagnostics.json"] = diagnostics_path
            _attempt_cleanup(
                cleanup_errors,
                "finish run artifacts",
                lambda: tracker.finish(
                    policy_summary,
                    stop_reason=stop_reason,
                    stop_detail=stop_detail,
                    config=vars(args),
                    navigation_path=args.memory,
                    room_views_path=args.memory.parent / "room_views",
                    extra_files=extra_files,
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
