# Automatic Run Doctor

Automatic Run Doctor is the project's read-only post-run diagnosis system. Trusted v1.0.1 analyzes recorded run artifacts after normal run finalization and reports likely reliability, perception, navigation, interaction, planning, telemetry, and timing problems without changing gameplay state.

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
- unresolved observed character/interactable evidence correlated with blind search
- objective churn
- unusually high evidence-filter/suppression pressure
- missing/degraded telemetry
- unverified, stale, or mismatched game-speed timing

### Incident intelligence

Overlapping findings are grouped into incidents so one failure does not become warning spam. Causal language is intentionally conservative. For example, overlapping capture degradation and blind search may be reported as a *plausible contributor* relationship, not proof of causation.

Run-level findings are context, not temporal bridges: a global low-visuality or suppression finding cannot glue otherwise unrelated room-specific intervals into one giant incident.

Health is scored separately for:

- perception/capture
- navigation
- interaction
- planning/reasoning
- telemetry/timing
- loop resistance

## v1.0.1 real-run calibration

The original 4,656-event classroom-heavy Run 20 archive became available after v1.0 was released, so its automatic report was compared against a manual whole-run review and then used as a real calibration case.

The first v1.0 report correctly rediscovered the major classroom stall/blind-search problem, severe visual-capture degradation, unresolved observed entity evidence, and suspicious evidence-filter pressure. It also exposed several calibration defects:

- all 28 raw repeated-action findings in that run were false positives. Sustained movement streaks showed substantial telemetry displacement, waits were deliberate transition-control-lock handling, and confirm streaks were dialogue/cutscene advancement;
- run-global findings with `room=None` could act as transitive bridges and collapse unrelated room intervals into a single incident;
- the historical speed diagnostics lacked the newer `verification_state` field, so v1.0 missed the recorded manual 10x setting despite `detected_multiplier=null`, `synchronized=false`, and zero DRSPEED packets;
- the historical `objective_changes=100` value could be the old retained-history cap and therefore should not be treated as an exact count;
- long room residence that later ended in a successful exit should be treated more cautiously than an end-of-run stall.

Trusted v1.0.1 calibrates those cases using only recorded run evidence:

- repeated movement is suppressed as a problem only when telemetry shows meaningful displacement or a room change; blocked/repeating movement with no meaningful progress remains a finding;
- repeated `wait` during control lock and repeated `confirm` while advancing dialogue/cutscenes are treated as expected behavior;
- historical high manual speed with no detected multiplier/DRSPEED verification receives a timing finding even when the old artifact format lacks `verification_state`;
- the old objective-history cap is surfaced as an explicit uncertainty;
- completed long-room residences are downgraded to efficiency signals when the run later exits and blind probing did not dominate the interval;
- global findings no longer bridge unrelated room-specific incidents.

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

## Validation model

The regression suite includes both compact synthetic failure-pattern fixtures and calibration tests derived from observed behavior in the real archived Run 20. The real-run calibration specifically protects against productive sustained movement, deliberate control-lock waits, dialogue/cutscene confirms, historical speed-format gaps, the objective-history cap, and global-incident bridging.

Fresh gameplay remains important because future runs can expose detector patterns that no existing archive contains. Confirmed false positives and misses should become regression fixtures before any future remediation automation is allowed.

## Next validation

Rerun Run Doctor v1.0.1 against the archived calibration run, then analyze the next fresh gameplay session. Compare each automatic report with a manual whole-run review, especially incident boundaries and health scoring. Keep automatic remediation disabled until detector precision is demonstrated across multiple real runs.
