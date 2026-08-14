# Automatic Run Doctor

Automatic Run Doctor is the project's read-only post-run diagnosis system. Trusted v1.0 analyzes recorded run artifacts after normal run finalization and reports likely reliability, perception, navigation, interaction, planning, telemetry, and timing problems without changing gameplay state.

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

Normal `run` commands install the v1.0 post-run hook before the episode tracker is created. The Doctor runs only after the tracker's normal `finish()` method completes.

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
- repeated-action streaks
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

Health is scored separately for:

- perception/capture
- navigation
- interaction
- planning/reasoning
- telemetry/timing
- loop resistance

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

The v1 regression suite includes a compact synthetic fixture that reproduces the observed *shape* of the previously diagnosed classroom-heavy failure: a long same-room stall, visual-capture degradation, blind probing, unresolved observed evidence, objective/filter churn, and unverified high-speed timing.

That fixture is intentionally synthetic. It is not presented as a replay of the original 4,656-event archived run. Fresh real gameplay remains the final runtime validation for detector thresholds and false-positive rates.

## Next validation

After v1.0 passes repository CI, run a fresh gameplay session on the Doctor-enabled agent. Compare the automatically generated report against a manual whole-run review. Tune thresholds only from observed evidence and add any confirmed misses/false positives as regression fixtures before allowing future remediation automation.
