# One-Game Population Training v1

Population Training compares a configurable population of 2–16 strategy
genomes against one real Deltarune run. It does not launch multiple games and
it does not let multiple controllers send competing keys. One authoritative
policy owns perception, world evidence,
navigation state, dialogue, choices, and battle control. Isolated strategy
heads rescore the same legal Autonomy options; one candidate owns each complete
causal segment and is the only candidate whose recommendation can control input
or receive reinforcement credit.

## Candidates

The baseline genome is loaded from `memory/strategy.json`. If that optional file
does not exist, its coefficients exactly reproduce the former hard-coded
Autonomy formula. Every coefficient is clamped to 0–10.

- **Balanced** keeps the baseline unchanged.
- **Explorer** increases information and novelty while reducing distance,
  loop, failure, budget, and reinforcement influence.
- **Progress** emphasizes confidence and learned reward while accepting more
  distance and budget cost.
- **Loop-safe** strongly penalizes loops and failures.

Four AIs is the compatibility-preserving default. With two or three AIs, the
stable prefix of that list is used. Above four, the controller adds
deterministic Explorer, Progress, and Loop-safe family members at 1.25x, 1.50x,
1.75x, and 2.00x mutation intensity. Coefficients remain clamped to 0–10, IDs
remain stable, and the chosen population can be reconstructed from the run
manifest without random mutation noise.

After a reviewed promotion, the winning `strategy.json` becomes the baseline
for the next population. Each candidate also starts with a private copy of the
baseline `reinforcement.json`. Shadow scoring only reads these copies; it cannot
advance a trace, consume a budget, change a goal, update the map, or send input.

## Segments and scoring

A candidate keeps ownership through its Navigation Coherence goal contract and
all dialogue, choice, cutscene, transition, or battle consequences caused by
that contract. A segment requests an end after contract completion/failure,
observed story progress, or 64 active overworld decisions. The handoff occurs
only after safe overworld control returns and after the owning action has been
recorded and sent. The old candidate's eligibility trace is cleared.

The first `2 × population size` completed segments are two deterministic
round-robin passes. Later segments use UCB1 with coefficient 0.75. Candidate
points come only from observed outcomes:

| Outcome | Points |
| --- | ---: |
| Non-discovery story progress | +50 |
| First entry into a new room | +15 |
| Successful choice response | +10 |
| First confirmed interactable | +3 |
| New open edge | +0.25, capped at +10 per segment |
| Failed choice | -8 |
| Ordinary/no-response interaction | -5 |
| Observed A-B-A room bounce | -15 |
| Forced loop escape | -10 |
| Failed goal contract | -4 |
| Broad reset | -2 |
| Active overworld decision | -0.05 |

The displayed score is `100 * total_points / (active_decisions + 64)`. A
candidate needs two completed segments and 64 active decisions. Ties use more
story progress, fewer safety penalties, then the stable candidate ID.

## Memory isolation and promotion

Training requires live input and telemetry:

```powershell
python -m deltarune_agent run --training --population-size 8 --live --steps 4000
```

`--population-size` accepts 2–16 and defaults to 4. More candidates require a
longer run because every candidate must complete at least two segments and 64
active decisions before a winner can pass the exposure gate.

The run folder is created before policy initialization. The active profile's
SHA-256 inventory is captured, then navigation, visual state, remembered room
views, settings, and window-title memory are copied under
`training_workspace/shared_memory/`. Candidate strategies and reinforcement
stores live under `training_workspace/candidates/`. The running policy writes
only to this workspace; the profile memory stays unchanged.

The GUI run bar exposes the same **AIs** selector whenever Population training
is selected. The **Training** page keeps all 2–16 AIs visible together in a
compact grid. Every card shows its live rank, top shadow recommendation,
exposure, points, normalized score, active-segment state, and safety state. A
green outline marks a safely exposed current leader or the final recommended
winner, amber marks an underexposed provisional leader, blue marks the active
segment owner, and red marks a disqualified candidate. The page deliberately
distinguishes a live leader from a final winner, which is shown only after all
promotion gates pass. Promotion is never automatic. The operator must click
**Review and promote winner** and confirm it.

Before promotion, the current profile inventory must exactly match the training
baseline. The promotion builds and verifies a complete staged memory directory
on the same volume, overlays shared verified world/visual evidence plus only the
winner's strategy and reinforcement memory, keeps a backup, and uses atomic
directory replacement. Replacement or verification failure rolls back. The
profile and run folder receive audit history in `training_history.json` and
`promotion.json`.

## Eligibility and artifacts

A candidate is disqualified after a scorer failure, a real uncertainty-budget
overrun, eight loop escapes, or four room bounces with a bounce rate of at least
two-thirds. A winner is recommended only after all candidates meet minimum
exposure and the run has:

- a clean step-limit completion or safely deferred GUI stop;
- telemetry on at least 90% of decisions and fewer than 5% invalid packets;
- matched speed telemetry, or manual 1x where verification is not required;
- successful input cleanup; and
- no critical finding from Automatic Run Doctor.

Every population run records its exact `population_size` and `candidate_ids`
and preserves `training_manifest.json`,
`baseline_fingerprints.json`, `population_events.jsonl`,
`training_scores.json`, all candidate genome/reinforcement snapshots, the
shared staged memory, and the ordinary detailed run artifacts. Crashes,
cancellation, rejection, ineligibility, and conflicts leave profile memory
untouched and preserve the workspace for review. Older runs and profiles remain
valid because all training files and `strategy.json` are optional.
