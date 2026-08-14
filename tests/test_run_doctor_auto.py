import json
from pathlib import Path

from deltarune_agent import run_doctor_auto


class _FakeTracker:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True)

    def finish(self, payload=None):
        (self.directory / "run.json").write_text(
            json.dumps({"agent_revision": "fake", "schema_version": 2}),
            encoding="utf-8",
        )
        (self.directory / "events.jsonl").write_text("", encoding="utf-8")
        return self.directory / "run_report.json"


def test_post_run_hook_runs_after_normal_finish(tmp_path: Path, monkeypatch):
    calls = []

    def analyze(directory: Path) -> None:
        calls.append(Path(directory))
        (Path(directory) / "run_doctor.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(run_doctor_auto, "analyze_finished_run", analyze)
    tracker_type = type("TrackerForSuccess", (_FakeTracker,), {})
    run_doctor_auto.install_post_run_hook(tracker_type)
    tracker = tracker_type(tmp_path / "run")
    result = tracker.finish({})
    assert result == tracker.directory / "run_report.json"
    assert calls == [tracker.directory]
    assert (tracker.directory / "run.json").is_file()
    assert (tracker.directory / "run_doctor.json").is_file()


def test_post_run_doctor_failure_never_breaks_completed_run(tmp_path: Path, monkeypatch):
    def explode(_directory: Path) -> None:
        raise RuntimeError("doctor fixture failure")

    monkeypatch.setattr(run_doctor_auto, "analyze_finished_run", explode)
    tracker_type = type("TrackerForFailure", (_FakeTracker,), {})
    run_doctor_auto.install_post_run_hook(tracker_type)
    tracker = tracker_type(tmp_path / "run")
    result = tracker.finish({})
    assert result == tracker.directory / "run_report.json"
    assert (tracker.directory / "run.json").is_file()
    error = json.loads((tracker.directory / "run_doctor_error.json").read_text(encoding="utf-8"))
    assert error["run_preserved"] is True
    assert error["error_type"] == "RuntimeError"


def test_post_run_hook_is_idempotent(tmp_path: Path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_doctor_auto,
        "analyze_finished_run",
        lambda directory: calls.append(Path(directory)),
    )
    tracker_type = type("TrackerIdempotent", (_FakeTracker,), {})
    run_doctor_auto.install_post_run_hook(tracker_type)
    first_finish = tracker_type.finish
    run_doctor_auto.install_post_run_hook(tracker_type)
    assert tracker_type.finish is first_finish
    tracker = tracker_type(tmp_path / "run")
    tracker.finish({})
    tracker.finish({})
    assert calls == [tracker.directory]
