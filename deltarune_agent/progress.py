import json
from datetime import datetime, timezone
from pathlib import Path

from .actions import Action
from .observer import Observation
from .perception import Perception
from .telemetry import TelemetrySample


class EpisodeTracker:
    def __init__(self, root: Path = Path("runs"), frame_interval: int = 10):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.directory = root / stamp
        self.directory.mkdir(parents=True, exist_ok=False)
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
            "telemetry": telemetry.as_dict() if telemetry else None,
            "action": action.name,
            "reason": reason,
            "live": live,
        }
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        if observation.step % self.frame_interval == 0:
            observation.frame.save(self.directory / f"frame-{observation.step:06d}.png")

    def finish(self, policy_summary: dict) -> None:
        with (self.directory / "summary.json").open("w", encoding="utf-8") as stream:
            json.dump(policy_summary, stream, indent=2)
