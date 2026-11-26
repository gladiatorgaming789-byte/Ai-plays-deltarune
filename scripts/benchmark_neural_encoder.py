#!/usr/bin/env python3
"""Cross-language parity and throughput check for the R1 neural encoder.

This intentionally benchmarks a persistent Rust JSONL process rather than a
single subprocess per observation, so process startup does not dominate the
measurement. The comparison is against the existing Python reference encoder.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import asdict

from deltarune_agent.neural_env import NeuralObservationEncoder
from deltarune_agent.telemetry import TelemetrySample


def sample() -> TelemetrySample:
    return TelemetrySample(
        mode="overworld",
        room_id=7,
        room_name="room_home",
        x=120.0,
        y=42.0,
        object_name="obj_mainchara",
        received_at=10.0,
        version=9,
        hspeed=4.0,
        vspeed=0.0,
        speed=4.0,
        direction=0.0,
        player_facing_direction="right",
        player_x=120.0,
        player_y=42.0,
        player_foot_x=120.0,
        player_foot_y=50.0,
        room_width=640.0,
        room_height=480.0,
        camera_x=32.0,
        camera_y=16.0,
        camera_width=320.0,
        camera_height=240.0,
        player_bbox_left=112.0,
        player_bbox_top=30.0,
        player_bbox_right=128.0,
        player_bbox_bottom=50.0,
        sprite_width=40.0,
        sprite_height=56.0,
        image_xscale=1.0,
        image_yscale=1.0,
        room_speed=30.0,
        fps=60.0,
        game_time_ms=5000.0,
        sample_delta_x=4.0,
        sample_delta_y=0.0,
        sample_interval_ms=33.3,
        interaction_state=0,
        player_controlled=True,
        visible=True,
        packet_sequence=12,
    )


def rust_request(s: TelemetrySample) -> dict[str, object]:
    raw = asdict(s)
    raw.pop("received_at", None)
    return {
        "telemetry": raw,
        "previous_action": "right",
        "previous_action_duration": 0.1,
    }


def compare(a: dict[str, object], b: dict[str, object]) -> None:
    for key in ("values", "available", "feature_names"):
        if a[key] != b[key]:
            if key == "values":
                for i, (left, right) in enumerate(zip(a[key], b[key])):
                    if left != right and not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
                        raise AssertionError(f"{key}[{i}] differs: Python={left!r}, Rust={right!r}")
                if len(a[key]) != len(b[key]):
                    raise AssertionError(f"{key} widths differ")
            else:
                raise AssertionError(f"{key} differs")
    if a["source_sequence"] != b["source_sequence"]:
        raise AssertionError("source_sequence differs")
    if a["room_id"] != b["room_id"] or a["room_name"] != b["room_name"]:
        raise AssertionError("room metadata differs")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path("rust/deltarune_runtime/target/release/encode-observation"),
    )
    args = parser.parse_args()

    binary = args.binary
    if os.name == "nt" and binary.suffix != ".exe":
        binary = binary.with_suffix(".exe")
    if not binary.exists():
        raise SystemExit(
            f"Rust encoder not found: {binary}\n"
            "Build it first with: cargo build --release --bin encode-observation"
        )

    encoder = NeuralObservationEncoder()
    s = sample()
    py = encoder.encode(s, previous_action="right", previous_action_duration=0.1).as_dict()
    request = rust_request(s)
    line = json.dumps(request, separators=(",", ":")) + "\n"

    with binary.open() if False else subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(line)
        proc.stdin.flush()
        first = json.loads(proc.stdout.readline())
        compare(py, first)

        py_start = time.perf_counter()
        py_last = None
        for _ in range(args.iterations):
            py_last = encoder.encode(
                s, previous_action="right", previous_action_duration=0.1
            ).as_dict()
        py_elapsed = time.perf_counter() - py_start

        rust_start = time.perf_counter()
        for _ in range(args.iterations):
            proc.stdin.write(line)
        proc.stdin.flush()
        rust_results = []
        for _ in range(args.iterations):
            rust_results.append(json.loads(proc.stdout.readline()))
        rust_elapsed = time.perf_counter() - rust_start

        proc.stdin.close()
        proc.wait(timeout=5)
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise SystemExit(f"Rust encoder exited with {proc.returncode}: {stderr}")

    compare(py_last, rust_results[-1])
    print(f"parity: PASS ({encoder.width} values + {encoder.width} masks)")
    print(f"iterations: {args.iterations}")
    print(f"Python: {args.iterations / py_elapsed:,.0f} observations/s")
    print(f"Rust JSONL: {args.iterations / rust_elapsed:,.0f} observations/s")
    print(f"Rust/Python throughput ratio: {py_elapsed / rust_elapsed:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
