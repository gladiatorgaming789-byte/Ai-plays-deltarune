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

Each run automatically reloads and updates `memory/navigation.json` and
`memory/visual_states.json`. The visual model learns from states confirmed by
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

The sidebar separates **Live Map**, **Runs**, **Profiles**, **Learning**,
**Logs**, and **Settings**. Live Map keeps the remembered room scene dominant
and places the current action, plain-language reason, AI leads, selection
details, room summary, and map legend in a fixed inspector beside it. This
prevents evidence text from covering the map and keeps every lead aligned with
the exact scene coordinate it describes. Repeated decisions are condensed so
goal changes, path checks, exit searches, interactions, and loop recovery stand
out.

The **Live input** checkbox remains off by default. Enable it before pressing
**Start AI** to send controls to Deltarune; leave it off for a safe dry run. The
speed selector defaults to **Auto**. Its status shows game speed, effective AI
speed, and synchronization source, while F8, F9, and F10 target only the
Deltarune window and mirror the mod's toggle/decrease/increase controls.

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
its v1.2.0 Chapters 1-5 DeltaMod package beside the separate telemetry v9.1.0
package. Both releases target Steam build 24484059 (Chapter 5 v0.0.253).
Multi-code-patch merging requires G3MTool 1.2.5 or newer; the included
backup-first updater in `mods/speed/tools/` replaces DeltaMod 2.0.1's affected
1.2.1 merge tool without modifying Deltarune. Optional per-chapter speed
archives and a manual UndertaleModTool script are included. There is no
combined package.

After installing the patch on one chapter, test its output without controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

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

1. Validate telemetry v9 packet health, exact transition sources, and its
   invisible native autosave on a clean Chapter 1 patch.
2. Compare the next live run's prediction report with actual story progress and
   tune only evidence-backed thresholds.
3. Add optional on-screen text understanding for richer dialogue goals without
   reading hidden game state.
4. Add battle-phase and projectile-aware control.
