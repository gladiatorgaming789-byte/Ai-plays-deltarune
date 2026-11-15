"""Developer-only adjudicated visual-exit dataset builder.

Labels are never inferred from a missing room transition. Candidate records
default to unknown until a developer explicitly adjudicates them using
recorded evidence. This module must not be imported by gameplay code.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .visual_exit_benchmark import VisualExitCandidate

DATASET_VERSION = 1
VALID_STATUSES = {"confirmed_exit", "tested_non_exit", "unreachable", "unknown"}
SUPERVISED_STATUSES = {"confirmed_exit", "tested_non_exit"}


@dataclass(frozen=True)
class ExitAdjudication:
    candidate_id: str
    status: str = "unknown"
    evidence: str = ""
    tested_step: int | None = None
    reviewer: str = ""


def build_adjudication_template(
    candidates: Sequence[VisualExitCandidate],
) -> list[dict[str, object]]:
    """Create editable records; every candidate begins explicitly unknown."""
    return [
        {
            "candidate_id": candidate.candidate_id,
            "status": "unknown",
            "evidence": "",
            "tested_step": None,
            "reviewer": "",
        }
        for candidate in candidates
    ]


def load_adjudications(path: Path) -> list[ExitAdjudication]:
    """Read JSONL adjudications. Missing files mean all candidates are unknown."""
    if not path.is_file():
        return []
    result: list[ExitAdjudication] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Adjudication line {line_number} must be a JSON object")
        candidate_id = str(row.get("candidate_id") or "").strip()
        status = str(row.get("status") or "unknown").strip()
        if not candidate_id:
            raise ValueError(f"Adjudication line {line_number} has no candidate_id")
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Unsupported status {status!r} on line {line_number}; "
                f"expected one of {sorted(VALID_STATUSES)}"
            )
        evidence = str(row.get("evidence") or "").strip()
        if status in SUPERVISED_STATUSES and not evidence:
            raise ValueError(
                f"{status} requires explicit evidence on line {line_number}"
            )
        raw_step = row.get("tested_step")
        try:
            tested_step = int(raw_step) if raw_step is not None else None
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid tested_step on line {line_number}") from exc
        result.append(ExitAdjudication(
            candidate_id=candidate_id,
            status=status,
            evidence=evidence,
            tested_step=tested_step,
            reviewer=str(row.get("reviewer") or "").strip(),
        ))
    ids = [item.candidate_id for item in result]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate adjudications for candidate IDs: {duplicates}")
    return result


def build_tested_exit_dataset(
    candidates: Sequence[VisualExitCandidate],
    adjudications: Sequence[ExitAdjudication],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Join explicit labels to observed candidates; never infer a negative."""
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("Candidate IDs must be unique before dataset construction")
    adjudication_ids = [item.candidate_id for item in adjudications]
    if len(set(adjudication_ids)) != len(adjudication_ids):
        duplicates = sorted({item for item in adjudication_ids if adjudication_ids.count(item) > 1})
        raise ValueError(f"Duplicate adjudications for candidate IDs: {duplicates}")
    for item in adjudications:
        if item.status not in VALID_STATUSES:
            raise ValueError(f"Unsupported status {item.status!r} for candidate {item.candidate_id!r}")
        if item.status in SUPERVISED_STATUSES and not item.evidence.strip():
            raise ValueError(f"{item.status} requires explicit evidence for candidate {item.candidate_id!r}")
    labels = {item.candidate_id: item for item in adjudications}
    unknown_ids = sorted(set(labels) - set(candidate_by_id))
    if unknown_ids:
        raise ValueError(f"Adjudications reference unknown candidates: {unknown_ids}")

    rows: list[dict[str, object]] = []
    status_counts: Counter[str] = Counter()
    for candidate in candidates:
        label = labels.get(candidate.candidate_id, ExitAdjudication(candidate.candidate_id))
        status_counts[label.status] += 1
        row: dict[str, object] = {
            "candidate_id": candidate.candidate_id,
            "room": candidate.room,
            "region": list(candidate.region),
            "first_seen_step": candidate.step,
            "initial_state": candidate.state,
            "state_history": [list(item) for item in candidate.state_history],
            "source": candidate.source,
            "visual_score": candidate.score,
            "anchor_world": [candidate.anchor_x, candidate.anchor_y],
            "label": label.status,
            "evidence": label.evidence,
            "tested_step": label.tested_step,
            "reviewer": label.reviewer,
            "supervised_example": label.status in SUPERVISED_STATUSES,
        }
        rows.append(row)

    summary = {
        "dataset_version": DATASET_VERSION,
        "candidate_count": len(candidates),
        "label_counts": {status: status_counts.get(status, 0) for status in sorted(VALID_STATUSES)},
        "supervised_example_count": sum(
            1 for row in rows if row["supervised_example"]
        ),
        "excluded_unvalidated_count": status_counts.get("unknown", 0),
        "excluded_unreachable_count": status_counts.get("unreachable", 0),
        "label_policy": {
            "confirmed_exit": "Explicitly observed to lead to a room transition.",
            "tested_non_exit": "Explicitly tested and adjudicated not to be an exit; evidence is required.",
            "unreachable": "Could not be tested; excluded from supervised labels.",
            "unknown": "Not adjudicated; excluded from supervised labels.",
        },
        "negative_labels_inferred_from_absence": False,
    }
    return rows, summary


def write_dataset_artifacts(
    output_directory: Path,
    candidates: Sequence[VisualExitCandidate],
    rows: Sequence[Mapping[str, object]],
    summary: Mapping[str, object],
) -> dict[str, Path]:
    """Write template once, plus reproducible dataset and summary artifacts."""
    output_directory.mkdir(parents=True, exist_ok=True)
    template_path = output_directory / "exit_adjudications.template.jsonl"
    if not template_path.exists():
        template = build_adjudication_template(candidates)
        template_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in template),
            encoding="utf-8",
        )
    dataset_path = output_directory / "tested_exit_dataset.jsonl"
    dataset_path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary_path = output_directory / "tested_exit_dataset_summary.json"
    summary_path.write_text(
        json.dumps({"dataset_version": DATASET_VERSION, **dict(summary)}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "template": template_path,
        "dataset": dataset_path,
        "summary": summary_path,
    }
