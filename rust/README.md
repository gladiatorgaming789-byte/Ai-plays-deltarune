# Rust numerical runtime (Milestone R1)

This crate is an independent Rust implementation of the numerical observation
contract in `deltarune_agent/neural_env.py`. It is deliberately not connected
to live input, game memory, screenshots, room maps, story text, or hidden
interactable data.

## Build and test

Install the stable Rust toolchain, then run from this directory:

```powershell
cargo test
cargo build --release
```

## JSON Lines encoder

The `encode-observation` binary accepts one JSON object per line on stdin.
Each request has a `telemetry` object whose field names match
`TelemetrySample`, plus optional `previous_action` and
`previous_action_duration` fields.

```powershell
'{"telemetry":{"mode":"overworld","room_id":7,"room_name":"room_home","x":120.0,"y":42.0,"player_facing_direction":"right"},"previous_action":"right","previous_action_duration":0.1}' |
  cargo run --release --bin encode-observation
```

Output contains the 55 numerical values, 55 availability-mask values, feature
names, packet sequence, and room metadata. The neural input remains
`values + available` (110 floats).

## Compatibility rules

- Feature names and order must match Python exactly.
- Normalization scales and clipping must match Python exactly.
- Missing or non-finite optional values become zero with availability zero.
- Previous actions use the fixed eight-action one-hot order.
- Room buckets use BLAKE2b with a two-byte digest, matching Python's
  `hashlib.blake2b(..., digest_size=2)`; the first digest byte is normalized
  to `[0, 1]`.
- The Rust crate is not yet used by the runner. Python remains authoritative
  until cross-language parity has been verified against real telemetry samples.

## Important limitation

This first milestone implements encoding only. It does not claim a speedup
for model training, does not perform neural inference, and does not send game
input. Performance decisions will be based on measured parity and benchmarks.


## R1 parity benchmark

From the repository root, after building the release binary:

```powershell
cargo build --release --manifest-path rust/deltarune_runtime/Cargo.toml --bin encode-observation
python scripts/benchmark_neural_encoder.py --iterations 5000
```

The benchmark starts one persistent Rust JSONL process, checks the complete
observation/mask/feature-name contract against the Python encoder, then reports
throughput for both implementations. It is a reference benchmark, not a claim
that Rust will improve end-to-end game learning speed.
