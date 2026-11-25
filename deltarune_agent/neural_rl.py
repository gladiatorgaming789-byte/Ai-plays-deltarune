"""Reward-driven neural learning infrastructure.

This module performs offline actor-critic updates only on transitions carrying
real environment rewards. It refuses datasets whose rewards are still marked
as unassigned. Reward design therefore remains an explicit environment concern
rather than a hidden developer label.
"""

from __future__ import annotations

from pathlib import Path

from .actions import ACTIONS
from .experience import read_transitions
from .neural_policy import NeuralPolicyConfig, build_policy_value_network
from .neural_env import DEFAULT_ENCODER


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
) -> dict[str, object]:
    import torch
    import torch.nn.functional as F

    if epochs <= 0 or learning_rate <= 0 or not 0 < gamma <= 1:
        raise ValueError("invalid reinforcement-learning configuration")
    transitions = read_transitions(dataset)
    if not transitions:
        raise ValueError("dataset contains no transitions")

    if any(item.reward_status != "assigned" for item in transitions):
        raise ValueError(
            "dataset contains unassigned rewards; provide environment rewards before RL training"
        )

    device_obj = (
        torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if device == "auto"
        else torch.device(device)
    )
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    action_to_index = {name: i for i, name in enumerate(ACTIONS)}
    x = torch.tensor(
        [(*t.observation, *t.available) for t in transitions],
        dtype=torch.float32,
        device=device_obj,
    )
    next_x = torch.tensor(
        [(*t.next_observation, *t.next_available) for t in transitions],
        dtype=torch.float32,
        device=device_obj,
    )
    actions = torch.tensor([action_to_index[t.action] for t in transitions], dtype=torch.long, device=device_obj)
    rewards = torch.tensor([t.reward for t in transitions], dtype=torch.float32, device=device_obj)
    dones = torch.tensor([t.done for t in transitions], dtype=torch.float32, device=device_obj)

    model = build_policy_value_network(DEFAULT_ENCODER.combined_width, NeuralPolicyConfig()).to(device_obj)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    history = []
    for epoch in range(1, epochs + 1):
        model.train()
        out = model(x)
        with torch.no_grad():
            next_value = model(next_x)["value"]
            target = rewards + gamma * (1.0 - dones) * next_value
        log_probs = F.log_softmax(out["policy_logits"], dim=-1)
        selected_log_prob = log_probs.gather(1, actions.unsqueeze(1)).squeeze(1)
        advantage = (target - out["value"]).detach()
        policy_loss = -(selected_log_prob * advantage).mean()
        value_loss = F.smooth_l1_loss(out["value"], target)
        entropy = -(torch.softmax(out["policy_logits"], -1) * log_probs).sum(-1).mean()
        loss = policy_loss + value_weight * value_loss - entropy_weight * entropy
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        history.append({
            "epoch": epoch,
            "loss": float(loss.item()),
            "policy_loss": float(policy_loss.item()),
            "value_loss": float(value_loss.item()),
            "entropy": float(entropy.item()),
            "mean_reward": float(rewards.mean().item()),
        })

    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "format": "deltarune-neural-rl-v1",
        "model_state_dict": model.state_dict(),
        "encoder_width": DEFAULT_ENCODER.width,
        "combined_input_width": DEFAULT_ENCODER.combined_width,
        "action_names": list(ACTIONS),
        "algorithm": "one-step-actor-critic",
        "gamma": gamma,
        "history": history,
        "dataset": str(dataset),
    }, checkpoint)
    return {"checkpoint": str(checkpoint), "device": str(device_obj), "history": history}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Train the numerical Deltarune neural policy with environment rewards.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/neural-rl-policy.pt"))
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = train_actor_critic(
        args.dataset, args.checkpoint, epochs=args.epochs,
        learning_rate=args.learning_rate, gamma=args.gamma, device=args.device,
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0
