# Automatic Run Doctor

Automatic Run Doctor is the project's read-only post-run diagnosis system. Trusted v1.0.2 analyzes recorded run artifacts after normal run finalization and reports likely reliability, perception, navigation, interaction, planning, telemetry, and timing problems without changing gameplay state.

## Safety boundary

Run Doctor does **not** modify:

- learned navigation memory
- reinforcement memory or rewards
- policy settings
- gameplay configuration
- route knowledge
- dialogue knowledge
- progression answers

Developer-side verification can later annotate a report, but outside walkthrough/wiki/data.win facts must never be written into the AI's learned memory or turned into hidden route instructions.

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

### Incident intelligence

Overlapping room-specific findings are grouped into incidents so one failure does not become warning spam. Causal language is intentionally conservative. For example, overlapping capture degradation and blind search may be reported as a *plausible contributor* relationship, not proof of causation.

Run-global findings are context rather than temporal events. Trusted v1.0.2 keeps unrelated global findings as separate incidents instead of merging them merely because both span the whole run.

Health is scored separately for:

- perception/capture
- navigation
- interaction
- planning/reasoning
- telemetry/timing
- loop resistance

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

The new runs confirmed that v1.0.1's repeated-action calibration works: neither report recreated the old sustained-movement/wait/dialogue false-positive family. They also exposed four narrower issues:

- the v0.2 unresolved-evidence detector still used the final navigation snapshot. In the 863-event run, every unresolved entity hypothesis was in `room_krishallway` while every no-frontier blind probe happened later in `room_krisroom`. In the 436-event run, some hypotheses cited by the final snapshot were not proposed until well after the earlier blind probes;
- both runs ended in a long room residence after the agent had already recorded a successful warp out of that same room, but no terminal-stall action selected the learned warp. This is observed learned evidence that Doctor can diagnose without asserting the warp is the story route;
- two unrelated run-global findings could still group together because both covered the whole run, even after globals were prevented from bridging room-specific incidents;
- current-format `verification_state=unverified` high manual speed with zero DRSPEED packets was scored MEDIUM, while the equivalent historical-format condition was already calibrated to HIGH.

Trusted v1.0.2 addresses those cases:

- final-snapshot `unconsumed_observed_evidence` is replaced, when navigation history exists, by an exact lifecycle reconstruction. Evidence must be in the **same room**, must already have been recorded **before** the blind-probe decision, must still be in an actionable `proposed`/`approaching` zero-test state, and must overlap repeated blind probes before it becomes a scored finding;
- terminal room stalls can emit `known_warp_underused_during_stall` only when a successful same-room warp was recorded before the stall began and no action in that interval selected a learned warp. The finding explicitly says the warp is an observed recovery option, not a proven progression route;
- run-global findings are kept as separate incidents by default because whole-run temporal overlap is not meaningful causal evidence;
- explicit current-format high manual speed with no DRSPEED confirmation is upgraded consistently with the historical-format calibration;
- the Markdown banner now reports the actual trusted release version instead of the stale hard-coded `Trusted v1.0` label.

The three archived real runs were used as a non-overfit replay check. The lifecycle rule removes the cross-room false correlation from the 863-event run and does not score the 436-event run's isolated one-step overlaps. It still identifies repeated same-room evidence-routing conflicts in earlier rooms of the original 4,656-event archive. The learned-warp rule fires on the two new terminal stalls but not on the original classroom terminal stall, where no learned warp out of that room existed in the recorded navigation history.

These calibration rules remain diagnostic only. They do not teach the AI what route, object, dialogue option, or progression action is correct.

## Runs-page integration

The PySide6 Runs page has a **Run Doctor** tab with:

- saved Doctor health badges
- Analyze / refresh
- Compare previous
- incident summaries
- evidence step ranges
- individual findings and engineering actions

Analysis runs in `QThreadPool`; selecting or analyzing a run should not block the main GUI thread.

## Historical-run compatibility

The loader tolerates partial/malformed JSONL where possible and reads both modern run summaries and older `run_report.policy_summary` counters. Historical runs are analyzed in place without migration or mutation.

When detailed navigation-update history exists, v1.0.2 prefers exact room/time evidence reconstruction over the final navigation snapshot. Snapshot-only historical artifacts remain lower-confidence evidence and should not be used to claim an exact earlier evidence lifecycle.

## Validation model

The regression suite includes compact synthetic failure-pattern fixtures plus calibration tests derived from real archived behavior. The v1.0.2 fixtures cover cross-room evidence rejection, future-evidence backdating, actionable same-room overlap, cooldown evidence, learned-warp availability before terminal re-entry, explicit learned-warp selection, current-format high-speed verification, and unrelated run-global incident separation.

Fresh gameplay remains important because future runs can expose detector patterns that no existing archive contains. Confirmed false positives and misses should become regression fixtures before any future remediation automation is allowed.

## Next validation

Regenerate Doctor reports for the two August 14 runs using v1.0.2, then compare the corrected reports against this manual review. A later same-speed fresh run should be used as a cleaner control for objective-churn scoring; the 10x and 1x runs are useful behavioral evidence but are not a strong apples-to-apples timing comparison. Keep automatic remediation disabled until detector precision is demonstrated across multiple real runs.
