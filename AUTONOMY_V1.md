# Autonomy v1

Autonomy v1 is the recovery and long-horizon planning layer above the Run21 navigation stack. It coordinates only evidence the agent has already learned from gameplay. It contains no room-specific route answer, NPC answer, dialogue answer, or hidden progression rule.

## Trust boundary

Autonomy may use:

- observed open/blocked movement edges;
- observed room transitions and warp metadata;
- learned interaction outcomes;
- Guessing v3 beliefs and evidence state;
- Exit Detection v2 / Entity Detection v2 lifecycle state;
- room/frontier coverage;
- recent loop history;
- story-progress events that the existing agent observed from game-state consequences.

Autonomy must not use developer playthrough/wiki knowledge as a gameplay input. External sources remain developer-side validation only.

Autonomy does not replace persisted world memory. Recovery tier, active goal, uncertainty budgets, and ranking state are run-local. Existing navigation memory should be preserved across this release.

## Recovery ladder

The planner escalates only when ordinary progress pressure exists:

0. `normal` — existing Run21 behavior.
1. `frontier` — a reachable learned frontier remains the first recovery preference.
2. `evidence` — rank strong observed semantic evidence, response-producing interaction retries, information probes, and observed progression warps.
3. `bounded_test` — admit weak one-sided entity tests and unresolved boundary tests under explicit action budgets.
4. `learned_route` — admit ordinary learned warps and multi-room plans over the observed warp graph.
5. `controlled_backtrack` — allow learned return/suppressed links after strong room-completion pressure, while Run21's short anti-bounce hold remains authoritative.
6. `broad_reset` — final fallback using the least-visited safe learned direction after structured options are exhausted.

A story-progress event resets the ladder. Newly learned map evidence can de-escalate expensive recovery back toward evidence-first reasoning.

A reachable frontier gets a **48-decision no-new-evidence grace period** rather than an unlimited veto on recovery. During that grace period, ordinary frontier-first Run21 behavior remains authoritative. If following/approaching frontier state produces no newly learned cell, open edge, interaction, or warp for the full grace period, Autonomy may escalate and put the still-available frontier into the same unified ranking as other learned evidence. Any new learned map evidence resets the frontier grace period. This prevents a stale or repeatedly revisited frontier from pinning recovery forever while still strongly preferring real unexplored space.

## Uncertainty budgets

Uncertain options have bounded action budgets. A candidate cannot refill its budget by accumulating its own failed approaches, completed tests, or approach counters.

A budget may reset only when its evidence fingerprint changes, for example:

- story epoch changes;
- an additional independent view is learned;
- multi-view consistency meaningfully changes;
- semantic/candidate state changes;
- belief distribution crosses a coarse evidence bucket;
- confirmed interaction/transition evidence appears.

Known warps are not uncertainty-budgeted; their cost is controlled by cooldown, loop-risk, recovery level, and learned route reachability. Frontier options that survive beyond the initial grace period are budgeted when they enter unified recovery ranking, so an unchanged frontier cannot consume recovery forever.

## Unified option ranking

Every recovery option is converted to a common score using only learned evidence:

- base value by evidence family;
- confidence;
- information value;
- novelty;
- route distance;
- temporary loop risk;
- previous failure cost;
- fraction of uncertainty budget already spent.

Loop risk is a planning penalty, not a warp semantic role. A portal classified as progression remains progression even if it recently participated in a return loop.

After the frontier grace period expires, a reachable frontier remains a high-scoring option rather than disappearing. It can therefore still beat a weaker interaction/warp hypothesis, but it must win on observed evidence instead of permanently blocking every other recovery family.

## Goal commitment

The original Autonomy coordinator supplied a short six-decision commitment window. Production now extends this through [Navigation Coherence v1](NAVIGATION_COHERENCE_V1.md): an evidence-backed option becomes a bounded goal contract and is reused until an observable replan trigger occurs. Geodesic route progress, action budget, material evidence, reachability, story/room transitions, and expected outcomes replace periodic reranking as the main lifecycle signals.

This is intended to reduce `explore → inspect → seek_exit → inspect` thrashing without hiding the objective-change metric. If Autonomy works, objective churn should fall naturally.

## Long-horizon learned-map planning

Autonomy searches at most four observed warp hops. Remote room utility is computed only from learned state such as:

- unresolved frontiers;
- unresolved observed evidence;
- retryable learned interactions;
- how little of that room has been mapped.

The planner may conclude that an observed route offers more unexplored/informative state. It never concludes that the route is the correct story route.

## Loop prediction

Warp options are penalized using observed return tendency, recorded loop risk, recent room history, entry-room status, and suppressed-link history. Run21's short repeated-link hold is a hard temporary safety rule. Older blanket/suppressed-link state may still be relaxed by the existing strong-recovery compatibility layer.

## Detector/lifecycle composition

Autonomy coordinates existing detector lifecycles rather than bypassing them:

- weak one-sided entity probes remain hard-bounded by Run21's five-action active approach limit and concrete no-response rejection;
- semantic and geometry visual candidates respect active visual cooldowns, while expired cooldowns can become eligible again;
- information probes remain bounded by Guessing v3's two-probe limit;
- unresolved exits remain non-semantic until Exit Detection v2 reaches `semantic_ready` or an observed crossing confirms them;
- Run21's immediate entry guard and short repeated-link hold remain authoritative even during controlled recovery.

## Diagnostics

Every Autonomy prediction snapshot records:

- recovery level, reason, age, story epoch/stall;
- active goal and age;
- whether goal commitment held;
- selected option;
- top ranked options, base scores, and final scores;
- confidence, information value, novelty, distance, loop risk and failure cost;
- uncertainty budget limit/spent/remaining.

Navigation Coherence snapshots additionally record the goal contract, exact target and saved learned-route preview, current/best route distance, no-progress ticks, replan triggers/reason, recent room trajectory, arrival lease, broad-reset cooldown, clustered frontier count, and portal sample/aperture count.

Run summaries add recovery-level changes, escalations/de-escalations, budget actions/exhaustions/evidence resets, goal switches/commitment holds, selections by kind, long-horizon plans, loop-risk avoids, broad resets, empty-tier escalations, frontier grace escalations, and frontier actions selected after unified ranking.

## Shadow replay

`deltarune_agent.autonomy_shadow` re-scores saved Autonomy option snapshots after a run. It can test generic alternative ranking weights and identify unexplained selection disagreements without sending any input to the game or mutating learned memory.

The GUI's Runs page includes a read-only **Autonomy** workbench. It shows the latest recovery reason, active contract, expected outcome, route progress, replan evidence, room-cycle state, active uncertainty budget, ranked alternatives, a saved route/target overlay, and a shadow consistency summary over the bounded prediction window loaded by the UI. Option metadata is available as a tooltip. Runs created before Autonomy v1 or Navigation Coherence v1 remain readable and show an explicit no-snapshot or no-contract state.

## Run Doctor v1.0.4

Trusted Run Doctor v1.0.4 retains all v1.0.3 calibrations and adds read-only Autonomy checks for:

- repeated recovery-goal switching;
- bidirectional recovery-level thrashing;
- uncertainty-budget overruns;
- repeated selection of a materially lower-scored option without a recorded commitment hold;
- long high-cost recovery residence;
- repeated broad-reset fallback.

These are internal consistency/efficiency findings. They do not claim which DELTARUNE route was correct.

## Validation targets

The first substantial live run should preserve existing learned memory and preferably use 1× speed or verified Auto/DRSPEED timing. Primary acceptance targets are:

- no uncertainty option exceeds its budget;
- no 10–20 action weak one-sided guess chase;
- materially less high-level goal churn;
- no repeated same-link burst caused by Autonomy;
- learned warps remain reconsiderable during genuine stalls;
- productive frontiers keep winning while stale/no-new-evidence frontiers stop pinning recovery forever;
- new evidence reopens an exhausted option only when its fingerprint actually changes;
- no active-cooldown visual/geometry candidate is routed prematurely;
- no persistent unresolved Exit Detection v2 semantic leak;
- shadow replay shows no unexplained large-score selection inconsistency;
- Run Doctor v1.0.4 produces no new false-positive family on normal dialogue/menu settling.

No DeltaMod/GML changes are part of Autonomy v1.
