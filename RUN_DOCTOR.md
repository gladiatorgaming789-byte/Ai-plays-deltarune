# Automatic Run Doctor

Automatic Run Doctor is the project's read-only post-run diagnosis system. Trusted v1.0.4 analyzes recorded run artifacts after normal run finalization and reports likely reliability, perception, navigation, interaction, planning, Autonomy, telemetry, and timing problems without changing gameplay state.

## Safety boundary

Run Doctor does **not** modify:

- learned navigation memory
- reinforcement memory or rewards
- policy settings
- gameplay configuration
- route knowledge
- dialogue knowledge
- progression answers

Developer-side verification can annotate a report, but outside walkthrough/wiki/data.win facts must never be written into the AI's learned memory or turned into hidden route instructions.

## Automatic behavior

Normal `run` commands install the trusted post-run hook before the episode tracker is created. The Doctor runs only after the tracker's normal `finish()` method completes.

If Doctor analysis itself fails, the completed gameplay run remains valid. A best-effort `run_doctor_error.json` records the Doctor failure instead of replacing the original runtime result.

Successful analysis creates:

- `run_doctor.json` — deterministic machine-readable findings, incidents, health scores, and optional comparison data
- `run_doctor.md` — concise human-readable diagnosis

## Manual CLI

Analyze one recorded run:

```powershell
python -m deltarune_agent run-doctor path\to\run
```

Print JSON without saving files:

```powershell
python -m deltarune_agent run-doctor path\to\run --json --no-save
```

Compare a candidate run with an older baseline:

```powershell
python -m deltarune_agent run-doctor path\to\candidate --compare path\to\baseline
```

Comparisons are labeled `strong`, `moderate`, or `weak`. Weak comparisons produce an inconclusive aggregate verdict rather than pretending unlike starting states/configurations are directly comparable.

## Detector families

### Foundation

- long room stays/stalls
- progress-aware repeated-action streaks
- rapid A → B → A room returns
- low visual-validity ratio
- long invalid-frame streaks

### Reasoning and evidence use

- explicit no-frontier/blind-search streaks
- repeated structured interactions with no response
- actionable observed character/interactable evidence bypassed during blind search, correlated by exact room and recorded evidence lifecycle
- known learned warps left unused during terminal room stalls
- objective churn
- unusually high evidence-filter/suppression pressure
- missing/degraded telemetry
- unverified, stale, or mismatched game-speed timing

### Run21 / eight-run calibration

Trusted v1.0.3 added and calibrated findings for the eight-run August 15 batch:

- long one-sided entity/interaction guess chase streaks;
- repeated two-room link ping-pong;
- unresolved Exit Detection v2 candidates leaking into semantic `possible_exit` memory;
- stale/frozen visual capture while telemetry proves player movement continues.

Two false-positive families were corrected at the same time. Confirmed save/menu settling is not called stuck behavior when the run demonstrates the menu later closes, and repeated no-response interactions are grouped by the actual/adjacent target cells rather than merely room plus facing direction.

### Autonomy v1

Trusted v1.0.4 retains every v1.0.3 calibration and adds read-only checks over recorded Autonomy snapshots:

- repeated recovery-goal switching within one room/story epoch;
- bidirectional recovery-level thrashing (monotonic escalation through exhausted tiers is explicitly allowed);
- uncertainty-budget overrun, which is an internal invariant failure;
- repeatedly selecting a materially lower-scored option without a recorded goal-commitment hold;
- prolonged residence at controlled-backtrack/broad-reset recovery level;
- repeated broad-reset fallback.

These findings assess internal consistency and efficiency. They do **not** claim that any particular room, warp, object, dialogue choice, or route was the correct progression answer.

## Incident intelligence

Overlapping room-specific findings are grouped into incidents so one failure does not become warning spam. Causal language is intentionally conservative. For example, overlapping capture degradation and blind search may be reported as a *plausible contributor* relationship, not proof of causation.

Run-global findings are context rather than temporal events. Trusted releases keep unrelated global findings as separate incidents instead of merging them merely because both span the whole run.

Health is scored separately for:

- perception/capture
- navigation
- interaction
- planning/reasoning
- telemetry/timing
- loop resistance

Autonomy findings currently contribute through the existing planning/navigation categories rather than inventing a new incompatible health schema.

## v1.0.1 real-run calibration

The original 4,656-event classroom-heavy Run 20 archive became available after v1.0 was released, so its automatic report was compared against a manual whole-run review and then used as the first real calibration case.

The first v1.0 report correctly rediscovered the major classroom stall/blind-search problem, severe visual-capture degradation, unresolved observed entity evidence, and suspicious evidence-filter pressure. It also exposed several calibration defects:

- all 28 raw repeated-action findings in that run were false positives. Sustained movement streaks showed substantial telemetry displacement, waits were deliberate transition-control-lock handling, and confirm streaks were dialogue/cutscene advancement;
- run-global findings with `room=None` could act as transitive bridges and collapse unrelated room intervals into a single incident;
- the historical speed diagnostics lacked the newer `verification_state` field, so v1.0 missed the recorded manual 10x setting despite `detected_multiplier=null`, `synchronized=false`, and zero DRSPEED packets;
- the historical `objective_changes=100` value could be the old retained-history cap and therefore should not be treated as an exact count;
- long room residence that later ended in a successful exit should be treated more cautiously than an end-of-run stall.

Trusted v1.0.1 calibrated those cases using only recorded run evidence. Productive sustained movement, deliberate control-lock waits, and dialogue/cutscene confirms are no longer treated as stuck-action loops; historical high-speed verification gaps are surfaced; completed room residences are downgraded to efficiency signals when appropriate; and run-global findings no longer bridge unrelated room-specific incidents.

## v1.0.2 multi-run calibration

Two additional real runs, `20260814T033257.403Z` and `20260814T033501.085Z`, were reviewed against their automatic v1.0.1 reports and raw event/navigation histories.

Trusted v1.0.2 replaced loose final-snapshot evidence correlation, when navigation history exists, with exact lifecycle reconstruction. Evidence must be in the same room, recorded before the blind-search decision, and still actionable. Terminal room stalls can emit `known_warp_underused_during_stall` only when a successful same-room warp was already learned and no action in the interval selected it; the finding explicitly calls that warp an observed recovery option, not a proven progression route. Current-format and historical high-speed verification gaps are handled consistently, and unrelated run-global findings remain separate.

The archived real runs were used as non-overfit replay checks. These rules remove cross-room and future-evidence false correlations while preserving real same-room evidence-routing conflicts.

## v1.0.3 eight-run calibration

The next eight consecutive live runs were reviewed as one learning sequence. The batch showed persistent exit-semantic pollution, weak one-sided entity chases, frozen-but-nonblank capture intervals, repeated room-link bursts, and learned recovery links being unreachable under older suppression state. Run21 fixed those behavior families, while Doctor v1.0.3 added matching diagnostics and corrected the save/menu and spatial-interaction false positives described above.

The calibration deliberately left objective churn visible. High objective-change counts are treated as a symptom to reduce by improving planning behavior rather than a reporting value to smooth away.

## v1.0.4 Autonomy instrumentation

Autonomy v1 emits a compact decision snapshot with recovery level, story epoch/stall, active goal, commitment state, selected option, top ranked options, score components, loop risk, and uncertainty-budget state. Doctor v1.0.4 consumes only those recorded fields.

A lower-scored selected option is not automatically a finding: the detector explicitly exempts a recorded short goal-commitment hold. Similarly, monotonic recovery escalation is not called thrashing. The detector requires repeated upward **and** downward changes in one room/story epoch before reporting recovery-level instability.

The shadow evaluator in `deltarune_agent.autonomy_shadow` is separate from Doctor and can re-score recorded option snapshots with generic alternative weights. It is post-run only and cannot send input to DELTARUNE.

## Runs-page integration

The PySide6 Runs page has a **Run Doctor** tab with:

- saved Doctor health badges
- Analyze / refresh
- Compare previous
- incident summaries
- evidence step ranges
- individual findings and engineering actions

Analysis runs in `QThreadPool`; selecting or analyzing a run should not block the main GUI thread.

The neighboring **Autonomy** tab is a separate read-only workbench. It explains the latest recovery goal and ranked choices and reports shadow-ranking disagreements across the loaded prediction window. It does not run Doctor, change weights, edit memory, or send controls.

## Historical-run compatibility

The loader tolerates partial/malformed JSONL where possible and reads both modern run summaries and older `run_report.policy_summary` counters. Historical runs are analyzed in place without migration or mutation.

When detailed navigation-update history exists, trusted calibration prefers exact room/time evidence reconstruction over the final navigation snapshot. Snapshot-only historical artifacts remain lower-confidence evidence and should not be used to claim an exact earlier evidence lifecycle.

Older runs that predate Autonomy snapshots simply produce no Autonomy v1.0.4-specific findings; they remain fully analyzable by the earlier detector families.

## Validation model

The regression suite includes compact synthetic failure-pattern fixtures plus calibration tests derived from real archived behavior. Current Autonomy fixtures cover recovery thresholds, frontier grace/escalation, evidence-only budget reset, budget exhaustion, goal commitment and strong-evidence breaks, loop penalty semantics, long-horizon learned-graph planning, normal Run21 delegation, learned interaction setup, visual cooldown composition, shadow replay, and v1.0.4 Doctor findings/negative controls.

Fresh gameplay remains important because future runs can expose detector patterns that no existing archive contains. Confirmed false positives and misses should become regression fixtures before any future automatic remediation is allowed.

## Next validation

Run a substantial Autonomy v1 session while preserving learned navigation memory. Prefer 1× speed or verified Auto/DRSPEED timing. Compare the automatic v1.0.4 report against a manual whole-run review and inspect the Autonomy snapshots directly.

Primary targets are bounded uncertain-candidate cost, naturally lower objective churn, no new same-link burst, productive frontier preference without stale-frontier lock-in, learned-warp reconsideration during real stalls, zero uncertainty-budget overrun, and no unexplained large-score shadow-selection disagreement.
