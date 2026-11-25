"""Reward-driven neural reinforcement-learning training.

This module trains the same recurrent numerical policy used by neural live
control. It consumes only observable numerical transitions and explicitly
assigned environment rewards. It never imports gameplay routes, story text,
screenshots, or developer-only benchmark data.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from .actions import ACTIONS
from .experience import read_transitions
from .neural_env import DEFAULT_ENCODER
from .neural_temporal import (
    NeuralTemporalConfig,
    build_temporal_policy_network,
    sequence_samples_from_transitions,
)


def _resolve_device(torch, requested: str):
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return torch.device(requested)


def _contiguous_groups_for_rewards(transitions):
    groups = []
    current = []
    previous = None
    for item in transitions:
        if item.source_sequence is None or item.next_source_sequence is None:
            current = []
            previous = None
            continue
        if previous is None or item.source_sequence == previous + 1:
            current.append(item)
        else:
            if current:
                groups.append(current)
            current = [item]
        previous = item.source_sequence
    if current:
        groups.append(current)
    return groups


def train_actor_critic(
    dataset: Path,
    checkpoint: Path,
    *,
    epochs: int = 10,
    learning_rate: float = 3e-4,
    gamma: float = 0.99,
    value_weight: float = 0.5,
    entropy_weight: float = 0.01,
    device: str = "auto",
    sequence_length: int = 8,
    temporal_checkpoint: Path | None = None,
) -> dict[str, object]:
    """Train the recurrent policy/value network with one-step actor-critic.

    A chronological sequence split is used so validation remains future-only.
    If temporal_checkpoint is supplied, its learned recurrent/policy weights
    are used as the warm start. Otherwise a fresh recurrent network is
    created. The resulting checkpoint is directly loadable by
    NeuralTemporalAgent.
    """
    import torch
    import torch.nn.functional as F

    if epochs <= 0 or learning_rate <= 0 or not 0 < gamma <= 1:
        raise ValueError("invalid reinforcement-learning configuration")
    if sequence_length < 2:
        raise ValueError("sequence_length must be at least 2")

    transitions = read_transitions(dataset)
    if not transitions:
        raise ValueError("dataset contains no transitions")
    if any(item.reward_status != "assigned" for item in transitions):
        raise ValueError(
            "dataset contains unassigned rewards; provide environment rewards before RL training"
        )

    samples = sequence_samples_from_transitions(transitions, sequence_length)
    if len(samples) < 2:
        raise ValueError("dataset does not contain enough contiguous sequences")

    groups = _contiguous_groups_for_rewards(transitions)
    final_transitions = [
        group[end]
        for group in groups
        for end in range(sequence_length - 1, len(group))
    ]
    if len(final_transitions) != len(samples):
        raise RuntimeError("reward sequence alignment mismatch")

    input_width = DEFAULT_ENCODER.combined_width
    x = torch.tensor(
        [list(sample[0]) for sample in samples],
        dtype=torch.float32,
    ).reshape(len(samples), sequence_length, input_width)
    action_to_index = {name: i for i, name in enumerate(ACTIONS)}
    actions = torch.tensor(
        [action_to_index[sample[2]] for sample in samples],
        dtype=torch.long,
    )
    rewards = torch.tensor(
        [item.reward for item in final_transitions],
        dtype=torch.float32,
    )
    dones = torch.tensor(
        [1.0 if item.done else 0.0 for item in final_transitions],
        dtype=torch.float32,
    )

    validation_count = max(1, int(round(len(samples) * 0.2)))
    validation_count = min(validation_count, len(samples) - 1)
    train_slice = slice(None, -validation_count)
    validation_slice = slice(-validation_count, None)

    device_obj = _resolve_device(torch, device)
    config = NeuralTemporalConfig(
        sequence_length=sequence_length,
        epochs=epochs,
        batch_size=64,
        learning_rate=learning_rate,
        device=device,
    )
    model = build_temporal_policy_network(input_width, config).to(device_obj)

    if temporal_checkpoint is not None:
        payload = torch.load(temporal_checkpoint, map_location=device_obj, weights_only=True)
        if not isinstance(payload, dict):
            raise ValueError("temporal checkpoint is invalid")
        if payload.get("format") != "deltarune-neural-temporal-policy-v1":
            raise ValueError("temporal_checkpoint is not a temporal policy checkpoint")
        checkpoint_config = payload.get("temporal_config", {})
        if int(checkpoint_config.get("sequence_length", sequence_length)) != sequence_length:
            raise ValueError("sequence_length does not match temporal checkpoint")
        model.load_state_dict(payload["model_state_dict"])

    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    history = []
    best_loss = float("inf")
    best_state = None

    train_x, validation_x = x[train_slice], x[validation_slice]
    train_actions, validation_actions = actions[train_slice], actions[validation_slice]
    train_rewards, validation_rewards = rewards[train_slice], rewards[validation_slice]
    train_dones, validation_dones = dones[train_slice], dones[validation_slice]

    for epoch in range(1, epochs + 1):
        model.train()
        output = model(train_x.to(device_obj))

        # Bootstrap from the next real chronological sequence. The final
        # training sequence has no later state in this split, so it uses zero.
        with torch.no_grad():
            if len(train_x) > 1:
                next_train = torch.cat((train_x[1:], train_x[-1:]), dim=0)
                bootstrap = model(next_train.to(device_obj))["value"].detach()
            else:
                bootstrap = torch.zeros_like(train_rewards.to(device_obj))
        target = (
            train_rewards.to(device_obj)
            + gamma * (1.0 - train_dones.to(device_obj)) * bootstrap
        )

        log_probs = F.log_softmax(output["policy_logits"], dim=-1)
        selected_log_prob = log_probs.gather(
            1, train_actions.to(device_obj).unsqueeze(1)
        ).squeeze(1)
        advantage = (target - output["value"]).detach()
        policy_loss = -(selected_log_prob * advantage).mean()
        value_loss = F.smooth_l1_loss(output["value"], target)
        entropy = -(
            torch.softmax(output["policy_logits"], -1) * log_probs
        ).sum(-1).mean()
        loss = policy_loss + value_weight * value_loss - entropy_weight * entropy

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        model.eval()
        with torch.inference_mode():
            val_output = model(validation_x.to(device_obj))
            val_probs = torch.softmax(val_output["policy_logits"], dim=-1)
            val_accuracy = float(
                (
                    val_probs.argmax(1) == validation_actions.to(device_obj)
                ).float().mean().item()
            )
            val_nll = float(
                F.cross_entropy(
                    val_output["policy_logits"],
                    validation_actions.to(device_obj),
                ).item()
            )
            val_reward = float(validation_rewards.mean().item())

        history.append(
            {
                "epoch": epoch,
                "loss": float(loss.item()),
                "policy_loss": float(policy_loss.item()),
                "value_loss": float(value_loss.item()),
                "entropy": float(entropy.item()),
                "mean_train_reward": float(train_rewards.mean().item()),
                "mean_validation_reward": val_reward,
                "validation_nll": val_nll,
                "validation_accuracy": val_accuracy,
            }
        )
        if val_nll < best_loss:
            best_loss = val_nll
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }

    if best_state is None:
        raise RuntimeError("reinforcement-learning training produced no checkpoint")

    model.load_state_dict(best_state)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": "deltarune-neural-rl-v2",
            "model_state_dict": model.state_dict(),
            "encoder_width": DEFAULT_ENCODER.width,
            "combined_input_width": input_width,
            "action_names": list(ACTIONS),
            "algorithm": "recurrent-one-step-actor-critic",
            "gamma": gamma,
            "temporal_config": asdict(config),
            "dataset": str(dataset),
            "temporal_warm_start": (
                str(temporal_checkpoint) if temporal_checkpoint else None
            ),
            "sequence_count": len(samples),
            "best_validation_nll": best_loss,
            "history": history,
        },
        checkpoint,
    )

    return {
        "checkpoint": str(checkpoint),
        "device": str(device_obj),
        "algorithm": "recurrent-one-step-actor-critic",
        "sequence_count": len(samples),
        "train_count": len(train_x),
        "validation_count": len(validation_x),
        "best_validation_nll": best_loss,
        "validation_accuracy": history[-1]["validation_accuracy"],
        "temporal_warm_start": (
            str(temporal_checkpoint) if temporal_checkpoint else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Train the recurrent numerical Deltarune neural policy with environment rewards."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("runs/neural-rl-policy.pt"),
    )
    parser.add_argument("--temporal-checkpoint", type=Path)
    parser.add_argument("--sequence-length", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = train_actor_critic(
        args.dataset,
        args.checkpoint,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        gamma=args.gamma,
        device=args.device,
        sequence_length=args.sequence_length,
        temporal_checkpoint=args.temporal_checkpoint,
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
