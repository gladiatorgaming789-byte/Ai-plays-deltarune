# Warp Classification v2

Warp classification v2 separates what the agent has actually learned about a portal from how the portal happened to be used during one or more traversals.

The goal is to prevent a real progression route from becoming permanently unattractive merely because the agent first used it to backtrack, returned through it quickly, or temporarily looped through it.

## Trust boundary

Warp semantics are learned only from the agent's recorded observations and outcomes.

The classifier does **not** use:

- walkthrough route answers
- wiki progression instructions
- hard-coded room-specific progression knowledge
- hidden object or trigger coordinates
- dialogue answers

Developer-side sources may be used to validate the classifier design, but those facts are never written into AI memory.

## Classification model

Each portal now has two independent views.

### Semantic role

`semantic_role` describes the strongest positive meaning actually supported by observed outcomes:

- `progression` — a non-discovery story-progress outcome was observed after the crossing.
- `new_area` — the portal was observed reaching a previously unseen room, but no independent story-progress outcome has yet been observed.
- `unknown` — the agent has not observed enough positive outcome evidence to assign either meaning.

A room becoming newly visible is deliberately not enough to call the portal progression.

### Behavioral tags

Traversal behavior is recorded separately:

- `observed_return_leg`
- `quick_return`
- `return_prone`
- `loop_risk`

These are observations about how the portal has been used. They are **not** claims that the portal is optional, a dead end, or incapable of later becoming progression.

Derived numeric diagnostics include `return_tendency`, `loop_risk`, and `mean_return_dwell_steps`.

## Primary planner role

The legacy-compatible `role` field remains because existing planning and GUI code consumes it, but classifier v2 changes its meaning.

Classifier v2 no longer emits `likely_optional` or `return/backtrack`.

The emitted planner roles are:

- `progression`
- `new_area`
- `unknown`
- `loop_suppressed`

`likely_optional` and `return/backtrack` remain accepted only as legacy persisted values so old navigation files can load and be recomputed safely.

Return evidence now lowers the planner role to `unknown` while retaining the semantic and behavioral evidence separately. This keeps the route available for future testing instead of making a one-way semantic conclusion from backtracking behavior.

## Reversible progression

Positive progression evidence always wins.

A portal may first be:

1. observed as a new area,
2. used for a quick return,
3. become `unknown` with return-prone behavioral tags,
4. and later be promoted to confirmed `progression` after an independent story-progress outcome is actually observed.

The return-prone tags remain in the audit history, but they cannot demote confirmed progression.

## Loop safety

Loop handling remains conservative.

One observed loop suppression is retained as `loop_risk` while the role stays `unknown`.

Two or more loop suppressions can place the portal in the temporary `loop_suppressed` safety role when no progression outcome has been observed.

`loop_suppressed` is not permanent. Confirmed progression immediately overrides it, and strong room-completion pressure allows the planner to reconsider a suppressed learned warp as a bounded recovery option.

## Arrival-door anti-bounce behavior

A portal that leads directly back to the room the agent just entered from remains temporarily avoided. This prevents immediate A → B → A bouncing.

The avoidance is no longer permanent. The learned arrival warp becomes eligible again when observed room-completion pressure is strong, including:

- story-stall pressure,
- a sufficiently long and well-mapped room stay, or
- enough completed flavor interactions to activate exit priority.

This means a return-prone doorway can later be reconsidered if exploration stalls, without the AI being told that the doorway is the correct progression route.

## Persistence and migration

World-model version 3 remains compatible.

Existing portal counters are authoritative. When an old navigation file is loaded, portal classification is recomputed from the saved evidence. An old `likely_optional` or `return/backtrack` string therefore does not remain sticky.

New derived fields include:

- `classification_version = 2`
- `semantic_role`
- `classification_state`
- `behavior_tags`
- `return_tendency`
- `loop_risk`
- `mean_return_dwell_steps`

Because these fields are derived from existing observed counters, old maps do not need to be deleted or reset.

## Regression coverage

Tests cover:

- new-room discovery without false progression,
- quick-return evidence without optional/return semantic labels,
- return-prone portals later promoted to progression,
- single-loop caution versus repeated-loop safety hold,
- progression overriding loop/return behavior,
- old version-2 world-model migration,
- old version-3 `likely_optional` records being recomputed,
- return-prone non-arrival warps remaining eligible,
- arrival-return warps being blocked before a stall and re-enabled under room-completion pressure,
- temporary loop suppression being re-testable under strong room-completion pressure,
- confirmed progression remaining eligible even when it points toward the entry room.
