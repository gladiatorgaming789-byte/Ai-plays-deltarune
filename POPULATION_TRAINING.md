# Independent-Game Population Training v2.1

Population Training runs 2–16 separate DELTARUNE games at the same time. Every AI owns a complete lane: one visible game process/window, one isolated save directory, one localhost telemetry port, one controller process, one private navigation/visual/strategy/reinforcement memory set, and one private run-artifact tree.

There is no shared player, active-candidate handoff, or shadow-only AI. All candidates play independently. The v2.1 repair focuses on making those comparisons measurable and safe rather than merely parallel.

## Required support package

Use the current combined **AI Support 2.0.1** DeltaMod package for Population Training. It combines Speed 1.4.0 and Telemetry 9.3.1.

Telemetry 9.3.1 requires a sanitized non-empty `ai_instance_*` identity before its invisible startup checkpoint can call `scr_save()`. Ordinary single-instance play therefore does not use the training-only checkpoint. Telemetry 9.3.0 and AI Support 2.0.0 are withdrawn.

The training preflight refuses to start unless the selected chapter's `data.win` contains all of these current support markers:

- `AI_MULTI_INSTANCE|1|`
- `DRTEL|9|`
- `AI_SPEED_MOD|1|`
- `AI_BACKGROUND_AUTOSAVE_V2`

If the 9.3.1 / 2.0.1 ZIP is not already present after pulling `development`, `Start AI GUI.bat` runs `mods/build_validated_packages.py` and materializes the current package from committed source before the GUI starts.

## Starting a run

In the GUI, choose **Population training**, Chapter 1–5, 2–16 AIs, Live input, and the desired run speed before starting. Per-process F8/F9/F10 speed controls are locked while training is active because changing only one candidate would invalidate the comparison.

Equivalent command:

```powershell
python -m deltarune_agent run --training --population-size 4 --chapter 1 --live --steps 4000
```

Game-root discovery uses, in order: explicit `--game-root`, `DELTARUNE_GAME_ROOT`, the normal Steam install location, then extra Steam libraries listed in `libraryfolders.vdf`.

Four candidates remain the default. Strategy genomes differ deterministically, but every candidate now receives the **same base RNG seed** for one comparison so genome quality is not automatically confounded with a different Python random seed.

## What counts as exposure

v2.1 distinguishes controller loop steps from active decisions. The 64-decision minimum exposure gate counts only actions where the AI was actually making a gameplay choice. It excludes passive/control-locked transition waits, cutscene waits, and ordinary `wait` actions.

This prevents a candidate from satisfying the exposure gate merely because DELTARUNE spent a long time in an automatic sequence.

## Telemetry coverage

Protocol v9 emits multiple UDP packet layers for one telemetry snapshot, so raw packet count is not a meaningful decision-coverage numerator.

v2.1 records whether each **active decision** had a telemetry sample. Candidate coverage is:

`active decisions with telemetry / active decisions`

A promotion-eligible lane still requires at least 90% decision coverage and under 5% invalid raw packets. Multipart packet volume can no longer inflate sparse telemetry to 100% coverage.

## Current scoring

Positive/negative gameplay rewards are derived from structured events the current policy actually emits, rather than optional summary keys that may silently be absent.

| Observed outcome | Points |
| --- | ---: |
| Non-discovery story-progress event | +50 |
| First observed entry into a new room after the starting room | +15 |
| Successful recorded choice outcome | +10 |
| First unique recorded interaction target | +3 |
| New recorded open edge | +0.25, capped at +10 |
| Recorded flavor interaction | -5 |
| Failed recorded choice outcome | -8 |
| Rapid room return | -15 |
| Forced oscillation/loop escape | -10 |
| Failed Navigation Coherence goal contract | -4 |
| Broad recovery reset | -2 |
| Active decision | -0.05 |

Displayed score remains:

`100 * total_points / (active_decisions + 64)`

Candidate score artifacts retain the underlying event/run evidence for review.

## Safe stopping

A GUI Stop or nominal step limit is a **request**, not permission to kill a lane mid-consequence. Each worker receives a bounded reserve beyond the requested evaluation horizon. Once a candidate reaches the requested horizon—or the GUI asks to stop—the supervisor waits until that lane reports controlled overworld state before writing its stop file.

A lane that exhausts its reserve before safe control returns is not treated as a clean comparable run. A `gui_stop` is promotion-eligible only if the supervisor actually issued it from the safe state.

The operator window itself also uses cooperative shutdown first. It does not automatically `QProcess.kill()` after a five-second timeout. If cleanup remains stuck, the GUI explicitly warns about the risk to keyboard release, run artifacts, memory persistence, and training children before offering an emergency force stop.

## Output and GUI isolation

Each controller still emits detailed evidence, but the supervisor no longer attaches the entire population table to every worker event. It uses a bounded message queue, drains batches, and publishes population status at a bounded cadence.

Candidate events carry an `instance` identity and feed the Training page/log. They do **not** update the single-profile Live Map during Population Training, preventing different candidates' rooms, positions, leads, and decisions from being merged into one fictional map view.

The Training page places the 2–16 candidate cards inside a scrollable area for smaller/high-DPI displays.

## Save/process isolation

Each game receives:

- a unique process ID and visible window caption;
- a unique localhost telemetry port and agent ID;
- a unique `ai_training/<instance-id>` save prefix;
- a private memory/workspace directory;
- a private runs directory.

The active profile is fingerprinted before launch and is not written during the comparison.

The current support mod's training startup checkpoint is gated by the same non-empty instance identity that enables the isolated save prefix. A normal non-training game therefore cannot accidentally receive this training checkpoint.

## Promotion gates

A candidate can be eligible only when it has enough active-decision exposure and passes the lane-level safety gates, including telemetry coverage/packet health, speed verification, input cleanup, Run Doctor critical findings, loop/bounce limits, uncertainty-budget limits, and recognized clean stopping.

v2.1 uses a **clean quorum** instead of requiring every launched process to survive an unrelated environmental failure. A winner is considered only when at least:

`max(2, ceil(population_size * 0.75))`

candidates pass all gates. The winner is the best normalized score among those clean comparators, with story progress/safety/candidate ID used as deterministic tie-break context.

Promotion remains explicit. The existing transactional promotion path fingerprints the profile, stages the winner, backs up the active memory, atomically swaps, verifies, and rolls back on mismatch. v2.1 then restores the pre-training `window_titles.json` from the backup so training-only `DELTARUNE - AI <id>` identities cannot leak into the normal profile; failure during this sanitization rolls the whole promotion back.

## Recommended validation progression

Do **not** start with 8–16 AIs. First run two candidates at 1× or verified Auto timing and confirm:

- distinct PIDs/windows/ports/agent IDs/save folders;
- same base RNG seed, different genomes;
- candidate events stay out of the Live Map;
- active-decision and telemetry-coverage values agree with raw events;
- GUI Stop waits for controlled overworld state;
- original DELTARUNE saves and active profile remain unchanged;
- queue/status latency remains bounded;
- both child controllers release input and finalize artifacts cleanly;
- any recommended winner is supported by its actual structured events and safety gates.

Scale only after that two-AI gate passes.

## Validation status

Source-level regression coverage has been added for v2.1 measurement, safe stopping, queue/event behavior, GUI isolation, scrollable candidate layout, support-mod marker gating, and production composition. However, the repository's GitHub Actions account is currently unable to provision runners because of the account billing/spending-limit condition. A red workflow with zero executed steps is **not** a passing or failing code result. The current head still requires a real Windows/DELTARUNE two-instance validation before Population Training v2.1 should be trusted for winner promotion.
