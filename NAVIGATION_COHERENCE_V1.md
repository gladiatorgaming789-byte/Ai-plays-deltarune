# Navigation Coherence v1

Navigation Coherence v1 is the production planning layer above Autonomy v1. It does not add room answers, portal coordinates, NPC identities, dialogue answers, or data-file knowledge. It makes better use of evidence that the agent has already observed.

## Why this layer exists

The previous coordinator could choose a sensible target and still fail behaviorally because it reranked after almost every short movement, treated routine map growth as a recovery reset, represented a wide doorway as several coordinate-specific portals, and allowed broad recovery to fire repeatedly. Those behaviors created goal churn, A-B room oscillation, and movement that looked indecisive even when the underlying evidence was useful.

The coherence layer keeps the existing collision and input safety logic authoritative. It changes planning only.

## Persistent goal contracts

Every selected evidence-backed target becomes a run-local contract containing:

- the option and target cell/room;
- the expected observable outcome;
- explicit replan triggers;
- a bounded action budget;
- current and best geodesic route distance;
- time without route progress;
- a saved learned-route preview.

The planner reuses the cached goal and recomputes only its next route action. It reranks after an observable event: story/room transition, material semantic evidence, invalid reachability, geodesic stall, exhausted uncertainty budget, or exhausted contract budget. Ordinary cell and edge discovery does not interrupt an unrelated route.

## Learned frontiers

Individual frontier tiles are grouped by the existing 32-pixel exploration region. A cluster is ranked by reachable route distance and expected information gain from unknown edges, unseen adjacent cells, and unseen adjacent regions. The chosen target and probe direction remain derived solely from learned movement evidence.

Straight movement commitments may expand from one to at most three decisions when every intermediate edge is already learned open. Unknown edges, portal approaches, nearby targets, entry-warps, and blocked evidence remain single-step precision actions.

## Portal apertures and room cycles

Coordinate samples with the same source room, action, and target room are merged for planning when their source and arrival coordinates fit one observed aperture. The persistent world model remains unchanged; this is a reversible planner-side view that preserves sample bounds and crossing count.

Portal and long-horizon scores include a temporary trajectory cost for immediate returns and repeated one- to three-room suffixes. After entering a room, a short arrival lease adds extra cost to the portal back to the source room. These are planning penalties, not semantic role changes: an observed progression portal remains classified as progression.

## Recovery damping

Recovery may escalate immediately when pressure increases. De-escalation happens one level at a time after a minimum residence period, preventing ordinary map updates from bouncing between recovery modes. A broad reset starts a cooldown and cannot repeat until that cooldown expires or material evidence changes.

## Saved evidence and UI

Prediction snapshots add a `coherence` object with the active contract, route preview, replan reason, recent room trajectory, arrival lease, reset cooldown, frontier-cluster count, and portal sample/aperture counts. Run summaries add aggregate activation, reuse, completion, failure, stall, hysteresis, cycle, reset-suppression, and adaptive-commitment metrics.

The Runs page Autonomy Workbench renders the contract and a read-only route overlay:

- blue circle: saved current cell;
- teal path: learned route preview;
- pink square: contract target.

Older run folders remain readable because the artifact reader treats the coherence object as optional.

## Live validation gate

The implementation is structurally testable offline, but the following claims require a new timing-verified live run:

- fewer high-level goal switches and broad resets;
- no repeated immediate portal-return burst;
- portal apertures remain stable across doorway coordinate jitter;
- route distance falls during committed approaches;
- frontier clusters reveal new evidence without tile-by-tile coverage;
- adaptive corridor commitments remain smooth without overshooting interactions or transitions.

If one of these fails, preserve the run folder and add the smallest replay fixture before changing thresholds.
