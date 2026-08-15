from __future__ import annotations

from deltarune_agent.autonomy_shadow import (
    evaluate_autonomy_snapshot,
    replay_prediction_snapshots,
    score_option_payload,
)


def _option(option_id: str, base: float, *, selected: bool = False) -> dict:
    return {
        "id": option_id,
        "base_score": base,
        "score": base,
        "confidence": 0.0,
        "information_value": 0.0,
        "novelty": 0.0,
        "distance": 0,
        "loop_risk": 0.0,
        "failure_cost": 0.0,
        "budget_limit": 0,
        "budget_spent": 0,
        "selected": selected,
    }


def test_shadow_scoring_penalizes_loop_risk_without_changing_source() -> None:
    option = _option("warp", 8.0)
    option["loop_risk"] = 0.75
    before = dict(option)

    score = score_option_payload(option)

    assert score < 8.0
    assert option == before


def test_shadow_marks_commitment_explained_non_top_selection() -> None:
    snapshot = {
        "selected_option_id": "active",
        "commitment_hold": True,
        "ranked_options": [
            _option("best", 9.0),
            _option("active", 8.5, selected=True),
        ],
    }

    result = evaluate_autonomy_snapshot(snapshot)

    assert result.selected_option_id == "active"
    assert result.highest_scored_option_id == "best"
    assert result.commitment_hold is True
    assert result.score_gap == 0.5


def test_shadow_detects_impossible_budget_overrun() -> None:
    option = _option("weak", 6.0, selected=True)
    option["budget_limit"] = 2
    option["budget_spent"] = 3
    snapshot = {
        "selected_option_id": "weak",
        "commitment_hold": False,
        "ranked_options": [option],
    }

    result = evaluate_autonomy_snapshot(snapshot)

    assert result.budget_overrun is True


def test_replay_is_read_only_and_reports_unexplained_disagreement() -> None:
    snapshot = {
        "selected_option_id": "lower",
        "commitment_hold": False,
        "ranked_options": [
            _option("higher", 10.0),
            _option("lower", 7.0, selected=True),
        ],
    }
    predictions = [{"prediction_snapshot": {"autonomy": snapshot}}]

    result = replay_prediction_snapshots(predictions)

    assert result["decision_count"] == 1
    assert result["selection_disagreements"] == 1
    assert result["unexplained_selection_disagreements"] == 1
    assert result["max_unexplained_score_gap"] == 3.0
