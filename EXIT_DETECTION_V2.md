# Exit Detection v2

Exit Detection v2 is a precision-first layer for visual room-exit detection. It was added because the older visual pipeline could identify scenery as exits more often than it identified real exits correctly.

The goal is not to make the agent refuse uncertain routes. The goal is to distinguish **exploration probes** from **semantic exit detections** and require independent evidence before the agent remembers a visual feature as an exit.

## Why the previous detector overcalled exits

Three older detector families are still useful for finding *exit-like structure*, but they were too eager semantically:

1. The base screen-region detector can find a localized dark channel connected to a camera/room edge and propose `possible_exit` from one view.
2. Run13 can detect visible room-colored pixels touching a true room boundary and force the observation to `possible_exit`, with an opening score of at least 0.82.
3. Run14 can detect a rectangular doorway-like facade and force `possible_exit`, with an opening score of at least 0.86.

Those large visual scores then passed older Run4 visual-exit thresholds easily, even though a dark seam, floor strip, window-like rectangle, cabinet, or decorative wall could satisfy the appearance test without being traversable.

A second audit found that `path_continuation` was also weaker than its name implied. It is set when the learned map contains an inward corridor ending at an untested boundary edge. The crossing has **not** necessarily succeeded yet. It is useful approach geometry, not proof that the boundary is an exit.

## New evidence hierarchy

V2 separates four stages.

### 1. Exit-like visual candidate

The existing image analyzers are retained as candidate generators. Their output may describe:

- `doorway_facade`
- `floor_boundary`
- `scrolling_floor_boundary`
- `dark_edge_opening`
- `generic_edge_opening`

These labels mean only that the pixels resemble a route feature. They do not make the planner treat the feature as an exit.

### 2. Learned approach geometry

V2 measures `exit_approach_length`: the number of consecutive learned-open movement cells leading inward from the candidate's anchor, capped at four cells.

A generic `path_continuation` / map-boundary probe is kept as `geometry_path_probe`. It is allowed to guide bounded exploration, but it is **not** a semantic `possible_exit` by itself.

### 3. Multi-view consistency

Guessing v3 already samples the raw current feature anchor from distinct viewpoints. V2 uses that measurement to require that a candidate remain spatially coherent instead of trusting one screenshot.

Strong drift is contradiction evidence. One view is never treated as multi-view confirmation.

### 4. Observed cardinal room crossing

A real current cardinal overworld movement that changes rooms is authoritative confirmation. V2 can confirm an unresolved nearby candidate after such a crossing, even if the precision gate had deliberately removed the old `possible_exit` label beforehand.

Scripted, dialogue-driven, cutscene-driven, menu-driven, and interaction-driven room changes do **not** confirm visual exits. The policy clears `last_movement` outside cardinal overworld movement, and the final confirmation guard requires a current `up`, `down`, `left`, or `right` movement signal.

Candidate-to-transition association is also spatially conservative: an anchored candidate must be within three learned cells of the transition source; without an anchor it must occupy the exact source region.

## Promotion gates

A record is visually actionable as an exit only when `exit_candidate_state` is `semantic_ready` or `confirmed`.

### Doorway facade

Requires:

- at least 2 independent candidate viewpoints;
- at least 2 raw multi-view samples;
- multi-view consistency >= 0.55;
- opening/doorway score >= 0.72; and
- learned-open approach length >= 2 cells.

### Floor / boundary continuation

Requires:

- an aligned learned boundary/path probe;
- learned-open approach length >= 2 cells;
- at least 2 independent candidate viewpoints;
- at least 2 raw multi-view samples; and
- multi-view consistency >= 0.60.

Pixels touching the room boundary are therefore never enough on their own, regardless of how high Run13's raw visual score is.

### Dark edge opening

Requires:

- at least 3 independent candidate viewpoints;
- at least 2 raw multi-view samples;
- multi-view consistency >= 0.70;
- opening score >= 0.68;
- localized opening width between 10% and 50%; and
- learned-open approach length >= 2 cells.

### Generic edge opening

Requires the same kind of repeated stable visual evidence plus aligned map-boundary and approach evidence. Otherwise it remains unresolved.

### Contradiction

A visual candidate becomes `contradicted` when current evidence includes any of:

- at least 2 failed approaches;
- at least 3 visual misses; or
- at least 2 multi-view samples with consistency below 0.35.

A later observed real crossing may still override earlier contradiction because the crossing is stronger evidence.

## Guessing v3 integration

Before a candidate is semantic-ready, Exit Detection v2 removes the old semantic-sized boosts from Guessing v3:

- `edge_opening_score * 2.0`; and
- the `+2.70` `path_continuation` boost.

V2 replaces them with smaller candidate-sized evidence. This keeps an exit-like feature interesting without letting the legacy 0.82/0.86 detector scores dominate the exit belief before independent evidence agrees.

A defensive final gate clears `hypothesis="possible_exit"` if a future belief calibration attempts to promote a candidate that has not reached `semantic_ready`. Such a feature remains `unknown_but_interesting` with the label `Exit-like feature; route evidence unresolved`.

## Exploration is not detection

The existing planner may still test an unknown learned map boundary after normal frontiers are exhausted. This is intentional exploration.

Exit Detection v2 prevents that geometry-only probe from being stored or routed as a semantic visual exit. This distinction keeps unusual or visually subtle exits discoverable without letting every plausible room boundary become an asserted doorway.

## Persistence and diagnostics

Per-region V2 fields are optional additions to existing screen-region memory:

- `exit_detection_version`
- `exit_candidate_source`
- `exit_candidate_state`
- `exit_candidate_visual_score`
- `exit_candidate_views`
- `exit_candidate_viewpoints`
- `exit_candidate_last_step`
- `exit_candidate_reasons`
- `exit_candidate_promotions`
- `exit_approach_length`

Run summaries expose:

- `exit_detection_version`
- `exit_visual_candidates`
- `exit_semantic_ready_candidates`
- `exit_contradicted_candidates`
- `exit_candidates_needing_approach`
- `exit_geometry_only_candidates`
- `exit_candidate_promotions`
- `exit_candidate_states`
- `exit_candidate_sources`

Map updates include the V2 fields so a run review can reconstruct why each candidate did or did not become an exit.

## Regression coverage

Focused tests cover:

- floor-boundary pixels never promoting by themselves;
- boundary candidates requiring repeated visual evidence even when map geometry points toward them;
- stable boundary + aligned learned approach promotion;
- geometry/path probes remaining non-semantic by themselves;
- one-view doorway facades remaining unresolved;
- stable doorway facades still requiring learned approach geometry;
- dark edge openings requiring repeated stable views and an open approach;
- poor multi-view consistency producing contradiction;
- removal of legacy opening/path semantic boosts before evidence fusion;
- old saved `possible_exit` labels not being actionable merely because of the label;
- `path_continuation` not being actionable by itself;
- unresolved nearby candidates becoming confirmed after a real cardinal room crossing;
- spatially unrelated candidates not receiving transition credit; and
- scripted/non-cardinal room changes not confirming visual exits.

## Next empirical calibration

The next gameplay run should preserve existing learned memory and record V2 diagnostics. The important measurements are:

- visual exit candidates created;
- candidates promoted to semantic-ready;
- candidates contradicted;
- geometry-only probes;
- promotions by source type;
- confirmed crossings by source type;
- false semantic exits that consume actions without crossing;
- true exits first missed by the stricter gate;
- average independent views before promotion;
- multi-view consistency of true vs false candidates; and
- action cost of geometry-only exploration probes.

The target is **precision first**: semantic `possible_exit` should mean substantially more than “some pixels resemble a doorway.” Recall can then be calibrated from real run evidence without restoring one-frame false positives.
