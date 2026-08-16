from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltarune_agent.qt_ui.artifacts import (
    iter_jsonl,
    load_run_summary,
    scan_runs,
    summarize_autonomy_predictions,
    tail_jsonl,
)
from deltarune_agent.qt_ui.themes import (
    BUILTIN_THEMES,
    BackgroundSettings,
    discover_themes,
    stylesheet,
    supported_background,
    theme_from_manifest,
    validate_theme_manifest,
)


def test_builtin_themes_have_complete_valid_manifests() -> None:
    assert set(BUILTIN_THEMES) == {
        "castle_town",
        "cyber_city",
        "hometown_sunset",
        "operator",
    }
    for theme in BUILTIN_THEMES.values():
        assert validate_theme_manifest(theme.to_manifest()) == []
        assert theme.colors["accent"]
        assert theme.map_colors["player"]


def test_theme_styles_tables_without_native_light_header_leaks() -> None:
    sheet = stylesheet(BUILTIN_THEMES["operator"])

    assert "QHeaderView::section" in sheet
    assert "QTableCornerButton::section" in sheet
    assert "alternate-background-color" in sheet


def test_theme_manifest_validation_and_custom_overrides() -> None:
    payload = BUILTIN_THEMES["operator"].to_manifest()
    payload["id"] = "custom_blue"
    payload["name"] = "Custom Blue"
    payload["map_colors"] = {"player": "#abcdef"}
    theme = theme_from_manifest(payload)
    assert theme.id == "custom_blue"
    assert theme.map_colors["player"] == "#abcdef"
    assert theme.map_colors["wall"]

    payload["id"] = "Not Valid"
    assert any("id must" in error for error in validate_theme_manifest(payload))


def test_theme_discovery_keeps_builtins_when_custom_file_is_bad(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    custom = BUILTIN_THEMES["operator"].to_manifest()
    custom["id"] = "quiet"
    custom["name"] = "Quiet"
    (tmp_path / "quiet.json").write_text(json.dumps(custom), encoding="utf-8")
    themes, warnings = discover_themes(tmp_path)
    assert "castle_town" in themes
    assert themes["quiet"].name == "Quiet"
    assert warnings and warnings[0].startswith("broken.json:")


def test_background_settings_are_bounded_and_extensions_are_explicit(tmp_path: Path) -> None:
    settings = BackgroundSettings.from_mapping(
        {
            "path": "example.gif",
            "mode": "unknown",
            "dim": 9,
            "parallax": False,
            "animation": True,
        }
    )
    assert settings.mode == "cover"
    assert settings.dim == 0.95
    assert settings.animation is True
    assert settings.parallax is False
    assert supported_background(tmp_path / "image.WEBP")
    assert supported_background(tmp_path / "movie.mp4")
    assert not supported_background(tmp_path / "payload.exe")


def test_run_summary_combines_small_manifests_without_reading_jsonl(tmp_path: Path) -> None:
    run = tmp_path / "20260802T120000Z"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "started_at": "2026-08-02T12:00:00Z",
                "duration_seconds": 12.5,
                "stop_reason": "step_limit",
                "recording": {
                    "events": 90,
                    "predictions": 89,
                    "navigation_updates": 7,
                    "last_step": 90,
                },
                "warnings": ["one"],
            }
        ),
        encoding="utf-8",
    )
    (run / "summary.json").write_text(
        json.dumps(
            {
                "rooms_discovered": 4,
                "story_progress_events": 2,
                "reinforcement_total_reward": 13.25,
            }
        ),
        encoding="utf-8",
    )
    (run / "events.jsonl").write_text("this is intentionally unread\n", encoding="utf-8")
    summary = load_run_summary(run)
    assert summary.status == "finished"
    assert summary.last_step == 90
    assert summary.rooms == 4
    assert summary.story_progress == 2
    assert summary.total_reward == 13.25
    assert summary.warning_count == 1
    assert scan_runs(tmp_path) == [summary]


def test_jsonl_readers_skip_bad_records_and_remain_bounded(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"step": step}) if step != 4 else "bad json"
            for step in range(10)
        )
        + "\n",
        encoding="utf-8",
    )
    assert [value["step"] for _line, value in iter_jsonl(path, start=2, limit=4)] == [2, 3, 5]
    assert [value["step"] for _line, value in tail_jsonl(path, limit=3)] == [7, 8, 9]
    with pytest.raises(ValueError):
        list(iter_jsonl(path, start=-1))


def test_unreadable_run_is_reported_instead_of_crashing(tmp_path: Path) -> None:
    run = tmp_path / "broken"
    run.mkdir()
    (run / "run.json").write_text("[]", encoding="utf-8")
    summary = load_run_summary(run)
    assert summary.status == "unreadable"
    assert summary.error


def test_autonomy_workbench_summarizes_latest_goal_and_shadow_window() -> None:
    option = {
        "id": "learned:room_next",
        "kind": "learned_warp",
        "required_level": "learned_route",
        "base_score": 8.0,
        "score": 8.0,
        "confidence": 0.8,
        "information_value": 0.4,
        "novelty": 0.5,
        "distance": 3,
        "loop_risk": 0.1,
        "failure_cost": 0.0,
        "budget_spent": 1,
        "budget_limit": 4,
        "budget_remaining": 3,
        "selected": True,
        "metadata": {"target_room": "room_next"},
    }
    prediction = {
        "step": 42,
        "prediction_snapshot": {
            "room": "room_start",
            "autonomy": {
                "version": 1,
                "recovery_level": "learned_route",
                "recovery_reason": "frontier stalled",
                "active_goal_id": "learned:room_next",
                "active_goal_kind": "learned_warp",
                "active_goal_age": 2,
                "selected_option_id": "learned:room_next",
                "active_budget": {"spent": 1, "limit": 4, "remaining": 3},
                "ranked_options": [option],
            },
        },
    }

    summary = summarize_autonomy_predictions([prediction])

    assert summary.available is True
    assert summary.latest_step == 42
    assert summary.latest_room == "room_start"
    assert summary.active_goal_kind == "learned_warp"
    assert summary.options[0].selected is True
    assert summary.options[0].metadata["target_room"] == "room_next"
    assert summary.shadow["decision_count"] == 1
    assert summary.shadow["unexplained_selection_disagreements"] == 0
