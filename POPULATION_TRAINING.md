# Independent-Game Population Training v2

Population Training runs 2–16 separate Deltarune games at the same time. Every
AI owns a complete lane:

- one visible Deltarune process and window;
- one isolated Deltarune save directory;
- one localhost telemetry port;
- one controller process;
- one private navigation, visual, strategy, and reinforcement memory; and
- one private set of detailed run artifacts.

The supervisor tiles all game windows so every AI is visible. Inputs are sent
to a specific process window, including while the controller GUI is focused.
There is no active candidate, shared player, segment handoff, or shadow-only AI.
All candidates play continuously and independently until the step limit or a
safe GUI stop.

## Starting a run

Install the current combined **AI Support** DeltaMod package in the selected
chapter. It supplies both speed synchronization and the multi-instance
telemetry/save support that this mode needs. Then select **Population training**
in the GUI, choose Chapter 1–5 and 2–16 AIs, enable **Live input**, and start.

The equivalent command is:

```powershell
python -m deltarune_agent run --training --population-size 4 --chapter 1 --live --steps 4000
```

Four AIs is the default. Balanced begins with the current strategy unchanged;
Explorer, Progress, and Loop-safe use deterministic bounded variants. Larger
populations add stable variants around those families. All coefficients remain
between 0 and 10, and a promoted winner becomes the baseline for the next run.

Each game starts from a copy of the user's current Deltarune save files under
`%LOCALAPPDATA%\DELTARUNE\ai_training\<instance-id>`. GameMaker file operations
are redirected into that instance folder by the support mod. The original save
files are never written by a training instance.

## Comparison and scoring

The Training page displays every process at once with its PID, UDP port, room,
latest action, decision count, points, normalized score, and safety state. A
green card identifies the best safely exposed candidate; amber means the lead
is still provisional; red means that lane was disqualified. A final winner is
shown only after every required gate passes.

Points come from each AI's own observed outcomes:

| Outcome | Points |
| --- | ---: |
| Non-discovery story progress | +50 |
| First entry into a new room | +15 |
| Successful choice response | +10 |
| First confirmed interactable | +3 |
| New open edge | +0.25, capped at +10 |
| Failed choice | -8 |
| Ordinary/no-response interaction | -5 |
| Observed A-B-A room bounce | -15 |
| Forced loop escape | -10 |
| Failed goal contract | -4 |
| Broad reset | -2 |
| Active decision | -0.05 |

The displayed score is `100 * total_points / (active_decisions + 64)`. Every AI
must complete at least 64 decisions. Ties prefer more story progress, fewer
safety penalties, then the stable candidate ID.

## Safety and promotion

The active profile's memory is fingerprinted before any game starts and copied
into every candidate workspace. Training never writes the profile directly.
A candidate is disqualified for a controller/scorer failure, uncertainty-budget
overrun, eight loop escapes, or four room bounces at a bounce rate of at least
two-thirds.

A winner is recommended only when every AI has enough exposure and each lane
has:

- a clean step-limit completion or safe GUI stop;
- telemetry covering at least 90% of decisions and under 5% invalid packets;
- verified matching game/AI speed (or manual 1x where verification is not
  required);
- successful keyboard-input cleanup; and
- no critical Automatic Run Doctor finding.

Promotion is always explicit. Before applying the winner, the GUI verifies that
the profile still matches its baseline fingerprint, copies the winner's entire
learned memory into same-volume staging, creates a backup, atomically replaces
the profile, and verifies the result. Failure rolls back. Cancelling, crashing,
rejecting, or failing eligibility leaves profile memory unchanged.

## Artifacts

The top-level training folder contains `training_manifest.json`,
`baseline_fingerprints.json`, and `training_scores.json`. Each
`instances/<candidate-id>/` directory contains that AI's genome, complete memory,
controller runs, predictions, navigation maps, telemetry/speed diagnostics, and
Run Doctor report. The workspace is preserved after completion for review.

This mode requires substantially more CPU and memory than a normal run because
every selected AI is a real game and controller process. Start with two AIs when
validating a new mod build or a slower computer.
