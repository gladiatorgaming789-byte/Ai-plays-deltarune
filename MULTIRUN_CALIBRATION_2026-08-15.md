# Eight-run calibration — 2026-08-15

This calibration reviews eight consecutive live AI runs from the same uploaded test batch. The analysis is evidence-first and room-agnostic: fixes use only screenshots, telemetry, learned collision/open-edge geometry, visual-guess lifecycle records, actions, and observed room transitions. No walkthrough route, dialogue answer, room-specific progression rule, NPC identity, or hidden game data is embedded in the policy.

## Runs reviewed

1. `20260815T023213.939Z`
2. `20260815T023647.747Z`
3. `20260815T023909.477Z`
4. `20260815T024156.111Z`
5. `20260815T024809.412Z`
6. `20260815T025350.525Z`
7. `20260815T025416.722Z`
8. `20260815T030120.271Z`

The batch is substantially more useful than a single endpoint because the same learned navigation memory carries forward between runs. That exposes persistence defects which a one-run review cannot reliably distinguish from temporary hypotheses.

## Confirmed failure families

### 1. Exit Detection v2 semantic leakage

Exit Detection v2 correctly distinguishes unresolved geometry/visual candidates from `semantic_ready` or confirmed exits, but the legacy path-continuation helper could stamp `hypothesis=possible_exit` afterward. The saved navigation memory therefore accumulated internally inconsistent records.

Persistent unresolved semantic-exit records by run:

| Run | Leaked records |
|---|---:|
| 1 | 0 |
| 2 | 1 |
| 3 | 1 |
| 4 | 10 |
| 5 | 21 |
| 6 | 27 |
| 7 | 27 |
| 8 | 30 |

Run 8 contains 28 `geometry_candidate` leaks and 2 `needs_approach_evidence` leaks. These counts are invariant failures, not judgments about the correct game route: the semantic field disagrees with the detector's own state.

**Fix:** `Run21MultiRunExplorer` re-evaluates exit semantics after every metadata refresh and when loading existing memory. Candidate evidence is preserved, but `possible_exit` is cleared unless the candidate is `semantic_ready` or confirmed by an observed crossing.

### 2. One-sided compact obstructions were over-promoted

Guessing v3 correctly records that one collision side cannot distinguish a person/object from scenery, but the calibrated belief model still gave a large interactable bonus to one-side 1–2-cell obstructions. Under story pressure, Run20 intentionally allowed those leads to bypass the ordinary one-side filter. The combination caused long routing commitments to weak evidence.

The new Run Doctor replay finds repeated one-side guess-selection streaks in Runs 1, 2, 4, 5, 7, and 8. Run 7 contains thirteen qualifying streaks; several last 10–19 consecutive decisions. Run 8 contains a 16-step streak toward one compact one-side candidate.

**Fix:** Entity Detection v2 removes the semantic-sized one-side bonus. One-side compact collision remains an `unknown_but_interesting` obstruction. During genuine story pressure it may receive a bounded concrete test instead of being ignored forever:

- at most 5 approach/planner decisions per weak-candidate episode;
- route distance no greater than 6 learned cells;
- at most 3 weak-candidate episodes per room/story epoch;
- one exact interaction test with no response is strong negative evidence;
- a response-producing interaction immediately confirms the learned interactable evidence;
- multi-side collision geometry and already confirmed response evidence are not weakened.

This preserves the earlier lesson that weak evidence must remain testable without allowing it to consume dozens of decisions.

### 3. Usable-looking but frozen Windows captures

The existing observer rotates to desktop/BitBlt only when PrintWindow fails or returns an obviously unusable bitmap. Several live intervals show the harder failure mode: PrintWindow continues returning a nonblank bitmap while telemetry proves the game/player is moving. VisualFreshnessGuard correctly invalidates the repeated bitmap, but the observer previously had no reason to try another backend because the primary capture still looked usable.

Direct replay detects:

- Run 3: moving-invalid streaks of 44, 65, and 34 steps;
- Run 4: a 176-step moving-invalid streak with 60 distinct telemetry positions and roughly 89 pixels of observed positional spread.

**Fix:** adaptive capture recovery fingerprints usable primary frames. After 8 repeated frames it periodically probes an independent backend (desktop when foreground, client BitBlt when background). A genuinely different usable alternate frame replaces the current observation and records recovery diagnostics. A static scene whose alternate backend matches the primary is left alone.

### 4. Repeated two-room link ping-pong

The existing arrival escape and rapid A-B-A checks handle immediate returns but do not cover repeated later re-entry through the same room link. Run 3 contains four bathroom/house link crossings in a short window. Run 8 crosses the same Dark World room pair seven times in 175 decisions.

**Fix:** Run21 adds a temporary behavior-only guard. Three crossings of the same unordered learned link within 220 navigation steps and the same story epoch activate a 120-step exact-warp-direction cooldown. The learned portal remains in memory, its semantic role is not demoted, and the cooldown expires automatically. This complements Warp Classification v2 rather than restoring permanent `backtrack = bad` semantics.

### 5. Objective churn is a symptom, not a reporting bug

Several long runs show roughly 150–219 objective identity changes. `ObjectiveManager` already excludes changing explanation text from objective identity, so this is primarily genuine switching between exploration, interaction, exit search, and recovery. The calibration deliberately does not hide the signal with objective hysteresis. The next live runs should show whether churn naturally falls after weak-guess, exit-memory, capture, and room-link thrashing are reduced.

## Run Doctor v1.0.3

Trusted Run Doctor v1.0.3 adds read-only detectors for the newly proven failure families:

- `repeated_weak_guess_approach`
- `repeated_room_link_pingpong`
- `unresolved_exit_semantic_leak`
- `capture_stale_while_player_moves`

The exit-memory invariant check reads the final saved `navigation.json` as well as lifecycle updates, so inherited stale semantic labels cannot disappear merely because a later run did not touch that region.

Threshold replay against all eight source runs shows the intended distribution: weak-guess findings appear in the runs where long same-candidate commitments are present; the broader link detector catches Run 3 and Run 8; moving-capture findings appear in Run 3 and Run 4; and the final-memory invariant follows the persistent exit leak as it accumulates. The very short Run 6 does not generate the weak-guess, ping-pong, or moving-capture findings, providing a useful negative control.

## Learning-memory policy

Do **not** wipe the existing learned navigation memory for this update. Run21 performs an in-place semantic migration:

- observed cells/open edges remain;
- learned warps remain;
- interaction outcomes remain;
- visual evidence ledgers remain;
- unresolved exit labels are downgraded without deleting their evidence;
- weak one-side entity labels are downgraded without deleting the collision observation.

A fresh live run is still required to validate behavior after migration. The main next-run measures are:

- unresolved semantic-exit leak count should be zero after load/save;
- long one-side selected-guess streaks should disappear or remain below the bounded budget;
- adaptive capture diagnostics should show whether alternate backends recover frozen primary captures;
- same-link crossing bursts should be reduced while later legitimate portal reuse remains possible;
- objective churn should decrease as a consequence of reduced planner thrash, not because it was hidden.

No DeltaMod/GML code is changed by this calibration.
