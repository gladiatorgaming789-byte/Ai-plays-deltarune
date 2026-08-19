# Deltarune AI roadmap

This roadmap prioritizes trustworthy observation, safe user data, and measurable autonomy before broader game competence. The gameplay agent may learn only from what the player can see and from outcomes it has observed. Developer-side files, wiki/playthrough review, and source inspection are validation tools only; they never become hidden route, dialogue, NPC, or progression answers in AI memory.

## Current production foundation

The current production stack is installed through one explicit `production_runtime.py` composition point. The historical Run2–Run21 inheritance chain remains the learned-navigation foundation for now, while new cross-cutting systems are installed in a fixed order so normal runs and independent-training workers cannot silently diverge.

Implemented layers:

- Navigation Coherence v1 over Autonomy v1 and Run21 navigation.
- Warp Classification v2, Guessing v3, Exit Detection v2, and Entity Detection v2.
- Trusted Automatic Run Doctor v1.0.4.
- Independent Population Training v2.1.
- Frame ↔ Telemetry Synchronization v1.
- Battle System v2 foundation.
- Learned-control Special Gameplay v1 with a prior-telemetry activation guard.

Current agent revision:

`population-training-v2.1-frame-sync-v1-battle-v2-special-gameplay-v1-navigation-coherence-v1-autonomy-v1-run-doctor-v1.0.4`

## Completed repair pass — August 19, 2026

A repository-wide audit identified issues spanning saves, shutdown, Population Training, GUI isolation, visual/telemetry timing, battle competence, special gameplay, release tooling, and runtime composition. The recommended repair order was implemented source-first.

### 1. Save protection and shutdown safety

- The telemetry startup checkpoint is now `AI_BACKGROUND_AUTOSAVE_V2` and runs only when a sanitized non-empty `ai_instance_*` identity exists. Ordinary single-instance play therefore cannot reach the training-only `scr_save()` checkpoint through this hook.
- Telemetry 9.3.0 and AI Support 2.0.0 are withdrawn.
- Current safe source candidates are Telemetry 9.3.1 and combined AI Support 2.0.1; Speed remains 1.4.0.
- The Qt operator console no longer automatically kills a still-running controller after five seconds. Close requests first use cooperative stop and continue waiting for input release, artifact finalization, memory persistence, and training-child cleanup. A force stop is available only after an explicit warning/confirmation.
- QProcess `FailedToStart` now leaves the GUI's STARTING state cleanly instead of depending on a `finished()` signal that may never arrive.

### 2. Independent Population Training v2.1

- Telemetry coverage is measured as decisions that had a telemetry sample divided by actual active decisions, rather than multipart UDP packet count divided by loop steps.
- Passive waits, cutscene/control-lock steps, and transition-lock waits no longer satisfy the 64-decision exposure gate.
- Candidate scoring consumes structured events the current policy actually emits: story-progress events, observed room discoveries, choice outcomes, interaction outcomes, and open-edge updates. Silent dead fields no longer decide a winner.
- Every candidate receives the same base random seed so strategy changes are not automatically confounded with different RNG seeds.
- GUI/step-limit stop requests are deferred until safe controlled overworld state, with a bounded reserve for the current consequence to finish.
- Worker output uses a bounded queue, drains batches, and emits population snapshots at a bounded cadence rather than duplicating the full population into every candidate event.
- Candidate events are isolated from the single-profile Live Map; the Training page/log still receives them.
- F8/F9/F10 per-process speed controls are locked during an active population comparison.
- The 16-AI card grid is scrollable.
- Game-root discovery supports an explicit path, `DELTARUNE_GAME_ROOT`, the default Steam library, and additional Steam libraries recorded in `libraryfolders.vdf`.
- Promotion requires a clean quorum (at least 75% of candidates and at least two AIs) rather than making one environmental lane failure invalidate every clean comparator.
- Winner promotion restores pre-training window-title memory so `DELTARUNE - AI <id>` identities cannot leak into the normal profile.

### 3. Frame ↔ Telemetry Synchronization v1

The observer now timestamps capture, and the telemetry receiver aligns the screenshot with the nearest safe bracketing telemetry sample. A preceding sample may be used only when it is closer, fresh, and belongs to the same agent; detected room transitions force the current sample. When the remaining time offset exceeds the configured reliability window, visual evidence is marked invalid rather than teaching world-space guesses or the visual classifier from a mismatched image/state pair. Synchronization diagnostics are saved for review.

### 4. Battle System v2 foundation

Battle v2 no longer trusts mixed overworld player coordinates as the battle SOUL position. It finds compact colored SOUL components directly from the visible battle image, separates same-colored HUD components, and records observed control mode. It adds a bounded visible-menu experiment learner, learns successful menu input patterns only from observed screen consequences, predicts short-horizon bright projectile motion, supports ordinary movement defense, visible yellow-mode shooting, and visible green-mode directional blocking. Unknown/constrained modes remain conservative rather than receiving encounter-specific hidden solutions.

This is a foundation, not a claim of full battle mastery. Live battle calibration is still required for menu semantics, complex boards/projectiles, non-red modes, targeting, turn outcomes, and later encounters.

### 5. Learned-control Special Gameplay v1

A generic control-discovery fallback handles dynamic gameplay phases that stop appearing in the normal telemetry stream. It has no chapter/minigame names or preloaded control answers. After a previously healthy telemetry stream disappears for a sustained period and the visible scene remains dynamic, it tests bounded ordinary controls, measures visible response/novelty, and learns which controls affect that visual context. Telemetry returning disables it immediately. A run that never had telemetry remains on the normal visual-only policy and is not hijacked by this fallback.

### 6. Runtime/release consolidation

- Cross-cutting runtime systems now install from one versioned production composition point.
- Current mod validation checks the `AI_BACKGROUND_AUTOSAVE_V2` marker itself.
- Unsafe Telemetry 9.3.0 and AI Support 2.0.0 archives were removed from `development` and their release records are marked withdrawn.
- While GitHub Actions runner provisioning is blocked, Telemetry 9.3.1 and AI Support 2.0.1 are materialized locally from committed source by `mods/build_validated_packages.py` during normal launcher startup. The materializer verifies version, five chapter CSX declarations, validated clean-file hashes, and required safety markers before accepting them.

## Validation gate A — safe ordinary run

Before scaling training or testing later gameplay systems:

1. Update/import AI Support 2.0.1 and keep standalone Speed/Telemetry disabled for normal combined operation.
2. Start one ordinary Chapter 1 run at 1× or verified Auto timing.
3. Confirm no GameMaker error, telemetry protocol v9 health, and speed synchronization.
4. Confirm the ordinary game does not use/create a training-instance save path or receive the training-only startup checkpoint.
5. Close the GUI during a run and confirm cooperative shutdown completes input release, memory/artifacts, and game/controller cleanup without an automatic hard kill.
6. Review frame-telemetry synchronization diagnostics and ensure rejected visual samples correspond to genuinely uncertain timing rather than healthy 1× operation.

## Validation gate B — two-AI Population Training v2.1

Use only two independent AIs initially.

Acceptance evidence:

- distinct PIDs, visible windows, UDP ports, agent IDs, and isolated save folders;
- both workers use the same base RNG seed but different strategy genomes;
- candidate events never alter the single-profile Live Map;
- per-instance speed hotkeys are locked while the comparison runs;
- active-decision counts exclude passive/control-lock waits;
- telemetry coverage is decision-based and cannot be inflated by multipart packets;
- GUI Stop waits for safe controlled overworld state;
- queue/status latency remains bounded;
- original profile and ordinary DELTARUNE saves remain unchanged;
- promotion gates are truthful; if a winner is eventually promoted, training-only window metadata is not imported into the normal profile.

Do not scale to 8–16 AIs until this passes.

## Validation gate C — Battle System v2 at 1×

Run battle tests at 1× first. Validate, from recorded screenshots/events only:

- SOUL component localization and color/mode changes;
- false detections from HUD/UI colors;
- menu pattern attempts and whether reported successes really advanced the visible battle state;
- projectile component detection and prediction quality;
- damage/survival outcomes;
- yellow shooting and green blocking when those visible modes are encountered;
- conservative behavior for modes not yet specialized.

Every confirmed failure should become a compact replay fixture before tuning thresholds or adding a new controller.

## Validation gate D — special gameplay control learning

When a later dynamic gameplay section is naturally reached, verify that:

- telemetry was healthy before the gap;
- the fallback does not activate for an intentional `--no-telemetry` run, dialogue, menu, battle, or a static screen;
- experiments remain bounded;
- learned controls correspond to visible causal response rather than background animation alone;
- learned context reuse improves performance without embedding developer-provided control mappings.

If scene-wide animation is too noisy to provide causal evidence, the next upgrade should track player/avatar motion or local controllable-object response before raising confidence.

## Repeatable evaluation

After gates A–D:

- maintain fixed clean-save and learned-memory profiles;
- run bounded seeded trials at verified speeds;
- compare observed story progress, useful interactions, room-link reuse, goal churn, recovery costs, battle survival/turn completion, special-control learning, timing health, and crashes;
- keep raw artifacts, Run Doctor reports, synchronization diagnostics, maps, training manifests, and configuration together;
- promote changes only when they improve multiple runs without weakening save safety, observation quality, or input cleanup.

GitHub Actions must be rerun once account runner provisioning is restored. Source-level regression tests have been added for these repairs, but a blocked workflow that executes zero steps is not evidence that the current head passed Windows CI.
