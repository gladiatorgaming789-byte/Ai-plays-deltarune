"""Temporal neural policy and sequence training for the numerical Deltarune agent.

The model consumes short sequences of the fixed numerical observation contract.
It has no vision, route tables, story text, or developer-only oracle inputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .actions import ACTIONS
from .experience import NeuralPolicySample, NeuralTransition, read_transitions
from .neural_env import DEFAULT_ENCODER


@dataclass(frozen=True)
class NeuralTemporalConfig:
    sequence_length: int = 8
    hidden_size: int = 128
    layers: int = 1
    dropout: float = 0.0
    epochs: int = 10
    batch_size: int = 64
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    validation_fraction: float = 0.2
    seed: int = 1337
    device: str = "auto"

    def __post_init__(self) -> None:
        if self.sequence_length < 2:
            raise ValueError("sequence_length must be at least 2")
        if self.hidden_size <= 0 or self.layers <= 0:
            raise ValueError("hidden_size and layers must be positive")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if self.epochs <= 0 or self.batch_size <= 0:
            raise ValueError("epochs and batch_size must be positive")
        if self.learning_rate <= 0 or self.weight_decay < 0:
            raise ValueError("invalid optimizer settings")
        if not 0.0 < self.validation_fraction < 1.0:
            raise ValueError("validation_fraction must be between 0 and 1")


def _resolve_device(torch, requested: str):
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return torch.device(requested)


def _contiguous_groups(transitions: list[NeuralTransition]) -> list[list[NeuralTransition]]:
    groups: list[list[NeuralTransition]] = []
    current: list[NeuralTransition] = []
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


def sequence_samples_from_transitions(
    transitions: list[NeuralTransition],
    sequence_length: int,
) -> list[tuple[tuple[float, ...], tuple[float, ...], str]]:
    """Build causal windows ending at each transition's action."""
    if sequence_length < 2:
        raise ValueError("sequence_length must be at least 2")
    result = []
    width = DEFAULT_ENCODER.width
    for group in _contiguous_groups(transitions):
        if len(group) < sequence_length:
            continue
        for end in range(sequence_length - 1, len(group)):
            window = group[end - sequence_length + 1:end + 1]
            observations = tuple(
                value
                for item in window
                for value in (*item.observation, *item.available)
            )
            if len(observations) != sequence_length * width * 2:
                raise ValueError("sequence sample width mismatch")
            result.append((observations, tuple(), window[-1].action))
    return result


def build_temporal_policy_network(input_width: int, config: NeuralTemporalConfig):
    import torch.nn as nn

    class TemporalPolicyValueNetwork(nn.Module):
        def __init__(self):
            super().__init__()
            self.input_width = input_width
            self.encoder = nn.GRU(
                input_size=input_width,
                hidden_size=config.hidden_size,
                num_layers=config.layers,
                batch_first=True,
                dropout=config.dropout if config.layers > 1 else 0.0,
            )
            self.policy_head = nn.Linear(config.hidden_size, len(ACTIONS))
            self.value_head = nn.Linear(config.hidden_size, 1)

        def forward(self, x):
            output, _hidden = self.encoder(x)
            features = output[:, -1, :]
            return {
                "policy_logits": self.policy_head(features),
                "value": self.value_head(features).squeeze(-1),
            }

    return TemporalPolicyValueNetwork()


def initialize_temporal_policy_checkpoint(
    checkpoint: Path,
    *,
    config: NeuralTemporalConfig | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Create a fresh, randomly initialized temporal policy checkpoint.

    This deliberately does not require a dataset. The resulting checkpoint is
    structurally compatible with the live neural controller and is intended as
    a starting point for later supervised or RL training.
    """
    import torch

    config = config or NeuralTemporalConfig(epochs=1, batch_size=1)
    if checkpoint.exists() and not overwrite:
        raise FileExistsError(f"checkpoint already exists: {checkpoint}")

    torch.manual_seed(config.seed)
    model = build_temporal_policy_network(DEFAULT_ENCODER.combined_width, config)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "format": "deltarune-neural-temporal-policy-v1",
            "model_state_dict": model.state_dict(),
            "encoder_width": DEFAULT_ENCODER.width,
            "combined_input_width": DEFAULT_ENCODER.combined_width,
            "action_names": list(ACTIONS),
            "temporal_config": asdict(config),
            "dataset": None,
            "sequence_count": 0,
            "best_validation_loss": None,
            "history": [],
            "initialization": "fresh_random",
        },
        checkpoint,
    )
    return {
        "checkpoint": str(checkpoint),
        "device": "cpu",
        "sequence_count": 0,
        "sequence_length": config.sequence_length,
        "initialization": "fresh_random",
    }


def train_temporal_policy(
    dataset: Path,
    checkpoint: Path,
    *,
    config: NeuralTemporalConfig | None = None,
) -> dict[str, object]:
    import torch
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, TensorDataset

    config = config or NeuralTemporalConfig()
    transitions = read_transitions(dataset)
    samples = sequence_samples_from_transitions(transitions, config.sequence_length)
    if len(samples) < 2:
        raise ValueError("dataset does not contain enough contiguous sequences")

    action_to_index = {name: index for index, name in enumerate(ACTIONS)}
    input_width = DEFAULT_ENCODER.combined_width
    x = torch.tensor(
        [list(sample[0]) for sample in samples],
        dtype=torch.float32,
    ).reshape(len(samples), config.sequence_length, input_width)
    y = torch.tensor(
        [action_to_index[sample[2]] for sample in samples],
        dtype=torch.long,
    )
    validation_count = max(1, int(round(len(samples) * config.validation_fraction)))
    validation_count = min(validation_count, len(samples) - 1)
    train_x, validation_x = x[:-validation_count], x[-validation_count:]
    train_y, validation_y = y[:-validation_count], y[-validation_count:]

    device = _resolve_device(torch, config.device)
    generator = torch.Generator().manual_seed(config.seed)
    loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=min(config.batch_size, len(train_y)),
        shuffle=True,
        generator=generator,
    )
    model = build_temporal_policy_network(input_width, config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    best_loss = float("inf")
    best_state = None
    history = []

    for epoch in range(1, config.epochs + 1):
        model.train()
        for batch_x, batch_y in loader:
            optimizer.zero_grad(set_to_none=True)
            output = model(batch_x.to(device))
            loss = F.cross_entropy(output["policy_logits"], batch_y.to(device))
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.inference_mode():
            out = model(validation_x.to(device))
            val_loss = float(F.cross_entropy(out["policy_logits"], validation_y.to(device)).item())
            val_accuracy = float(
                (out["policy_logits"].argmax(1) == validation_y.to(device)).float().mean().item()
            )
        history.append({"epoch": epoch, "validation_loss": val_loss, "validation_accuracy": val_accuracy})
        if val_loss < best_loss:
            best_loss = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is None:
        raise RuntimeError("temporal training produced no checkpoint")
    model.load_state_dict(best_state)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "format": "deltarune-neural-temporal-policy-v1",
        "model_state_dict": model.state_dict(),
        "encoder_width": DEFAULT_ENCODER.width,
        "combined_input_width": input_width,
        "action_names": list(ACTIONS),
        "temporal_config": asdict(config),
        "dataset": str(dataset),
        "sequence_count": len(samples),
        "best_validation_loss": best_loss,
        "history": history,
    }, checkpoint)
    return {
        "checkpoint": str(checkpoint),
        "device": str(device),
        "sequence_count": len(samples),
        "train_count": len(train_x),
        "validation_count": len(validation_x),
        "best_validation_loss": best_loss,
        "validation_accuracy": history[-1]["validation_accuracy"],
        "sequence_length": config.sequence_length,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Train or initialize the temporal neural Deltarune policy.")
    parser.add_argument("dataset", type=Path, nargs="?")
    parser.add_argument("--fresh", action="store_true", help="create a fresh randomly initialized checkpoint without a dataset")
    parser.add_argument("--checkpoint", type=Path, default=Path("runs/neural-temporal-policy.pt"))
    parser.add_argument("--sequence-length", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.fresh:
        if args.dataset is None:
            checkpoint = args.checkpoint
        else:
            checkpoint = args.dataset
        report = initialize_temporal_policy_checkpoint(
            checkpoint,
            config=NeuralTemporalConfig(
                sequence_length=args.sequence_length,
                epochs=args.epochs,
                batch_size=args.batch_size,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                validation_fraction=args.validation_fraction,
                seed=args.seed,
                device=args.device,
            ),
        )
    else:
        if args.dataset is None:
            parser.error("dataset is required unless --fresh is used")
        report = train_temporal_policy(
        args.dataset,
        args.checkpoint,
        config=NeuralTemporalConfig(
            sequence_length=args.sequence_length,
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            validation_fraction=args.validation_fraction,
            seed=args.seed,
            device=args.device,
        ),
    )
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
