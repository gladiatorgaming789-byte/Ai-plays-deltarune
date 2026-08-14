# Guessing v3

Guessing v3 upgrades the visual-guess system from a single early semantic label to evidence-led, revisable beliefs.

## Trust boundary

The system uses only evidence the agent observed while playing: visual salience, room/world geometry, collision approaches, mapped path continuation, visual misses, route-test outcomes, and camera/viewpoint history. It contains no room-specific progression answers, NPC identities, walkthrough route knowledge, dialogue answers, sprite labels, or hidden game-state coordinates.

## 1. Evidence ledger

Each active region can retain a bounded `guess_evidence_ledger` (maximum 24 entries). Entries record the observed step/sequence, supporting evidence, contradictory evidence, belief snapshot, semantic state, and information-probe actions. Duplicate refreshes do not consume ledger space.

This makes a guess explainable as a history rather than only a final confidence number.

## 2. Multi-hypothesis beliefs

Each v3 record carries normalized probabilities for four interpretations:

- `possible_exit`
- `possible_character`
- `possible_interactable`
- `scenery`

Raw visual salience increases the value of investigating a feature but does not by itself decide its type. More specific evidence shifts the relevant beliefs: path/opening evidence supports exits; compact multi-side collision geometry supports character-sized obstacles; one-side compact geometry supports interactables; broad seams, repeated misses, failed approaches, and poor world-space consistency support scenery/artifact explanations.

The legacy `hypothesis` field is exposed to the existing routing policy only when a semantic belief is strong enough and sufficiently separated from the runner-up. This prevents a weak first interpretation from immediately controlling navigation.

### Evidence-purity rule

The currently exposed legacy `hypothesis` is **not evidence for itself**. Two records with identical observed geometry/outcomes produce the same v3 belief distribution even if an older routing layer happened to label one `possible_character` and the other `possible_interactable`. A semantic decision stays committed only while the underlying observed evidence continues to justify it.

The calibrated semantic threshold is 0.40 with a 0.07 lead over the next semantic interpretation. With the current evidence model, a compact one-side 1–2-cell obstruction can become `possible_interactable`, while a broader one-side four-cell obstruction normally remains unresolved and is investigated before commitment.

## 3. `unknown_but_interesting`

A feature with genuine structural evidence but insufficient semantic separation is retained as `guess_semantic_state = "unknown_but_interesting"`.

That state means: the agent observed something worth learning about, but currently cannot justify calling it a person, object, exit, or scenery. It is not routed as a character/object/exit until later evidence supports that conclusion.

Confirmed/rejected/retired lifecycle states remain authoritative; v3 does not silently demote a confirmed observation.

## 4. Multi-view consistency

V3 keeps up to ten bounded world-space observation samples per guess, including the world anchor and available camera viewpoint. With two or more distinct viewpoints it measures how much the feature's world anchor drifts.

A stable world-space anchor is evidence that the observation represents one coherent feature. Large anchor drift is contradictory evidence and raises the scenery/artifact explanation. A single viewpoint is intentionally treated as unknown rather than stable.

## 5. Information-gain probing

When the normal planner would otherwise fall all the way through to `no reachable frontier; probe ...`, v3 may use a visible `unknown_but_interesting` feature for a cheap viewpoint-changing probe.

The probe:

- prefers a safe perpendicular sidestep rather than a full approach;
- prefers already learned-open movement;
- respects blocked edges, entry-warp avoidance, and loop avoidance;
- never counts as a completed semantic test;
- has a cooldown;
- is capped at two attempts per guess;
- is recorded in the evidence ledger.

It cannot replace a learned warp, mapped frontier, strong exit, retryable interaction, or already actionable semantic visual target.

## Persistence and migration

The existing `WorldModel` schema remains readable. Guessing v3 wraps the already-installed Run16 persistence path and injects only optional extra fields into each saved screen-region record. Old memories therefore remain valid and are enriched lazily when loaded/observed.

An order-safe bootstrap captures the persistence/planner methods at installation time, preventing an early developer/test import from bypassing later persistence extensions. The evidence-only belief calibration is installed before those wrappers become active.

## Diagnostics

Run summaries now expose:

- `guessing_version`
- `multi_hypothesis_guess_records`
- `unknown_but_interesting_guesses`
- `guess_information_probes`
- `guess_evidence_ledger_entries`
- `low_multiview_consistency_guesses`

Screen-region map updates also include the v3 belief/ledger/consistency/probe fields so post-run analysis can inspect the AI's reasoning lifecycle.

## Validation targets

Focused tests cover:

- ambiguous evidence remaining unresolved;
- strong mapped-path evidence still committing to an exit;
- belief revision after stronger observed geometry;
- the legacy routing label not feeding back into the next belief calculation;
- compact one-side evidence versus broader ambiguous one-side geometry;
- stable versus drifting multi-view anchors;
- bounded information-gain probing;
- information probing replacing only blind fallback;
- non-interference with learned-warp/strong-evidence plans;
- production-order save/reload persistence in an isolated interpreter;
- bounded evidence/world-sample history;
- confirmed-guess lifecycle preservation.

The next empirical gate is a fresh run with existing learned memory preserved. The main metrics to inspect are false semantic commitments, `unknown_but_interesting` lifetime, information probes per confirmed/rejected guess, actions spent on weak guesses, multi-view consistency, and whether stronger evidence correctly revises earlier uncertainty.
