"""Evaluation for the supervised numerical Deltarune neural policy.

Evaluation is deliberately performed on the same chronological holdout contract
used by supervised training. No training step occurs here and no reward,
screenshot, room text, or developer-only oracle data is consumed.
"""

from __future__ import annotations

import json
from pathlib import Path

from .actions import ACTIONS
from .experience import NeuralPolicySample, policy_samples_from_transitions, read_transitions
from .neural_env import DEFAULT_ENCODER
from .neural_policy import NeuralPolicyConfig, build_policy_value_network
from .neural_training import _split


def _resolve_device(torch, requested: str):
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return torch.device(requested)


def _load_checkpoint(torch, checkpoint: Path, device):
    payload = torch.load(checkpoint, map_location=device, weights_only=True)
    if not isinstance(payload, dict) or "model_state_dict" not in payload:
        raise ValueError("checkpoint is not a deltarune-neural-policy-v1 checkpoint")
    if payload.get("format") != "deltarune-neural-policy-v1":
        raise ValueError(f"unsupported neural checkpoint format: {payload.get('format')!r}")
    if payload.get("encoder_width") != DEFAULT_ENCODER.width:
        raise ValueError("checkpoint encoder width does not match the current encoder")
    config_raw = payload.get("policy_config", {})
    config = NeuralPolicyConfig(
        hidden_sizes=tuple(config_raw.get("hidden_sizes", (128, 128))),
        dropout=float(config_raw.get("dropout", 0.0)),
    )
    model = build_policy_value_network(DEFAULT_ENCODER.combined_width, config).to(device)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    return model, payload


def _metrics(logits, targets):
    import torch

    probabilities = torch.softmax(logits, dim=1)
    predictions = probabilities.argmax(dim=1)
    correct = predictions.eq(targets)
    confidence = probabilities.gather(1, predictions.unsqueeze(1)).squeeze(1)
    accuracy = float(correct.float().mean().item()) if len(targets) else 0.0
    nll = float(
        torch.nn.functional.cross_entropy(logits, targets, reduction="mean").item()
    )
    brier = float(
        ((probabilities - torch.nn.functional.one_hot(targets, num_classes=len(ACTIONS)).float()) ** 2)
        .sum(dim=1)
        .mean()
        .item()
    )
    confusion = [[0 for _ in ACTIONS] for _ in ACTIONS]
    for actual, predicted in zip(targets.tolist(), predictions.tolist()):
        confusion[actual][predicted] += 1

    per_action = {}
    for index, name in enumerate(ACTIONS):
        mask = targets == index
        count = int(mask.sum().item())
        per_action[name] = {
            "count": count,
            "accuracy": float(correct[mask].float().mean().item()) if count else 0.0,
            "mean_confidence": float(confidence[mask].mean().item()) if count else 0.0,
        }

    calibration = []
    for bucket in range(10):
        low = bucket / 10
        high = (bucket + 1) / 10
        mask = (confidence >= low) & (
            confidence <= high if bucket == 9 else confidence < high
        )
        count = int(mask.sum().item())
        calibration.append({
            "lower": low,
            "upper": high,
            "count": count,
            "accuracy": float(correct[mask].float().mean().item()) if count else None,
            "mean_confidence": float(confidence[mask].mean().item()) if count else None,
        })

    return {
        "accuracy": accuracy,
        "nll": nll,
        "brier_score": brier,
        "sample_count": int(len(targets)),
        "confusion_matrix": {
            "actions": list(ACTIONS),
            "rows_actual_columns_predicted": confusion,
        },
        "per_action": per_action,
        "confidence": {
            "mean": float(confidence.mean().item()) if len(confidence) else 0.0,
            "min": float(confidence.min().item()) if len(confidence) else 0.0,
            "max": float(confidence.max().item()) if len(confidence) else 0.0,
        },
        "calibration": calibration,
    }


def evaluate_policy(
    dataset: Path,
    checkpoint: Path,
    *,
    validation_fraction: float = 0.2,
    device: str = "auto",
) -> dict[str, object]:
    import torch

    transitions = read_transitions(dataset)
    samples = policy_samples_from_transitions(transitions)
    if not samples:
        raise ValueError("dataset contains no transitions")
    width = len(samples[0].observation)
    if width != DEFAULT_ENCODER.width or any(
        len(sample.observation) != width for sample in samples
    ):
        raise ValueError("dataset observation width does not match the current encoder")

    _train_samples, validation_samples = _split(samples, validation_fraction)
    x = torch.tensor(
        [(*sample.observation, *sample.available) for sample in validation_samples],
        dtype=torch.float32,
    )
    targets = torch.tensor(
        [{name: index for index, name in enumerate(ACTIONS)}[sample.action]
         for sample in validation_samples],
        dtype=torch.long,
    )

    resolved = _resolve_device(torch, device)
    model, payload = _load_checkpoint(torch, checkpoint, resolved)
    with torch.inference_mode():
        output = model(x.to(resolved))
    metrics = _metrics(output["policy_logits"], targets.to(resolved))

    report = {
        "format": "deltarune-neural-policy-evaluation-v1",
        "dataset": str(dataset),
        "checkpoint": str(checkpoint),
        "device": str(resolved),
        "dataset_count": len(samples),
        "validation_count": len(validation_samples),
        "validation_fraction": validation_fraction,
        "validation_split": "chronological",
        "checkpoint_best_validation_loss": payload.get("best_validation_loss"),
        "metrics": metrics,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate a numerical Deltarune neural policy on its chronological holdout."
    )
    parser.add_argument("dataset", type=Path)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = evaluate_policy(
        args.dataset,
        args.checkpoint,
        validation_fraction=args.validation_fraction,
        device=args.device,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
