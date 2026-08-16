# Deltarune AI roadmap

## Completed: one-game Population Training v1

- Versioned strategy genomes preserve the exact former Autonomy defaults.
- A configurable population of 2–16 deterministic, bounded candidates rescores shared legal options without
  mutating the authoritative world, goal, budget, trace, or input state.
- Coherent causal segments use round-robin exposure, UCB1 selection, observed
  outcome scoring, disqualification rules, and deferred safe handoffs.
- Run-local staged memory, SHA-256 conflict detection, eligibility gates,
  review-only GUI promotion, verified same-volume replacement, backups, and
  rollback keep active profiles safe.
- Detailed training manifests, event streams, candidate snapshots, score
  breakdowns, and GUI/Runs readers preserve auditability and old-run support.

See `POPULATION_TRAINING.md` for the implemented contract and live validation
requirements.

This roadmap prioritizes trustworthy observation and measurable autonomy before adding broader game knowledge. The gameplay agent may learn only from what the player can see and from outcomes it has observed. Developer-side files and manual review are validation tools, not hidden route input.

## Current foundation

- [x] Compose Warp Classification v2 with the production Run16+ policy stack without losing semantic role, lifecycle, or behavior evidence.
- [x] Run the full suite on the official Python 3.14 environment and add Python 3.14 to Recovery CI.
- [x] Make the Windows launcher prefer official Python 3.14, including the normal per-user launcher/install locations when they are absent from `PATH`.
- [x] Add the first Runs-page Autonomy workbench and export the base score needed for real shadow re-scoring.
- [x] Keep Run Doctor and Autonomy analysis read-only.
- [x] Add Navigation Coherence v1: persistent goal contracts, event-driven replanning, information-gain frontier clusters, portal apertures, room-cycle costs, geodesic progress gates, recovery hysteresis, reset cooldowns, and a saved-route Workbench overlay.

## Gate 1 — substantial Autonomy live run

Run at 1x or verified Auto/DRSPEED timing while preserving the existing learned navigation memory.

Acceptance evidence:

- no uncertainty-budget overrun;
- no unresolved Exit Detection v2 candidate saved as a semantic exit;
- no repeated same-link burst introduced by Autonomy;
- weak one-sided candidates remain within their bounded test budget;
- stale frontiers stop pinning recovery, while productive frontiers still win;
- learned warps become eligible during genuine stalls without becoming an immediate bounce loop;
- the Autonomy workbench shows the actual selected goal and alternatives;
- the Navigation Coherence contract shows falling route distance, stable target reuse, and an accurate learned-route overlay;
- coordinate-jittered crossings appear as one planning aperture without mutating persistent warp evidence;
- broad reset cooldown and arrival leases prevent repeated reset/return bursts;
- shadow replay has no unexplained large-score disagreement;
- Run Doctor findings agree with a manual whole-run review.

If this gate fails, add the smallest replay fixture that reproduces each confirmed problem before changing policy thresholds.

## Gate 2 — Autonomy Workbench experiments

After Gate 1 establishes trustworthy saved snapshots:

1. add offline weight controls with Reset and Compare actions;
2. show selection changes and score deltas per decision;
3. export an experiment report without mutating profile memory or live settings;
4. add run-to-run comparison for goal churn, recovery residence, budget use, loop resistance, and observed progress;
5. permit promotion of a weight set only through an explicit user action and a bounded validation run.

## Gate 3 — Chapters 1–5 mod validation

The current normal runtime-test candidate is AI Support 1.0.0, which atomically combines Speed 1.3.1 and Telemetry 9.2.1. Standalone Speed and Telemetry packages are isolated diagnostic candidates because current DeltaMod CSX application does not reliably compose two independent patches to the same `data.win`.

For each chapter:

1. start from DeltaMod's clean protected copy;
2. validate AI Support startup with no GameMaker error;
3. verify protocol-v9 telemetry identity, packet sequence health, room/player/control state, transitions, dialogue, choices, battle state, and native autosave evidence;
4. verify 1x–10x controls and Auto synchronization, including stale-packet fallback;
5. exercise overworld movement, dialogue, choice, cutscene, room transition, menu, and battle input at 1x and 2x;
6. smoke-test higher speeds separately and record the first processing-limited multiplier;
7. disable/re-enable the package once and confirm clean restoration and deterministic repatching.

The package remains a runtime-test candidate until every chapter passes. Do not interpret source-level patch success as live runtime verification.

## Gate 4 — dialogue and story reasoning

Add player-visible text understanding only after observation health is stable:

- OCR or multimodal reading of the visible dialogue/choice region;
- dialogue episode memory keyed by observed screen text and consequence, not NPC identity from game files;
- explicit choice exploration with outcome tracking and bounded retries;
- detection of repeated non-progress dialogue so the same object is not spammed;
- story-goal hypotheses expressed as testable observed outcomes.

Success means the AI can explain what visible prompt it reacted to, what option it tried, what changed afterward, and why another attempt is or is not warranted.

## Gate 5 — battle competence

- separate battle phase/action-state perception from overworld reasoning;
- learn menu navigation and action consequences from visible state;
- add projectile/soul tracking and short-horizon dodge control;
- record damage avoided/taken, turn completion, resource changes, and battle outcome;
- keep battle learning isolated from overworld route memory.

Start with reliable survival and menu control at 1x before testing accelerated battles.

## Gate 6 — repeatable evaluation

- maintain a fixed set of clean-save and learned-memory profiles;
- run bounded seeded trials at verified speeds;
- compare observed story progress, unique useful interactions, room-link reuse, goal churn, recovery costs, timing health, and crashes;
- keep raw artifacts, Autonomy snapshots, maps, Run Doctor reports, and configuration together;
- promote changes only when they improve multiple runs without weakening safety or observation quality.

The best next action is Gate 1: one substantial, timing-verified Navigation Coherence run followed by a whole-run review in the Autonomy and Run Doctor tabs. Compare contract reuse, route stalls, goal switches, broad resets, room-link bounces, and observed progress against the pre-coherence run.
