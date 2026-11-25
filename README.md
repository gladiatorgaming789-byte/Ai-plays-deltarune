# AI Plays Deltarune

An external, safety-first controller for experimenting with an AI agent that can
observe Deltarune, choose a small action, send keyboard input, and record its
progress. The controller itself does **not** patch or modify the game. Optional
telemetry and speed mods are separate, reversible packages that DeltaMod applies
to protected copies.

## Current scope

- Captures only the Deltarune window's client area (or a configured region).
- Classifies frames as dialogue, overworld, menu, battle, or unknown using
  grayscale spatial embeddings, edge maps, UI geometry, and temporal voting.
- Optionally consumes authoritative localhost telemetry for rooms and positions.
- Chooses state-specific actions through a replaceable policy.
- Sends controls only when `--live` is supplied.
- Records buffered events, ranked AI predictions, navigation updates, periodic
  screenshots, diagnostics, memory snapshots, and exported maps under `runs/`.
- Can launch a configurable population of fully independent AIs. Each gets its
  own visible Deltarune window, save sandbox, telemetry port, controller,
  learned memory, and run artifacts; the reviewed winner can later be promoted.
- Includes an emergency stop: move the mouse to the upper-left corner, or press
  `Ctrl+C` in the controller terminal.

The starter policy advances dialogue, navigates menus, and moves during
battles. With telemetry v9 enabled, dialogue and choices are authoritative
states rather than screen guesses. Overworld navigation systematically maps
collision-foot position cells and attempted edges, tries interaction once at genuine
collisions, remembers obstacles, seeks unexplored frontiers, and records room
transitions with their confirmed source cells and observed arrival positions. A blockage is accepted only after
three distinct, fresh stationary telemetry samples; later successful movement
through that edge removes the contradicted wall. There are no room-specific
routes or known exits in the policy. Its learned cells, blocked edges, attempted
paths, interactables, and room warps persist across runs in
`memory/navigation.json`. Decision reasons and a per-run map summary are also
saved with each run.

The production explorer adds a Navigation Coherence layer above the learned
Autonomy planner. Evidence-backed targets become bounded goal contracts instead
of being reranked after every short movement. Learned frontiers are grouped into
information-gain regions, coordinate-jittered warp samples are planned as one
portal aperture, geodesic distance verifies route progress, and recent room
trajectories penalize immediate return cycles. Recovery de-escalates with
hysteresis, broad resets have a cooldown, and already learned straight corridors
can use short adaptive commitments while unknown edges remain single-step. The
Runs-page Autonomy Workbench saves and displays the exact contract, replan reason,
route progress, cycle state, and a learned-route/target overlay. See
`NAVIGATION_COHERENCE_V1.md` for the trust boundary and live validation gate.

The explorer treats the reverse of an observed path as known rather than as a
new frontier. Exploration coverage uses 32-pixel regions, while the display map
retains its detailed 8-pixel cells; this keeps the AI from trying to stand on
every tile. It searches genuinely unknown regions first, routes through its
learned path graph, and starts seeking a previously crossed room warp after a
short period without new room coverage. Known exits to less-explored rooms are
preferred. If exact sampled paths contain small gaps, the agent can route across
coarse regions Kris has actually visited; it does not invent unseen walkable
space. If broad exploration stalls without a known useful exit, it groups the
outline of paths it has actually walked into continuous boundary sections and
tests one best-supported point per section. It keeps that goal while routing
there and remembers unsuccessful probes, so one wall is not mistaken for
dozens of different exits.
This search receives no undiscovered warp coordinates from the game. Graph routes replan after each telemetry sample so Kris stops
at intermediate frontiers instead of walking past them, and a repeated
two-endpoint corridor loop triggers a perpendicular escape. After entering a
room, the entry warp receives a backtracking penalty, but remains available when
it is the only learned way out; an observed A-B-A room loop suppresses that link
for the rest of the run unless it is needed as an escape. The
backtrack avoidance covers the learned doorway area as well as its jittery
source coordinate, so graph routes do not lead Kris straight back through it.
An object is added to
memory only after an interaction actually opens dialogue or a menu.
The controller now distinguishes ordinary flavor-text interactions from actions
that lead to a scripted sequence or a new room. When story progress stalls, it
switches to a story-objective search across remembered passages and possible
static characters. A character lead requires a compact obstruction that Kris
has encountered within otherwise walked space; evidence from multiple sides is
strongest, while a small one-sided obstruction must remain visually distinctive
across repeated views. Animation is deliberately ignored because scenery can
animate while most Deltarune NPCs remain still. Kris routes to an exact learned
collision side, faces the candidate, and tests it once instead of stopping when
it merely enters the same broad map region.

Before pressing Z, the controller verifies Kris's telemetry-facing direction.
If a character-region probe produces no dialogue or menu, that exact approach
direction is remembered as unsuccessful and another learned side is tried. This
prevents a nearby desk, wall edge, or the wrong side of a seated NPC from being
selected repeatedly.

Each completed interaction now keeps a small story-memory record. The AI stores
which side was used, whether a choice menu appeared, whether the result changed
rooms or entered a scripted sequence, and whether the last result looked useful,
promising, or like ordinary flavor text. Story search prefers unresolved
promising interactions over unknown ones, and flavor-only interactions are
cooled down so Kris does not keep talking to harmless scenery while progress is
stalled. This memory is still learned only from observed consequences; it does
not read hidden dialogue text or story flags.

Exit search does not assume that a warp is a door. It prioritizes untested
continuations of paths Kris has walked, especially straight corridor or boundary
approaches, and sweeps distinct learned outline regions before falling back to
visual edge landmarks. A plain path, opening, or invisible trigger strip can
therefore be discovered through movement alone.
In a well-sampled room, completion search runs as a bounded episode rather than
staying active forever: observed non-return portals rank first by learned
outcome, strong mapped corridor geometry ranks next, and only localized,
high-confidence visual openings rank after that. A failed episode cools down
before another attempt, preventing one false seam from owning hundreds of
decisions.

High contrast alone no longer creates an interior interaction target. After an
interaction, a target that only produced ordinary dialogue is classified as a
tested non-choice interaction and is not repeatedly selected. An NPC becomes
confirmed as story-relevant only through stronger observed consequences such as
opening a choice menu or starting a scripted sequence. A completed target is not
reopened by generic navigation; response patterns are explored while a visible
choice menu remains active.

Choice telemetry confirms when a Deltarune choice object is active but does not
expose hidden option text, a selection index, or an option count. The agent
therefore learns choices through play: it identifies the visible menu as a whole,
tries bounded directional response patterns, notices when the same menu returns,
and remembers patterns followed by an observed scripted sequence or new room.
Some responses are drawn inside ordinary `obj_writer` dialogue instead of a
choicer object. The controller recognizes the repeated visible option markers in
that panel without reading hidden option text. If a response returns to the
overworld without progress, the NPC remains the active objective and is
re-engaged for the next untested pattern from a bounded response set. Existing long
dialogue records receive one migration retry so this works without clearing the
learned map.
These objectives are learned from visible scenes and observed consequences; no
room-specific route, NPC position, required response, or story coordinate is
supplied.
Camera telemetry tells the controller which 32-pixel world regions are
currently on screen. The screenshot is scored for anonymous visual structure
inside those regions, allowing the policy to form and test unconfirmed passage
guesses. A possible stationary-character lead is created only when a visible
feature also matches a compact obstruction Kris has learned through movement.
Each guess keeps a stable ID, its exact visible or collision-derived extent, a
separate routing anchor, evidence type, ranking score, and lifecycle. Reaching
a region, failing to route there, and actually testing it are distinct outcomes;
repeated route failures cool down and then reject the lead. It is not given
room-warp coordinates or nearby interactable
identities/positions. Screen visibility, visual guesses, lifecycle outcomes, and failed
exit probes persist separately from confirmed paths and objects.

The controller also builds a persistent visual memory under
`memory/room_views/`. Each captured camera frame is projected into the camera's
reported world coordinates and saved as small 32-pixel scene tiles. Scrolling
reveals and joins new tiles while anything that has never appeared on screen
remains transparent. The room's full art, undiscovered objects, and off-camera
areas are never read from game assets or filled from room dimensions. The tile
currently containing Kris is retained with only Kris's reported rectangle
masked. A stale sprite or camera seam is replaced only after two matching clean
observations, filling old holes without letting animation flicker through the
remembered scene.

## Setup

Use Python 3.11 or newer on Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start Deltarune in windowed mode, then test without sending controls:

```powershell
python -m deltarune_agent run --steps 20
```

Review the new folder under `runs/`. When capture looks correct, enable input:

```powershell
python -m deltarune_agent run --live --steps 200
```

To train several independent AIs in separate visible game windows, use:

```powershell
python -m deltarune_agent run --training --population-size 8 --live --steps 4000
```

Population Training requires live input and the current combined AI Support
mod. Choose 2–16 AIs with `--population-size`; the default four are Balanced,
Explorer, Progress, and Loop-safe. The supervisor launches and tiles one game
window per AI and targets each process directly, so no AI can control or learn
from another AI's game. Profile memory remains unchanged unless you explicitly
review and promote an eligible winner in the GUI. See `POPULATION_TRAINING.md`
for isolation, scoring, safety gates, artifacts, and rollback.

Movement keys remain held across consecutive decisions and successful paths get
a short directional commitment, producing continuous motion without sacrificing
quick collision recovery. Turns release the old direction immediately, while
dialogue and menu buttons retain a longer debounce. Avoid passing `--interval`
for normal play; that option exists only to override tuned delays while debugging.

With the separate speed mod installed, `--speed auto` (the default) follows its
localhost `DRSPEED` announcements. Action holds, cooldowns, waits, and an
explicit `--interval` are divided by the detected 1x-10x multiplier, while very
short taps retain a registration-safe floor. The startup countdown remains
normal wall-clock time. If speed packets become stale, automatic mode warns
once and safely returns the AI to 1x timing. Use `--speed 1` through
`--speed 10` for a manual override, including when telemetry is disabled.

Each normal run automatically reloads and updates `memory/navigation.json` and
`memory/visual_states.json`; Population Training updates only its run-local
staged copies until an eligible winner is explicitly promoted. The visual model learns from states confirmed by
telemetry, then remains usable if telemetry is temporarily unavailable. Delete
these files only when you intentionally want the controller to forget what it
has learned and begin a clean evaluation.

Every new run folder is self-contained enough for later diagnosis. Alongside
`events.jsonl`, it includes `predictions.jsonl` with the exact selected guess
and ranked player-observed candidates, `navigation_updates.jsonl`, `run.json`,
`run_report.json`, `summary.json`, learned-navigation and scene snapshots, and
rendered room maps with exact guess boxes and learned portal-role badges. A
`telemetry_diagnostics.json` file records packet counts, rejected/out-of-order
parts, and the latest v9 sequence health. Logs stay open and flush in small batches rather than
reopening a file on every step. `speed_diagnostics.json` and each prediction
record include the requested/detected multiplier, synchronization source,
packet age, effective delays, and loop timing.

`--live` is intentionally required every time. At startup, the controller looks
for `deltarune` in both the executable name and the visible window title,
briefly brings that window forward, and waits three seconds before acting. Once
the run starts, Deltarune does not have to remain focused. When another app is
active, controls are posted only to the remembered Deltarune window and frames
are captured directly from its client area rather than from overlapping desktop
pixels. If neither the title nor executable contains the default name, pass
part of either one:

```powershell
python -m deltarune_agent run --live --game-window "SURVEY_PROGRAM"
```

If matching fails, the error lists every visible window as
`executable: title`; rerun using a distinctive piece of the correct entry.

The GUI status changes to **Running in background** when another window has
focus. Clicking the controller, inspecting the map, or using another app does
not end the run and does not redirect Deltarune controls into that app.

## Neural experience collection

The neural observation encoder and policy/value network can be evaluated in
read-only shadow mode. To begin building a sequential dataset while the
existing hierarchical controller remains in charge, enable **Collect neural
experiences** in the desktop run bar, or run a normal bounded episode with the
opt-in collector:

```powershell
python -m deltarune_agent run --live --steps 2000 --collect-neural-experiences
```

Use `--live` only when you want the existing controller to actually
execute its selected actions; without it, the run is a dry run and records
proposed actions rather than actions sent to the game. In the GUI, enable both
**Live input** and **Collect neural experiences** for executed-action samples.
This writes append-only JSONL transitions to
`runs/neural-experiences.jsonl` by default. Use
`--neural-experiences PATH` to select another dataset file. Collection
requires fresh telemetry and ignores repeated polls of the same telemetry
sample. A transition links the observation before an action to the next newer
telemetry observation. Telemetry dropouts break the pending transition rather
than guessing what happened in between.

Rewards are deliberately marked **unassigned** (stored as zero only as a
neutral placeholder). This dataset is suitable for inspecting state/action
coverage, but it is **not ready for reinforcement-learning training** until an
outcome-based reward definition is designed and validated. The collector does
not use hidden story flags, oracle labels, benchmark answers, or screenshots
as neural inputs.

Inspect the dataset with:

```powershell
python -m deltarune_agent analyze-experiences runs/neural-experiences.jsonl
```

The report includes counts and fractions for every action, missing and
underrepresented actions, duplicate observations, named per-feature
availability, inconsistent observation widths, and telemetry sequence gaps.
The 1% action and 5% feature-availability thresholds are screening heuristics,
not training guarantees. Action counts describe the existing hierarchical
controller's behavior; they are not evidence of neural policy performance.
The report does not claim that the policy has learned or that the dataset is
balanced.

### Supervised neural policy training

The first neural training stage is an offline supervised warm-start: it learns
to predict the actions already present in the collected numerical telemetry.
It does not use rewards, next-state targets, screenshots, room text, or
developer-only calibration/oracle data.

Training uses a chronological validation split rather than a random split, so
adjacent gameplay samples are not deliberately mixed between training and
validation. The resulting checkpoint uses the portable
`deltarune-neural-policy-v1` format and can be loaded by Neural Shadow.

CLI:

```powershell
python -m deltarune_agent train-neural-policy runs/neural-experiences.jsonl --checkpoint runs/neural-policy.pt --device auto
```

The Qt Training page also exposes **Train neural policy** for the active
profile's experience dataset.

### Neural policy evaluation

After a supervised checkpoint exists, evaluate it on the same chronological
holdout contract without updating the model:

```powershell
python -m deltarune_agent evaluate-neural-policy runs/neural-experiences.jsonl runs/neural-policy.pt --device auto --output runs/neural-policy-evaluation.json
```

The evaluator reports held-out accuracy, per-action accuracy and confidence,
negative log-likelihood, Brier score, a full confusion matrix, and ten
confidence-calibration buckets. This is still numerical-only evaluation:
screenshots, room text, rewards, and developer-only oracle data are not used.



## Desktop GUI

Start the functional desktop controller with:

```powershell
python -m deltarune_agent gui
```

On Windows, you can instead double-click `Start AI GUI.bat` in the project
folder. It uses the project virtual environment when available, then falls back
to the installed `py` or `python` command.

The default interface is a custom-framed PySide6 operator console. The previous
Tk interface remains available for one transition release with:

```powershell
python -m deltarune_agent gui --legacy
```

The sidebar separates **Live Map**, **Runs**, **Training**, **Profiles**,
**Learning**, **Logs**, and **Settings**. Live Map keeps the remembered room scene dominant
and places the current action, plain-language reason, AI leads, selection
details, room summary, and map legend in a fixed inspector beside it. This
prevents evidence text from covering the map and keeps every lead aligned with
the exact scene coordinate it describes. Repeated decisions are condensed so
goal changes, path checks, exit searches, interactions, and loop recovery stand
out.

The **Live input** checkbox remains off by default. Enable it before pressing
**Start AI** to send controls to Deltarune; leave it off for a safe dry run. The
mode selector starts a normal run by default. Choosing **Population training**
requires live input and telemetry, opens a compact grid showing all 2–16 AI
scorecards at once, launches and tiles one visible game per AI, and enables an
**AIs** selector for 2–16 candidates. The grid highlights the provisional or
qualified live leader, final recommended winner, and disqualified AIs
differently. It never promotes a winner without a separate confirmation. The
speed selector defaults to **Auto**.
Choosing a manual multiplier and starting a live run applies that target to
Deltarune before control begins; **Apply to game** performs the same action
without starting a run. Its status must confirm matching game and AI speeds.
F8, F9, and F10 target only the Deltarune window and mirror the mod's
toggle/decrease/increase controls.

**Runs** reads small summaries first and loads bounded tails of large event and
prediction files in a worker thread. **Profiles** keeps learned memory and run
history isolated. **Learning** edits validated reinforcement settings. **Logs**
filters readable decisions, telemetry, build notices, and raw runtime output
without replacing the complete artifacts saved on disk.

**Settings** provides Castle Town, Cyber City, Hometown Sunset, and an
artwork-free Operator theme. Backgrounds support PNG, JPEG, WebP, GIF, MP4,
WebM, and MKV files with cover/contain/stretch modes, dimming, optional
animation, pointer parallax, and reduced-motion controls. Custom theme JSON can
be validated and imported from the same page. The two bundled backgrounds were
provided by the project owner and are documented in `THIRD_PARTY_ASSETS.md`;
select Operator when no artwork is desired.
The map loads persistent navigation and scene memory, follows the current room
by default, and shows the actual camera pixels the AI has seen behind visited
cells, observed paths, weighted blocked edges, unconfirmed visual guesses,
discovered interactables, confirmed room exits, and Kris's current cell. An
outlined **VISIBLE NOW** rectangle shows the exact telemetry camera footprint.
The **Layers** menu independently controls the scene, paths and walls, visit
heat, detail grid, learned objects and exits, guesses, and current camera.
Use the mouse wheel to zoom around the pointer, drag with the middle or right
mouse button to move freely around the room, and press **Fit** to restore
the automatic overview.
Unseen space stays blank. Numbered `E`, `C`, and `O` pins identify possible
exits, possible characters, and one-sided object leads. Their rows separate the
visual anchor from the route anchor and explain the stable lead ID, exact
extent, evidence, ranking strength, and lifecycle. Selecting a guess centers it
and outlines the complete remembered feature or learned obstruction; coarse
storage buckets are available only as an optional diagnostic layer. Adjacent
regions belonging to one feature are grouped under one marker, and the pursued
guess is highlighted. These remain unconfirmed guesses, not object identities.
Gold `I` markers are confirmed interactables. Confirmed exit apertures use
outcome-learned badges: `P` progression, `N` new area, `O` likely optional, `R`
return/backtrack, `L` loop-suppressed, and `?` not learned yet. A newly visited
room alone never earns a progression label. Nearby transition samples combine
into one aperture, while the destination spawn stays an arrival observation
rather than another exit. Click a mapped cell to inspect its visit count, wall confidence,
learned interaction approaches, and warp destination. The **AI leads**,
**Selection**, **Room**, and **Map key** tabs keep these details outside the pannable
scene so map art cannot cover the text. The **Map data** menu can clear all
learned map data or rebuild only remembered scene images.
Remembered scene tiles use four captured pixels per in-game world pixel and
require repeated matching evidence before replacing known scenery, avoiding
animation flicker, sprite ghosts, and camera-edge tears.
**Rebuild scene images** deletes only those pictures while retaining learned
paths, walls, interactions, and exits; use it once if older low-resolution or
glitched tiles are still present.
**Stop AI** requests a graceful stop so any held movement key is released
before the controller exits.

When a run starts, the GUI focuses the matched game before launching the child
controller. Exact detected titles and executable names are remembered in
`memory/window_titles.json`, allowing later matches even when a chapter uses a
different visible title. The controller validates the game process rather than
requiring one unchanging window handle.

## Optional telemetry mod

The external controller works without modifying Deltarune, but visual state
detection can be fooled. The optional v9 patch in `mods/telemetry/` sends named,
sequenced localhost packet parts containing room, instance origin, collision
foot, exact transition source, camera geometry, collision/motion/render details,
sample timing, dialogue, choices, the game's player-control gate, and
authoritative overworld/battle mode to
`127.0.0.1:42069`. See its README for the backup-first installation procedure.
It deliberately does not look up or transmit nearby interactable objects.
The controller listens automatically; use `--no-telemetry` to run vision-only.

The separate mod in `mods/speed/` starts at 2x, supports 1x through 10x, and
controls the whole GameMaker simulation without changing audio pitch. Enable
**AI Support 2.0.0** for normal combined telemetry-and-speed use. It atomically
composes Speed 1.4.0 and Telemetry 9.3.0 because current DeltaMod CSX execution
does not reliably accumulate two separate CSX patches targeting the same
`data.win`. The standalone component packages remain available for isolated
diagnosis, but must not be enabled together. These runtime-test candidates
target Steam build 24484059 (Chapter 5 v0.0.253). See
`MOD_PACKAGE_STATUS.md` for exact hashes and the remaining Chapters 1-5 live
release gate.

After installing the patch on one chapter, test its output without controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

## Room-transition benchmark

Each run now records the AI policy's observed room-change predictions in
`predictions.jsonl`, separately from the raw telemetry evidence. The run also
preserves the ordered telemetry trace packets that the policy actually
observed. This makes it possible to compare the baseline room-change detector
against explicit transition metadata without exposing developer oracle records
to the gameplay policy.

After a run, generate its developer-only benchmark with:

```powershell
python -m deltarune_agent.transition_benchmark runs/<run-folder>
```

The command writes `transition_benchmark/oracle.json`,
`transition_benchmark/evaluation.json`,
`transition_benchmark/visual_exit_evaluation.json`,
`transition_benchmark/visual_exit_calibration.json`, and
`transition_benchmark/summary.json`. It automatically reads that run's
`events.jsonl` and `predictions.jsonl`. The summary reports matched,
missed, false-positive, and wrong-destination counts; precision, recall, F1;
and measured detection latency. It also writes
`visual_exit_evaluation.json`, which spatially associates the earliest
observed exit-candidate anchor in each room region with later, explicitly
confirmed room transitions. It reports transition recall, semantic-ready
recall, lead time in run steps, and candidate-to-crossing distance. Candidates
without a later crossing are labeled **unvalidated**, not false positives,
because the run may have ended before the agent tested them.

The same command also writes `visual_exit_calibration.json`: an offline
sweep over the visual scores actually observed in the run, plus a breakdown by
candidate source. It shows how transition recall changes as a score cutoff is
raised. This is diagnostic only and never changes the live detector. Since
untested candidates are not known negatives, the report deliberately does not
claim precision or an optimal threshold; those require explicit tested-negative
labels.

The spatial matcher defaults to a 16-world-unit radius (two 8-unit navigation cells) and a 180-step lead
window. Tune those assumptions with `--exit-match-radius` and
`--exit-max-lead-steps`; these are evaluation parameters, not game knowledge.
The optional `--predictions <file.json>` argument accepts a separate JSON-list
room-transition prediction stream.

The oracle accepts only explicit transition metadata. It does not infer a
transition merely because adjacent telemetry packets have different room
names. The oracle and benchmark modules are post-run developer tooling and
must not be imported by gameplay decision code.

## Architecture

`observer.py` captures frames, `room_view.py` stitches only observed camera
pixels into persistent room memory, `perception.py` recognizes the current game
state, `visual_model.py` learns visual state prototypes from telemetry,
`screen_regions.py` extracts anonymous localized openings, `map_guesses.py`
turns observations into feature-sized stable leads, `policy.py` selects actions,
`navigation_semantics.py` classifies observed portal outcomes, `world_model.py`
persists learned navigation, `controller.py` sends keys, `progress.py` and
`run_artifacts.py` package the episode, and `runner.py`
coordinates the loop. These boundaries let us later add OCR, a multimodal
planner, or reinforcement learning without changing capture or input code.

## Next milestones

The ordered, evidence-gated project plan is maintained in `ROADMAP.md`.


## Developer-only tested visual-exit dataset

After a run, the transition benchmark creates an editable
`transition_benchmark/exit_adjudications.template.jsonl`. Copy it to
`transition_benchmark/exit_adjudications.jsonl` and review each candidate.
Allowed labels are:

- `confirmed_exit`: explicitly observed to lead to a room transition.
- `tested_non_exit`: explicitly tested and established not to be an exit.
- `unreachable`: could not be tested; excluded from supervised examples.
- `unknown`: not adjudicated; excluded from supervised examples.

Both supervised labels require a non-empty evidence note. The benchmark never
turns an unmatched candidate into a negative example. To use a separately
stored review file, pass `--exit-adjudications path\to\labels.jsonl`:

```powershell
py -m deltarune_agent.transition_benchmark runs/<run-folder>
```

The output includes `tested_exit_dataset.jsonl`,
`tested_exit_dataset_summary.json`, and
`tested_exit_calibration.json`. The calibration report scores the existing
visual heuristic against only explicitly reviewed positive and negative labels.
It reports precision, recall, specificity, F1, a diagnostic score-threshold
sweep, source breakdowns, and leave-one-room-out threshold validation when
there is enough labeled data. Unknown and unreachable candidates are excluded,
never converted into negatives. The selected threshold is diagnostic only: no
model is trained and no gameplay threshold is changed. These are developer-side
evaluation artifacts only; they are not loaded by the gameplay agent or its
action policy. After reviewing `exit_adjudications.jsonl`, rerun the benchmark
to refresh the dataset and calibration report.


## Neural environment interface

Phase 1 adds `deltarune_agent/neural_env.py`, a vision-free numerical observation contract for the future neural policy. It converts the telemetry the agent is already permitted to observe into a fixed-width vector of finite values in approximately `[-1, 1]`, plus a parallel availability mask for optional fields.

The observation includes player position and foot position, horizontal/vertical velocity, speed, motion direction, room/camera geometry, collision bounds, sprite dimensions/scales, timing, observed movement deltas, numerical room identity, interaction/control state, facing and game mode, and the previous action/duration. It does **not** expose screenshots, room art, hidden interactable identities/coordinates, story text, or developer-only benchmark/oracle data.

The encoder is deliberately pure Python and has no ML dependency yet. The next neural milestone can consume `NeuralObservation.combined()` directly, while a later recurrent model can stack these observations over time without changing the environment contract.

Vision remains an optional future input channel rather than a prerequisite for the neural agent.


## Neural experience interface

The neural stack now has a transition/replay layer in `deltarune_agent/experience.py`. A `NeuralTransition` stores the numerical observation, action, reward, next observation, terminal flag, and telemetry sequence IDs. `NeuralReplayBuffer` provides bounded storage and deterministic sampling, while JSONL helpers make transitions portable for offline training.

This layer intentionally does **not** calculate rewards or inject developer-only labels. Reward assignment remains an environment/controller responsibility. The next step is to connect this transition interface to live/shadow execution so real observations and actions can be recorded without giving the untrained neural policy control of the game.


## Neural Shadow GUI

The existing desktop controller now exposes a **Neural Shadow** button in its
run controls. It opens a separate read-only monitor that consumes the same
localhost telemetry stream as the controller, encodes the fixed numerical
observation vector, and runs the neural policy/value network. It can also be
opened directly with:

```powershell
python -m deltarune_agent neural-shadow
```

Shadow mode is intentionally non-authoritative:

- It never calls `KeyboardController` and cannot send game input.
- Vision is disabled; screenshots and room art are not part of the neural input.
- It displays the predicted action, confidence, value estimate, and all eight
  action probabilities.
- It shows the named numerical features and their availability mask so the
  observation seen by the network is inspectable.
- Predictions are recorded under `runs/neural-shadow/`.
- An optional PyTorch checkpoint can be loaded from the GUI.
- The existing normal AI run remains unchanged and continues to be the only
  controller that can send gameplay input.

This makes the GUI useful for collecting and validating real neural
observations before the model is ever allowed to control Deltarune.


## Neural shadow mode

The neural stack now has a non-authoritative shadow runner in `deltarune_agent/neural_shadow.py`. It consumes the same telemetry-based numerical observation contract as the future neural controller, produces an action distribution and value estimate, and records predictions/transitions without sending keyboard input.

Shadow mode is intentionally separate from the existing gameplay policy: the existing policy remains authoritative. This makes it safe to collect real observations before the neural model is trusted with control. A checkpoint can be loaded later for trained-model evaluation.
