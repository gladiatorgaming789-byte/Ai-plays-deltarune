"""Quality summary for a collected neural experience JSONL dataset."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from .experience import read_transitions


def analyze_dataset(path: Path) -> dict[str, object]:
    transitions = read_transitions(path)
    actions = Counter(item.action for item in transitions)
    widths = sorted({len(item.observation) for item in transitions})
    state_keys = [item.observation for item in transitions]
    duplicates = len(state_keys) - len(set(state_keys))
    total_slots = sum(len(item.available) for item in transitions)
    available_slots = sum(sum(item.available) for item in transitions)
    sequence_pairs = [
        (item.source_sequence, item.next_source_sequence)
        for item in transitions
        if item.source_sequence is not None
        and item.next_source_sequence is not None
    ]
    non_increasing = sum(current <= previous for previous, current in sequence_pairs)
    gaps = sum(current - previous > 1 for previous, current in sequence_pairs)
    return {
        "path": str(path),
        "transition_count": len(transitions),
        "action_counts": dict(sorted(actions.items())),
        "distinct_action_count": len(actions),
        "observation_widths": widths,
        "unique_observation_count": len(set(state_keys)),
        "duplicate_observation_count": duplicates,
        "duplicate_observation_fraction": (
            duplicates / len(transitions) if transitions else 0.0
        ),
        "feature_availability_fraction": (
            available_slots / total_slots if total_slots else 0.0
        ),
        "sequence_pair_count": len(sequence_pairs),
        "non_increasing_sequence_pairs": non_increasing,
        "sequence_gap_pairs": gaps,
        "reward_status": "unassigned",
        "training_ready": False,
        "training_blockers": [
            "Rewards are intentionally unassigned; define and validate an outcome-based reward model first.",
            "Inspect action balance and observation quality before training.",
        ],
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
        args.output.write_text(rendered + "\\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
