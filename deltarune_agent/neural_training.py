"""Supervised warm-start training for the numerical neural Deltarune policy.

This trainer learns to imitate actions present in recorded telemetry transitions.
It intentionally ignores reward, next-state, room text, screenshots, and any
developer-only oracle/calibration data. Validation is chronological by default
to reduce leakage from adjacent gameplay samples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random

from .actions import ACTIONS
from .experience import NeuralPolicySample, policy_samples_from_transitions, read_transitions
from .neural_env import DEFAULT_ENCODER
from .neural_policy import NeuralPolicyConfig, build_policy_value_network


@dataclass(frozen=True)
class NeuralTrainingConfig:
    epochs: int = 10
    batch_size: int = 128
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    validation_fraction: float = 0.2
    seed: int = 1337
    device: str = "auto"
    hidden_sizes: tuple[int, ...] = (128, 128)
    dropout: float = 0.0

    def __post_init__(self) -> None:
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.learning_rate <= 0:
            raise ValueError("learning_rate must be positive")
        if self.weight_decay < 0:
            raise ValueError("weight_decay must be non-negative")
        if not 0.0 < self.validation_fraction < 1.0:
            raise ValueError("validation_fraction must be between 0 and 1")
        if not self.hidden_sizes or any(size <= 0 for size in self.hidden_sizes):
            raise ValueError("hidden_sizes must contain positive widths")


def _resolve_device(torch, requested: str):
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return torch.device(requested)


def _split(samples: list[NeuralPolicySample], fraction: float):
    if len(samples) < 2:
        raise ValueError("at least two policy samples are required")
    validation_count = max(1, int(round(len(samples) * fraction)))
    validation_count = min(validation_count, len(samples) - 1)
    return samples[:-validation_count], samples[-validation_count:]


def _metrics(logits, targets, action_count: int) -> dict[str, object]:
    import torch

    predictions = logits.argmax(dim=1)
    correct = predictions.eq(targets)
    accuracy = float(correct.float().mean().item()) if len(targets) else 0.0
    per_action: dict[str, dict[str, float | int]] = {}
    for index, name in enumerate(ACTIONS):
        mask = targets == index
        count = int(mask.sum().item())
        per_action[name] = {
            "count": count,
            "accuracy": (
                float(correct[mask].float().mean().item()) if count else 0.0
            ),
        }
    return {
        "accuracy": accuracy,
        "sample_count": int(len(targets)),
        "per_action": per_action,
    }


def train_policy(
    dataset: Path,
    checkpoint: Path,
    *,
    config: NeuralTrainingConfig | None = None,
) -> dict[str, object]:
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, TensorDataset

    config = config or NeuralTrainingConfig()
    transitions = read_transitions(dataset)
    samples = policy_samples_from_transitions(transitions)
    if not samples:
        raise ValueError("dataset contains no transitions")

    width = len(samples[0].observation)
    if width != DEFAULT_ENCODER.width:
        raise ValueError(
            f"dataset observation width {width} does not match "
            f"current encoder width {DEFAULT_ENCODER.width}"
        )
    if any(len(sample.observation) != width for sample in samples):
        raise ValueError("dataset contains inconsistent observation widths")

    train_samples, validation_samples = _split(samples, config.validation_fraction)
    action_to_index = {name: index for index, name in enumerate(ACTIONS)}

    def tensors(items):
        x = torch.tensor(
            [(*item.observation, *item.available) for item in items],
            dtype=torch.float32,
        )
        y = torch.tensor(
            [action_to_index[item.action] for item in items],
            dtype=torch.long,
        )
        return x, y

    train_x, train_y = tensors(train_samples)
    validation_x, validation_y = tensors(validation_samples)

    generator = torch.Generator().manual_seed(config.seed)
    train_loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=min(config.batch_size, len(train_samples)),
        shuffle=True,
        generator=generator,
    )

    device = _resolve_device(torch, config.device)
    model = build_policy_value_network(
        width * 2,
        NeuralPolicyConfig(
            hidden_sizes=config.hidden_sizes,
            dropout=config.dropout,
        ),
    ).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )

    history: list[dict[str, object]] = []
    best_validation_loss = float("inf")
    best_state = None

    for epoch in range(1, config.epochs + 1):
        model.train()
        running_loss = 0.0
        seen = 0
        for batch_x, batch_y in train_loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            optimizer.zero_grad(set_to_none=True)
            output = model(batch_x)
            loss = F.cross_entropy(output["policy_logits"], batch_y)
            loss.backward()
            optimizer.step()
            count = len(batch_y)
            running_loss += float(loss.item()) * count
            seen += count

        model.eval()
        with torch.inference_mode():
            train_output = model(train_x.to(device))
            validation_output = model(validation_x.to(device))
            train_loss = float(
                F.cross_entropy(train_output["policy_logits"], train_y.to(device)).item()
            )
            validation_loss = float(
                F.cross_entropy(
                    validation_output["policy_logits"],
                    validation_y.to(device),
                ).item()
            )
            train_metrics = _metrics(
                train_output["policy_logits"], train_y.to(device), len(ACTIONS)
            )
            validation_metrics = _metrics(
                validation_output["policy_logits"],
                validation_y.to(device),
                len(ACTIONS),
            )

        epoch_record = {
            "epoch": epoch,
            "optimization_loss": running_loss / seen,
            "train_loss": train_loss,
            "validation_loss": validation_loss,
            "train": train_metrics,
            "validation": validation_metrics,
        }
        history.append(epoch_record)

        if validation_loss < best_validation_loss:
            best_validation_loss = validation_loss
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }

    if best_state is None:
        raise RuntimeError("training produced no checkpoint candidate")

    model.load_state_dict(best_state)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": "deltarune-neural-policy-v1",
            "model_state_dict": model.state_dict(),
            "encoder_width": DEFAULT_ENCODER.width,
            "combined_input_width": width * 2,
            "action_names": list(ACTIONS),
            "policy_config": {
                "hidden_sizes": list(config.hidden_sizes),
                "dropout": config.dropout,
            },
            "training_config": asdict(config),
            "dataset": str(dataset),
            "train_count": len(train_samples),
            "validation_count": len(validation_samples),
            "best_validation_loss": best_validation_loss,
            "history": history,
        },
        checkpoint,
    )

    return {
        "checkpoint": str(checkpoint),
        "device": str(device),
        "dataset_count": len(samples),
        "train_count": len(train_samples),
        "validation_count": len(validation_samples),
        "best_validation_loss": best_validation_loss,
        "best_validation": history[-1]["validation"],
        "history": history,
        "reward_status": "ignored_for_supervised_imitation",
        "validation_split": "chronological",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Train the numerical Deltarune neural policy from recorded actions."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/neural-policy.pt"))
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = train_policy(
        args.dataset,
        args.checkpoint,
        config=NeuralTrainingConfig(
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            validation_fraction=args.validation_fraction,
            seed=args.seed,
            device=args.device,
        ),
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
