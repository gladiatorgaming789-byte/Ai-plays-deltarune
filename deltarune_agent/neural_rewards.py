"""Explicit, observable reward assignment for neural RL experiments.

The default reward is intentionally a small bootstrap signal based only on
observed player displacement and action cost. It is not claimed to represent
game completion. A future task-specific reward should replace this function
once its evidence and failure modes are validated.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import math

from .experience import NeuralTransition, read_transitions, write_transitions


def movement_reward(
    transition: NeuralTransition,
    *,
    displacement_scale: float = 0.01,
    action_cost: float = 0.001,
) -> float:
    if displacement_scale < 0 or action_cost < 0:
        raise ValueError("reward scales must be non-negative")
    width = len(transition.observation)
    # The fixed encoder stores normalized x/y at positions 0 and 1. The
    # availability mask immediately follows the observation vector.
    if width < 2:
        return 0.0
    dx = transition.next_observation[0] - transition.observation[0]
    dy = transition.next_observation[1] - transition.observation[1]
    distance = math.hypot(dx, dy)
    return float(displacement_scale * distance - action_cost)


def assign_movement_rewards(
    source: Path,
    destination: Path,
    *,
    displacement_scale: float = 0.01,
    action_cost: float = 0.001,
) -> dict[str, object]:
    transitions = read_transitions(source)
    rewarded = [
        replace(
            item,
            reward=movement_reward(
                item,
                displacement_scale=displacement_scale,
                action_cost=action_cost,
            ),
            reward_status="assigned",
        )
        for item in transitions
    ]
    write_transitions(destination, rewarded)
    return {
        "source": str(source),
        "destination": str(destination),
        "transition_count": len(rewarded),
        "reward_contract": "observable_movement_v1",
        "warning": "bootstrap movement reward; not a game-completion reward",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Assign the explicit observable bootstrap reward to a neural dataset."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--displacement-scale", type=float, default=0.01)
    parser.add_argument("--action-cost", type=float, default=0.001)
    args = parser.parse_args(argv)
    report = assign_movement_rewards(
        args.source,
        args.destination,
        displacement_scale=args.displacement_scale,
        action_cost=args.action_cost,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
