import argparse
import json
import time
from pathlib import Path

from .controller import KeyboardController
from .observer import ScreenObserver
from .perception import VisualStateDetector
from .policy import StarterPolicy
from .progress import EpisodeTracker
from .telemetry import TelemetryReceiver, fuse_perception
from .window import (
    client_region,
    find_window,
    focus_window,
    foreground_description,
    is_window_foreground,
    remember_window,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="External Deltarune AI controller")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one bounded agent episode")
    run.add_argument("--live", action="store_true", help="actually send keyboard input")
    run.add_argument("--steps", type=int, default=100)
    run.add_argument(
        "--interval",
        type=float,
        default=None,
        help="override each action's tuned post-input delay",
    )
    run.add_argument("--seed", type=int, default=0)
    run.add_argument(
        "--game-window",
        default="deltarune",
        help="part of the game window title or executable name",
    )
    run.add_argument("--countdown", type=int, default=3, help="seconds before live input starts")
    run.add_argument("--telemetry-port", type=int, default=42069)
    run.add_argument(
        "--memory",
        type=Path,
        default=Path("memory/navigation.json"),
        help="persistent learned world model",
    )
    run.add_argument(
        "--visual-memory",
        type=Path,
        default=Path("memory/visual_states.json"),
        help="visual state model learned from telemetry labels",
    )
    run.add_argument(
        "--window-memory",
        type=Path,
        default=Path("memory/window_titles.json"),
        help="known Deltarune window titles and executable names",
    )
    run.add_argument("--no-telemetry", action="store_true", help="disable the local telemetry listener")
    run.add_argument("--region", nargs=4, type=int, metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"))
    run.add_argument("--stop-file", type=Path, help=argparse.SUPPRESS)
    run.add_argument("--event-stream", action="store_true", help=argparse.SUPPRESS)
    listen = sub.add_parser("telemetry", help="print telemetry without controlling the game")
    listen.add_argument("--port", type=int, default=42069)
    listen.add_argument("--seconds", type=float, default=30.0)
    sub.add_parser("gui", help="open the desktop controller and wall-map viewer")
    return parser


def listen(args: argparse.Namespace) -> None:
    receiver = TelemetryReceiver(args.port)
    deadline = time.monotonic() + args.seconds
    previous = None
    print(f"Listening on 127.0.0.1:{args.port}; press Ctrl+C to stop.")
    try:
        while time.monotonic() < deadline:
            sample = receiver.poll()
            if sample and sample != previous:
                player_position = (
                    f" player=({sample.player_x:.1f},{sample.player_y:.1f})"
                    if sample.player_x is not None and sample.player_y is not None
                    else ""
                )
                nearby = (
                    f" near={sample.nearest_interactable_name}"
                    f"@{sample.nearest_interactable_distance:.1f}"
                    if sample.nearest_interactable_name
                    and sample.nearest_interactable_distance is not None
                    else ""
                )
                print(
                    f"v{sample.version} {sample.mode:<9} "
                    f"room={sample.room_name or sample.room_id} "
                    f"source=({sample.x:.1f},{sample.y:.1f}){player_position} "
                    f"object={sample.object_name} sprite={sample.sprite_name or '-'} "
                    f"dir={sample.facing_direction or '-'}{nearby}"
                )
                previous = sample
            time.sleep(0.05)
    finally:
        receiver.close()


def run(args: argparse.Namespace) -> Path:
    if args.steps < 1 or (args.interval is not None and args.interval < 0) or args.countdown < 0:
        raise ValueError("steps must be positive; interval and countdown must be non-negative")
    observer = ScreenObserver(tuple(args.region) if args.region else None)
    detector = VisualStateDetector(args.visual_memory)
    telemetry_receiver = None if args.no_telemetry else TelemetryReceiver(args.telemetry_port)
    policy = StarterPolicy(args.seed, args.memory)
    if policy.memory_warning:
        print(f"Memory warning: {policy.memory_warning}")
    if detector.memory_warning:
        print(f"Visual memory warning: {detector.memory_warning}")
    controller = KeyboardController(args.live)
    tracker = EpisodeTracker()
    print(f"Mode: {'LIVE' if args.live else 'DRY RUN'} | output: {tracker.directory}")
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
        observer.set_region(client_region(window.hwnd))
        print(f"Capturing game area: {observer.region}")
    telemetry_seen = False
    try:
        for step in range(args.steps):
            if args.stop_file is not None and args.stop_file.exists():
                print("Stop requested by GUI; ending the run safely.", flush=True)
                break
            if args.live and not is_window_foreground(window):
                raise RuntimeError(
                    "Deltarune lost focus; stopped before sending more input. "
                    f"Foreground was {foreground_description()}."
                )
            observation = observer.observe(step)
            visual = detector.classify(observation.frame)
            telemetry = telemetry_receiver.poll() if telemetry_receiver else None
            if telemetry_receiver is not None:
                policy.observe_room_trace(telemetry_receiver.drain_overworld_trace())
            if telemetry is not None:
                telemetry_seen = True
                detector.learn_from_telemetry(observation.frame, telemetry.mode)
            elif telemetry_receiver is not None and step == 15 and not telemetry_seen:
                print(
                    "Telemetry warning: no packets received; continuing with the "
                    "learned visual model."
                )
            perception = fuse_perception(visual, telemetry)
            action = policy.choose(observation, perception, telemetry)
            map_updates = policy.drain_map_updates()
            location = (
                f" room={telemetry.room_name or telemetry.room_id} pos=({telemetry.x:.0f},{telemetry.y:.0f})"
                if telemetry
                else ""
            )
            print(
                f"{step:04d}: {perception.state.value:<9} {perception.confidence:.2f}"
                f" [{perception.source}] -> {action.name}{location} | {policy.reason}",
                flush=True,
            )
            tracker.record(observation, perception, telemetry, action, policy.reason, args.live)
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
                            "telemetry": telemetry.as_dict() if telemetry else None,
                            "map_updates": map_updates,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
            controller.execute(action)
            time.sleep(action.cooldown if args.interval is None else args.interval)
    finally:
        controller.release_all()
        if telemetry_receiver:
            telemetry_receiver.close()
        policy.save_memory()
        detector.save_memory()
        tracker.finish(policy.summary())
        if args.stop_file is not None:
            args.stop_file.unlink(missing_ok=True)
    return tracker.directory


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        run(args)
    elif args.command == "telemetry":
        listen(args)
    elif args.command == "gui":
        from .gui import launch_gui

        launch_gui()
