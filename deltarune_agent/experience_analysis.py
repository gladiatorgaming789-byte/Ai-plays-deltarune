"""Quality and coverage report for a collected neural experience JSONL dataset.

This analyzer never trains the policy or assigns rewards. Thresholds below are
screening heuristics for collecting more data, not claims of statistical
sufficiency.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .actions import ACTIONS
from .experience import read_transitions
from .neural_env import DEFAULT_ENCODER

LOW_ACTION_FRACTION = 0.01
LOW_FEATURE_AVAILABILITY = 0.05
MIN_TRANSITIONS_FOR_COVERAGE = 100


def analyze_dataset(path: Path) -> dict[str, object]:
    transitions = read_transitions(path)
    total = len(transitions)
    actions = Counter(item.action for item in transitions)
    action_counts = {name: actions.get(name, 0) for name in ACTIONS}
    action_fractions = {
        name: (count / total if total else 0.0)
        for name, count in action_counts.items()
    }
    missing_actions = [name for name, count in action_counts.items() if count == 0]
    underrepresented_actions = [
        name for name, fraction in action_fractions.items()
        if total and fraction < LOW_ACTION_FRACTION
    ]

    widths = sorted({len(item.observation) for item in transitions})
    next_widths = sorted({len(item.next_observation) for item in transitions})
    transition_width_mismatches = sum(
        len(item.observation) != len(item.next_observation)
        for item in transitions
    )
    state_keys = [item.observation for item in transitions]
    duplicates = total - len(set(state_keys))

    feature_width = (
        widths[0]
        if len(widths) == 1
        and len(next_widths) == 1
        and widths[0] == next_widths[0]
        and transition_width_mismatches == 0
        else None
    )
    feature_names = (
        DEFAULT_ENCODER.feature_names
        if feature_width == DEFAULT_ENCODER.width
        else tuple(f"feature_{index}" for index in range(feature_width or 0))
    )
    feature_available_counts = [0] * (feature_width or 0)
    feature_slots = total * 2
    if feature_width is not None:
        for item in transitions:
            for index, mask in enumerate(item.available):
                feature_available_counts[index] += int(mask)
            for index, mask in enumerate(item.next_available):
                feature_available_counts[index] += int(mask)
    feature_availability = {
        name: (
            feature_available_counts[index] / feature_slots
            if feature_slots else 0.0
        )
        for index, name in enumerate(feature_names)
    }
    never_available_features = [
        name for name, fraction in feature_availability.items()
        if fraction == 0.0
    ]
    low_availability_features = [
        name for name, fraction in feature_availability.items()
        if 0.0 < fraction < LOW_FEATURE_AVAILABILITY
    ]

    sequence_pairs = [
        (item.source_sequence, item.next_source_sequence)
        for item in transitions
        if item.source_sequence is not None
        and item.next_source_sequence is not None
    ]
    non_increasing = sum(current <= previous for previous, current in sequence_pairs)
    gaps = sum(current - previous > 1 for previous, current in sequence_pairs)

    blockers = [
        "Rewards are intentionally unassigned; define and validate an outcome-based reward model first."
    ]
    if total < MIN_TRANSITIONS_FOR_COVERAGE:
        blockers.append(
            f"Collect at least {MIN_TRANSITIONS_FOR_COVERAGE} transitions before evaluating coverage."
        )
    if missing_actions:
        blockers.append("Actions not observed: " + ", ".join(missing_actions) + ".")
    if underrepresented_actions:
        blockers.append(
            "Actions below the 1% screening threshold: "
            + ", ".join(underrepresented_actions) + "."
        )
    if len(widths) > 1 or len(next_widths) > 1:
        blockers.append("Observation widths are inconsistent across transitions.")
    if transition_width_mismatches:
        blockers.append(
            f"{transition_width_mismatches} transitions have different current/next observation widths."
        )
    if never_available_features:
        blockers.append(
            "Features never available: " + ", ".join(never_available_features) + "."
        )
    if low_availability_features:
        blockers.append(
            "Features available in less than 5% of samples: "
            + ", ".join(low_availability_features) + "."
        )
    blockers.append("Inspect gameplay diversity and telemetry quality before training.")

    return {
        "path": str(path),
        "transition_count": total,
        "action_counts": action_counts,
        "action_fractions": action_fractions,
        "distinct_action_count": sum(count > 0 for count in action_counts.values()),
        "missing_actions": missing_actions,
        "underrepresented_actions": underrepresented_actions,
        "observation_widths": widths,
        "next_observation_widths": next_widths,
        "transition_width_mismatches": transition_width_mismatches,
        "unique_observation_count": len(set(state_keys)),
        "duplicate_observation_count": duplicates,
        "duplicate_observation_fraction": duplicates / total if total else 0.0,
        "feature_availability_fraction": (
            sum(feature_available_counts) / feature_slots
            if feature_slots and feature_width is not None else 0.0
        ),
        "feature_availability_by_name": feature_availability,
        "never_available_features": never_available_features,
        "low_availability_features": low_availability_features,
        "sequence_pair_count": len(sequence_pairs),
        "non_increasing_sequence_pairs": non_increasing,
        "sequence_gap_pairs": gaps,
        "reward_status": "unassigned",
        "training_ready": False,
        "training_blockers": blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect a neural Deltarune experience dataset without training."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze_dataset(args.dataset)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
