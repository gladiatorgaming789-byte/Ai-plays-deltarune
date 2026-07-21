import json
from datetime import datetime, timezone
from pathlib import Path

from .actions import Action
from .observer import Observation
from .perception import Perception
from .telemetry import TelemetrySample


class EpisodeTracker:
    def __init__(
        self,
        root: Path = Path("runs"),
        frame_interval: int = 10,
    ):
        if frame_interval < 1:
            raise ValueError("frame_interval must be positive")
        stamp = datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        attempt = 0
        while True:
            suffix = "" if attempt == 0 else f"-{attempt}"
            self.directory = root / f"{stamp}{suffix}"
            try:
                self.directory.mkdir(
                    parents=True,
                    exist_ok=False,
                )
                break
            except FileExistsError:
                attempt += 1
        self.events = self.directory / "events.jsonl"
        self.frame_interval = frame_interval

    def record(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None,
        action: Action,
        reason: str,
        live: bool,
    ) -> None:
        event = {
            "step": observation.step,
            "state": perception.state.value,
            "confidence": perception.confidence,
            "perception_source": perception.source,
            "features": perception.features.as_dict(),
            "visual_valid": observation.visual_valid,
            "telemetry": (
                telemetry.as_dict()
                if telemetry
                else None
            ),
            "action": action.name,
            "reason": reason,
            "live": live,
        }
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        if observation.step % self.frame_interval == 0:
            try:
                self.directory.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                observation.frame.save(
                    self.directory
                    / f"frame-{observation.step:06d}.png"
                )
            except OSError:
                pass

    def finish(self, policy_summary: dict) -> None:
        with (
            self.directory / "summary.json"
        ).open("w", encoding="utf-8") as stream:
            json.dump(policy_summary, stream, indent=2)
