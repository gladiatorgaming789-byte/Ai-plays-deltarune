from pathlib import Path

from deltarune_agent import run_doctor_auto


def test_post_run_hook_tolerates_none_return_when_tracker_has_no_directory(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_doctor_auto,
        "analyze_finished_run",
        lambda directory: calls.append(Path(directory)),
    )

    class Tracker:
        def finish(self):
            return None

    run_doctor_auto.install_post_run_hook(Tracker)
    assert Tracker().finish() is None
    assert calls == []
