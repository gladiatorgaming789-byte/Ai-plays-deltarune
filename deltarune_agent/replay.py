from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from PIL import Image

from .evaluation import RunMetrics, calculate_metrics, load_events, write_metrics
from .perception import VisualStateDetector


@dataclass(frozen=True)
class ReplayResult:
    run_directory: Path
    metrics: RunMetrics
    frames_checked: int
    state_matches: int
    missing_frames: int

    @property
    def visual_state_accuracy(self) -> float:
        return self.state_matches / self.frames_checked if self.frames_checked else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "run_directory": str(self.run_directory),
            "metrics": self.metrics.as_dict(),
            "frames_checked": self.frames_checked,
            "state_matches": self.state_matches,
            "missing_frames": self.missing_frames,
            "visual_state_accuracy": round(self.visual_state_accuracy, 4),
        }


def replay_run(
    run_directory: Path,
    visual_memory: Path | None = None,
    save_report: bool = True,
) -> ReplayResult:
    events = load_events(run_directory)
    metrics = calculate_metrics(events)
    detector = VisualStateDetector(visual_memory)
    frames_checked = 0
    state_matches = 0
    missing_frames = 0

    for event in events:
        try:
            step = int(event.get("step", -1))
        except (TypeError, ValueError):
            continue
        frame_path = run_directory / f"frame-{step:06d}.png"
        if not frame_path.exists():
            missing_frames += 1
            continue
        try:
            with Image.open(frame_path) as image:
                replayed = detector.classify(image.convert("RGB"))
        except OSError:
            missing_frames += 1
            continue
        frames_checked += 1
        if replayed.state.value == str(event.get("state") or "unknown"):
            state_matches += 1

    result = ReplayResult(
        run_directory=run_directory,
        metrics=metrics,
        frames_checked=frames_checked,
        state_matches=state_matches,
        missing_frames=missing_frames,
    )
    if save_report:
        write_metrics(run_directory, metrics)
        (run_directory / "replay.json").write_text(
            json.dumps(result.as_dict(), indent=2),
            encoding="utf-8",
        )
    return result


def print_replay(result: ReplayResult) -> None:
    print(json.dumps(result.as_dict(), indent=2))
