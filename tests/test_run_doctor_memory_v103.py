from __future__ import annotations

import json
from pathlib import Path

from deltarune_agent import run_doctor as foundation
from deltarune_agent.run_doctor_memory_v103 import final_memory_exit_leak_finding


def _run(tmp_path: Path) -> foundation.NormalizedRun:
    return foundation.NormalizedRun(
        directory=tmp_path,
        manifest={},
        summary={},
        run_report={},
        telemetry_diagnostics={},
        speed_diagnostics={},
        events=[],
        predictions=[],
        navigation_updates=[],
    )


def test_final_navigation_snapshot_catches_inherited_exit_semantic_leaks(tmp_path: Path) -> None:
    (tmp_path / "navigation.json").write_text(
        json.dumps(
            {
                "screen_regions": [
                    {
                        "room": "room_old",
                        "region_x": 3,
                        "region_y": 4,
                        "hypothesis": "possible_exit",
                        "exit_candidate_state": "geometry_candidate",
                        "path_continuation": True,
                    },
                    {
                        "room": "room_current",
                        "region_x": 1,
                        "region_y": 2,
                        "hypothesis": "possible_exit",
                        "exit_candidate_state": "semantic_ready",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    finding = final_memory_exit_leak_finding(_run(tmp_path))

    assert finding is not None
    assert finding.finding_type == "unresolved_exit_semantic_leak"
    assert finding.measured["persistent_leaked_candidate_count"] == 1
    assert finding.measured["examples"][0]["room"] == "room_old"


def test_clean_final_navigation_snapshot_has_no_memory_leak_finding(tmp_path: Path) -> None:
    (tmp_path / "navigation.json").write_text(
        json.dumps(
            {
                "screen_regions": [
                    {
                        "room": "room_current",
                        "region_x": 1,
                        "region_y": 2,
                        "hypothesis": "possible_exit",
                        "exit_candidate_state": "confirmed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert final_memory_exit_leak_finding(_run(tmp_path)) is None
