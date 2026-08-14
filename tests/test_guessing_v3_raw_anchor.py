from __future__ import annotations

from deltarune_agent import guessing_v3 as v3
from deltarune_agent import guessing_v3_screen as v3_screen
from deltarune_agent.guessing_v3_calibration import install_guessing_v3_calibration


install_guessing_v3_calibration()


def _record() -> dict[str, object]:
    return {
        "guess_id": "room_test@2,2",
        "views": 2,
        "independent_views": 2,
        "interest": 0.24,
        "hypothesis": None,
        "guess_state": "proposed",
        "entity_approach_directions": 1,
        "obstruction_target_cells": 4,
        # Deliberately frozen legacy routing anchor. A broken multi-view
        # implementation would sample this twice and report perfect stability.
        "anchor_world": [999.0, 999.0],
        "last_seen_sequence": 1,
        "last_seen_step": 10,
        "evidence_viewpoints": [[0, 0]],
    }


def test_multiview_consistency_uses_raw_current_anchor_not_frozen_memory() -> None:
    record = _record()
    v3_screen._LATEST_RAW.clear()
    v3_screen._LATEST_RAW[(2, 2)] = {
        "focus_world": [100.0, 100.0],
        "interest": 0.24,
        "signature": "first",
    }
    v3.refresh_guess_record_v3(record, region=(2, 2))

    record["last_seen_sequence"] = 2
    record["last_seen_step"] = 20
    record["evidence_viewpoints"] = [[0, 0], [2, 0]]
    v3_screen._LATEST_RAW[(2, 2)] = {
        "focus_world": [164.0, 100.0],
        "interest": 0.24,
        "signature": "second",
    }
    v3.refresh_guess_record_v3(record, region=(2, 2))

    samples = record["guess_world_samples"]
    assert samples[0]["anchor_world"] == [100.0, 100.0]
    assert samples[1]["anchor_world"] == [164.0, 100.0]
    assert all(sample["anchor_source"] == "raw_observation" for sample in samples)
    assert record["multi_view_sample_count"] == 2
    assert record["multi_view_consistency"] <= 0.05


def test_multiview_falls_back_to_stable_memory_outside_live_observation() -> None:
    record = _record()
    v3_screen._LATEST_RAW.clear()

    v3.refresh_guess_record_v3(record, region=(2, 2))

    assert record["guess_world_samples"][0]["anchor_world"] == [999.0, 999.0]
    assert record["guess_world_samples"][0]["anchor_source"] == "stable_memory_fallback"
