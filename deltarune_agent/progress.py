from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
import time
from typing import Any, TextIO

from .actions import Action
from .observer import Observation
from .perception import Perception
from .run_artifacts import (
    RUN_SCHEMA_VERSION,
    build_run_diagnostics,
    copy_run_snapshots,
    export_navigation_maps,
    json_safe,
    utc_now_iso,
    write_json,
)
from .telemetry import TelemetrySample
from .version import AGENT_REVISION


class _JsonLinesWriter:
    """A lazy, persistent JSONL writer with bounded crash-loss buffering."""

    def __init__(self, path: Path, flush_interval: int) -> None:
        self.path = path
        self.flush_interval = flush_interval
        self._stream: TextIO | None = None
        self._pending = 0

    def write(self, value: Mapping[str, Any]) -> None:
        if self._stream is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.path.open(
                "a",
                encoding="utf-8",
                buffering=64 * 1024,
            )
        self._stream.write(
            json.dumps(
                json_safe(value),
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        self._pending += 1
        if self._pending >= self.flush_interval:
            self.flush()

    def flush(self) -> None:
        if self._stream is not None and self._pending:
            self._stream.flush()
            self._pending = 0

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
            self._pending = 0


class EpisodeTracker:
    """Record one AI run and package the evidence needed to audit it later."""

    def __init__(
        self,
        root: Path = Path("runs"),
        frame_interval: int = 10,
        *,
        flush_interval: int = 10,
        config: Mapping[str, Any] | object | None = None,
    ):
        if frame_interval < 1:
            raise ValueError("frame_interval must be positive")
        if flush_interval < 1:
            raise ValueError("flush_interval must be positive")

        # One wall-clock sample supplies both the directory name and manifest,
        # avoiding contradictory start timestamps around a second boundary.
        self.started_at = utc_now_iso()
        stamp = self.started_at.replace("-", "").replace(":", "")
        stamp = stamp.replace("Z", "Z").replace(".", ".")
        attempt = 0
        while True:
            suffix = "" if attempt == 0 else f"-{attempt}"
            self.directory = Path(root) / f"{stamp}{suffix}"
            try:
                self.directory.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                attempt += 1

        self.events = self.directory / "events.jsonl"
        self.predictions = self.directory / "predictions.jsonl"
        self.navigation_updates = self.directory / "navigation_updates.jsonl"
        self.manifest_path = self.directory / "run.json"
        self.summary_path = self.directory / "summary.json"
        self.report_path = self.directory / "run_report.json"
        self.frame_interval = frame_interval
        self.flush_interval = flush_interval

        self._events_writer = _JsonLinesWriter(self.events, flush_interval)
        self._predictions_writer = _JsonLinesWriter(
            self.predictions,
            flush_interval,
        )
        self._navigation_writer = _JsonLinesWriter(
            self.navigation_updates,
            flush_interval,
        )
        self._started_monotonic = time.monotonic()
        self._event_count = 0
        self._prediction_count = 0
        self._navigation_update_count = 0
        self._decision_context_count = 0
        self._frame_count = 0
        self._frame_failures = 0
        self._last_step: int | None = None
        self._stop_reason: str | None = None
        self._stop_detail: str | None = None
        self._finished = False
        self._policy_summary: dict[str, Any] = {}
        self._copied_artifacts: dict[str, str] = {}
        self._artifact_warnings: list[str] = []
        self._config = json_safe(config) if config is not None else {}
        self._manifest: dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "agent_revision": AGENT_REVISION,
            "status": "running",
            "started_at": self.started_at,
            "ended_at": None,
            "duration_seconds": None,
            "stop_reason": None,
            "stop_detail": None,
            "config": self._config,
            "recording": self._recording_summary(),
            "artifacts": {},
            "warnings": [],
        }
        self._write_manifest()

    def __enter__(self) -> EpisodeTracker:
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        if not self._finished:
            reason = "error" if exc is not None else "context_closed"
            detail = str(exc) if exc is not None else None
            self.finish({}, stop_reason=reason, stop_detail=detail)

    def __del__(self) -> None:
        # Do not attempt report generation during interpreter shutdown, but do
        # release Windows file handles so temporary run directories remain
        # removable in tests and aborted sessions.
        try:
            self.close()
        except Exception:
            pass

    def set_config(self, config: Mapping[str, Any] | object | None) -> None:
        """Attach the effective runner configuration to the run manifest."""
        self._config = json_safe(config) if config is not None else {}
        self._manifest["config"] = self._config
        self._write_manifest()

    def set_stop_reason(self, reason: str, detail: str | None = None) -> None:
        """Record why execution is expected to stop before calling finish."""
        self._stop_reason = str(reason).strip() or "unspecified"
        self._stop_detail = str(detail) if detail is not None else None

    @staticmethod
    def _normalise_updates(
        map_updates: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if map_updates is None:
            return []
        if isinstance(map_updates, Mapping):
            values: Iterable[Mapping[str, Any]] = (map_updates,)
        else:
            values = map_updates
        return [
            json_safe(update)
            for update in values
            if isinstance(update, Mapping)
        ]

    def record(
        self,
        observation: Observation,
        perception: Perception,
        telemetry: TelemetrySample | None,
        action: Action,
        reason: str,
        live: bool,
        *,
        decision_context: Mapping[str, Any] | None = None,
        map_updates: (
            Iterable[Mapping[str, Any]] | Mapping[str, Any] | None
        ) = None,
        prediction_snapshot: Mapping[str, Any] | Iterable[Any] | None = None,
    ) -> None:
        """Record an action and its structured evidence.

        The first six arguments retain the original API. New evidence fields
        are keyword-only so existing runner and test calls remain valid.
        ``prediction_snapshot`` may contain ranked candidate guesses, scores,
        or any other policy-owned visible evidence for the selected action.
        """
        if self._finished:
            raise RuntimeError("cannot record after the run has finished")

        step = int(observation.step)
        recorded_at = utc_now_iso()
        elapsed = max(0.0, time.monotonic() - self._started_monotonic)
        updates = self._normalise_updates(map_updates)
        context = json_safe(decision_context) if decision_context else None
        snapshot = (
            json_safe(prediction_snapshot)
            if prediction_snapshot is not None
            else None
        )
        telemetry_data = telemetry.as_dict() if telemetry else None
        event: dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "agent_revision": AGENT_REVISION,
            "recorded_at": recorded_at,
            "elapsed_seconds": round(elapsed, 4),
            "step": step,
            "state": perception.state.value,
            "confidence": perception.confidence,
            "perception_source": perception.source,
            "features": perception.features.as_dict(),
            # Older replay/test observations predate capture-freshness
            # tracking. Treat a missing flag as valid.
            "visual_valid": getattr(observation, "visual_valid", True),
            "telemetry": telemetry_data,
            "action": action.name,
            "reason": reason,
            "live": live,
        }
        if context is not None:
            event["decision_context"] = context
        if updates:
            event["map_updates"] = updates
        if snapshot is not None:
            # The full ranked snapshot lives in predictions.jsonl. Keep the
            # action event compact while making the cross-file relationship
            # explicit instead of duplicating a potentially large candidate
            # table on every step.
            event["prediction_recorded"] = True
        self._events_writer.write(event)
        self._event_count += 1
        self._last_step = step

        prediction: dict[str, Any] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "agent_revision": AGENT_REVISION,
            "recorded_at": recorded_at,
            "elapsed_seconds": round(elapsed, 4),
            "step": step,
            "observed_state": {
                "name": perception.state.value,
                "confidence": perception.confidence,
                "source": perception.source,
                "visual_valid": event["visual_valid"],
            },
            "selected_action": action.name,
            "reason": reason,
            "decision_context": context,
            "prediction_snapshot": snapshot,
        }
        if isinstance(telemetry_data, dict):
            prediction["location"] = {
                "room": (
                    telemetry_data.get("room_name")
                    or telemetry_data.get("room_id")
                ),
                "x": telemetry_data.get("player_foot_x")
                if telemetry_data.get("player_foot_x") is not None
                else telemetry_data.get("player_x", telemetry_data.get("x")),
                "y": telemetry_data.get("player_foot_y")
                if telemetry_data.get("player_foot_y") is not None
                else telemetry_data.get("player_y", telemetry_data.get("y")),
                "facing": (
                    telemetry_data.get("player_facing_direction")
                    or telemetry_data.get("facing_direction")
                ),
            }
        self._predictions_writer.write(prediction)
        self._prediction_count += 1
        if context is not None:
            self._decision_context_count += 1

        for update in updates:
            self._navigation_writer.write(
                {
                    "schema_version": RUN_SCHEMA_VERSION,
                    "agent_revision": AGENT_REVISION,
                    "recorded_at": recorded_at,
                    "step": step,
                    "update": update,
                }
            )
            self._navigation_update_count += 1

        frame_due = step % self.frame_interval == 0
        if frame_due:
            try:
                observation.frame.save(
                    self.directory / f"frame-{step:06d}.png",
                    compress_level=1,
                )
                self._frame_count += 1
            except (OSError, ValueError):
                self._frame_failures += 1
            # Align event durability with visual checkpoints. At most
            # ``flush_interval - 1`` non-frame records remain buffered.
            self.flush()

    def flush(self) -> None:
        """Make every pending JSONL record visible to readers immediately."""
        self._events_writer.flush()
        self._predictions_writer.flush()
        self._navigation_writer.flush()

    def close(self) -> None:
        self._events_writer.close()
        self._predictions_writer.close()
        self._navigation_writer.close()

    def _recording_summary(self) -> dict[str, Any]:
        return {
            "events": self._event_count,
            "predictions": self._prediction_count,
            "predictions_with_context": self._decision_context_count,
            "navigation_updates": self._navigation_update_count,
            "frames": self._frame_count,
            "frame_failures": self._frame_failures,
            "last_step": self._last_step,
            "frame_interval": self.frame_interval,
            "flush_interval": self.flush_interval,
        }

    def _artifact_summary(self) -> dict[str, Any]:
        def file_record(path: Path, records: int | None = None) -> dict[str, Any]:
            result: dict[str, Any] = {
                "path": path.relative_to(self.directory).as_posix(),
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else 0,
            }
            if records is not None:
                result["records"] = records
            return result

        return {
            "events": file_record(self.events, self._event_count),
            "predictions": file_record(
                self.predictions,
                self._prediction_count,
            ),
            "navigation_updates": file_record(
                self.navigation_updates,
                self._navigation_update_count,
            ),
            "frames": {
                "pattern": "frame-*.png",
                "count": self._frame_count,
                "failures": self._frame_failures,
            },
            "snapshots": dict(sorted(self._copied_artifacts.items())),
        }

    def _write_manifest(self) -> None:
        self._manifest["recording"] = self._recording_summary()
        self._manifest["artifacts"] = self._artifact_summary()
        self._manifest["warnings"] = list(self._artifact_warnings)
        write_json(self.manifest_path, self._manifest)

    def finalize_artifacts(
        self,
        *,
        navigation_path: Path | None = None,
        room_views_path: Path | None = None,
        extra_files: Mapping[str, Path] | None = None,
        export_maps: bool = True,
    ) -> dict[str, str]:
        """Copy learned memory into the run and optionally render map PNGs."""
        copied, warnings = copy_run_snapshots(
            self.directory,
            navigation_path=navigation_path,
            room_views_path=room_views_path,
            extra_files=extra_files,
        )
        self._copied_artifacts.update(copied)
        self._artifact_warnings.extend(warnings)

        copied_navigation = self.directory / "navigation.json"
        copied_views = self.directory / "room_views" / "index.json"
        if export_maps and copied_navigation.is_file() and copied_views.is_file():
            try:
                outputs = export_navigation_maps(
                    copied_navigation,
                    copied_views,
                    self.directory / "navigation_maps",
                )
                if outputs:
                    self._copied_artifacts["navigation_maps"] = (
                        "navigation_maps/"
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._artifact_warnings.append(
                    f"Could not export navigation maps: {exc}"
                )

        self._write_manifest()
        if self._finished:
            self._write_reports()
        return dict(self._copied_artifacts)

    def _load_recorded_events(self) -> list[dict[str, Any]]:
        if not self.events.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def _write_reports(self) -> None:
        from .evaluation import calculate_metrics

        try:
            events = self._load_recorded_events()
            metrics = calculate_metrics(events).as_dict()
            diagnostics = build_run_diagnostics(events)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            events = []
            metrics = {}
            diagnostics = {"error": str(exc)}
            warning = f"Could not calculate run metrics: {exc}"
            if warning not in self._artifact_warnings:
                self._artifact_warnings.append(warning)

        policy_summary = dict(self._policy_summary)
        policy_summary["agent_revision"] = AGENT_REVISION
        policy_summary["run"] = {
            "started_at": self._manifest.get("started_at"),
            "ended_at": self._manifest.get("ended_at"),
            "duration_seconds": self._manifest.get("duration_seconds"),
            "stop_reason": self._manifest.get("stop_reason"),
        }
        policy_summary["metrics"] = metrics
        write_json(self.summary_path, policy_summary)

        report = {
            "schema_version": RUN_SCHEMA_VERSION,
            "agent_revision": AGENT_REVISION,
            "run": self._manifest,
            "metrics": metrics,
            "diagnostics": diagnostics,
            "policy_summary": self._policy_summary,
            "recording": self._recording_summary(),
            "artifacts": self._artifact_summary(),
            "warnings": list(self._artifact_warnings),
        }
        write_json(self.report_path, report)

    def finish(
        self,
        policy_summary: dict,
        *,
        stop_reason: str | None = None,
        stop_detail: str | None = None,
        config: Mapping[str, Any] | object | None = None,
        navigation_path: Path | None = None,
        room_views_path: Path | None = None,
        extra_files: Mapping[str, Path] | None = None,
        export_maps: bool = True,
    ) -> Path:
        """Finalize logs, metrics, metadata, and supplied learned snapshots.

        Calling this more than once is safe; later calls can still supply
        snapshot paths that were unavailable during the first finalization.
        """
        if config is not None:
            self.set_config(config)
        if stop_reason is not None:
            self.set_stop_reason(stop_reason, stop_detail)
        elif stop_detail is not None:
            self._stop_detail = stop_detail
        self._policy_summary = dict(policy_summary)

        if not self._finished:
            self.flush()
            self.close()
            duration = max(0.0, time.monotonic() - self._started_monotonic)
            self._manifest.update(
                {
                    "status": "finished",
                    "ended_at": utc_now_iso(),
                    "duration_seconds": round(duration, 4),
                    "stop_reason": self._stop_reason or "runner_finished",
                    "stop_detail": self._stop_detail,
                }
            )
            self._finished = True

        if (
            navigation_path is not None
            or room_views_path is not None
            or extra_files
        ):
            self.finalize_artifacts(
                navigation_path=navigation_path,
                room_views_path=room_views_path,
                extra_files=extra_files,
                export_maps=export_maps,
            )
        else:
            self._write_manifest()
        self._write_reports()
        # Reports may have added a warning while parsing; make the manifest's
        # warning list agree with the report before returning.
        self._write_manifest()
        return self.report_path
