"""Read-only counterfactual analysis for Autonomy v1 decisions.

The shadow evaluator consumes option snapshots already emitted by the gameplay
agent. It never controls DELTARUNE, edits learned memory, or adds route knowledge.
Its purpose is to answer questions such as "would different generic ranking
weights have selected another observed option?" after a run has finished.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


DEFAULT_WEIGHTS = {
    "confidence": 3.0,
    "information_value": 2.8,
    "novelty": 2.0,
    "distance": -0.30,
    "loop_risk": -4.0,
    "failure_cost": -1.3,
    "budget_fraction_spent": -2.2,
}


@dataclass(frozen=True)
class ShadowDecision:
    selected_option_id: str | None
    highest_scored_option_id: str | None
    selected_score: float | None
    highest_score: float | None
    score_gap: float | None
    commitment_hold: bool
    budget_overrun: bool
    option_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return default if result != result else result


def score_option_payload(
    option: Mapping[str, object],
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Re-score one recorded option using only fields saved by the live agent."""

    merged = dict(DEFAULT_WEIGHTS)
    if weights:
        merged.update({key: float(value) for key, value in weights.items()})
    base = option.get("base_score")
    if base is None:
        # Older Autonomy snapshots may predate base-score export. Their stored
        # score is still useful for ordering, but cannot support full weight
        # counterfactuals.
        return _number(option.get("score"), float("-inf"))
    limit = max(0.0, _number(option.get("budget_limit")))
    spent = max(0.0, _number(option.get("budget_spent")))
    budget_fraction = spent / limit if limit > 0 else 0.0
    return (
        _number(base)
        + _number(option.get("confidence")) * merged["confidence"]
        + _number(option.get("information_value")) * merged["information_value"]
        + _number(option.get("novelty")) * merged["novelty"]
        + min(12.0, max(0.0, _number(option.get("distance")))) * merged["distance"]
        + _number(option.get("loop_risk")) * merged["loop_risk"]
        + _number(option.get("failure_cost")) * merged["failure_cost"]
        + budget_fraction * merged["budget_fraction_spent"]
    )


def rank_snapshot_options(
    autonomy_snapshot: Mapping[str, object],
    *,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[float, Mapping[str, object]]]:
    raw = autonomy_snapshot.get("ranked_options")
    if not isinstance(raw, list):
        return []
    ranked = [
        (score_option_payload(option, weights=weights), option)
        for option in raw
        if isinstance(option, Mapping)
    ]
    ranked.sort(
        key=lambda item: (
            -item[0],
            _number(item[1].get("distance")),
            str(item[1].get("id") or ""),
        )
    )
    return ranked


def evaluate_autonomy_snapshot(
    autonomy_snapshot: Mapping[str, object],
    *,
    weights: Mapping[str, float] | None = None,
) -> ShadowDecision:
    ranked = rank_snapshot_options(autonomy_snapshot, weights=weights)
    selected_id = str(autonomy_snapshot.get("selected_option_id") or "") or None
    highest_id = (
        str(ranked[0][1].get("id") or "") or None
        if ranked
        else None
    )
    selected_score = None
    budget_overrun = False
    for score, option in ranked:
        if str(option.get("id") or "") == selected_id:
            selected_score = score
        limit = int(max(0.0, _number(option.get("budget_limit"))))
        spent = int(max(0.0, _number(option.get("budget_spent"))))
        if limit > 0 and spent > limit:
            budget_overrun = True
    highest_score = ranked[0][0] if ranked else None
    gap = (
        highest_score - selected_score
        if highest_score is not None and selected_score is not None
        else None
    )
    return ShadowDecision(
        selected_option_id=selected_id,
        highest_scored_option_id=highest_id,
        selected_score=selected_score,
        highest_score=highest_score,
        score_gap=gap,
        commitment_hold=bool(autonomy_snapshot.get("commitment_hold")),
        budget_overrun=budget_overrun,
        option_count=len(ranked),
    )


def replay_prediction_snapshots(
    predictions: Iterable[Mapping[str, object]],
    *,
    weights: Mapping[str, float] | None = None,
) -> dict[str, object]:
    decisions: list[ShadowDecision] = []
    for prediction in predictions:
        snapshot = prediction.get("prediction_snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        autonomy = snapshot.get("autonomy")
        if not isinstance(autonomy, Mapping):
            continue
        decisions.append(evaluate_autonomy_snapshot(autonomy, weights=weights))
    scored = [decision for decision in decisions if decision.highest_score is not None]
    disagreements = [
        decision
        for decision in scored
        if decision.selected_option_id is not None
        and decision.highest_scored_option_id is not None
        and decision.selected_option_id != decision.highest_scored_option_id
    ]
    unexplained = [
        decision
        for decision in disagreements
        if not decision.commitment_hold
    ]
    return {
        "decision_count": len(decisions),
        "ranked_decision_count": len(scored),
        "selection_disagreements": len(disagreements),
        "commitment_explained_disagreements": sum(
            decision.commitment_hold for decision in disagreements
        ),
        "unexplained_selection_disagreements": len(unexplained),
        "budget_overrun_decisions": sum(decision.budget_overrun for decision in decisions),
        "max_unexplained_score_gap": max(
            (decision.score_gap or 0.0 for decision in unexplained),
            default=0.0,
        ),
    }


__all__ = [
    "DEFAULT_WEIGHTS",
    "ShadowDecision",
    "evaluate_autonomy_snapshot",
    "rank_snapshot_options",
    "replay_prediction_snapshots",
    "score_option_payload",
]
