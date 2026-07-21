# AI Plays Deltarune

An external, safety-first controller for experimenting with an AI agent that can
observe Deltarune, choose a small action, send keyboard input, and record its
progress. It does **not** patch or modify the game.

## Current scope

- Captures only the Deltarune window's client area (or a configured region).
- Classifies frames as dialogue, overworld, menu, battle, or unknown using
  grayscale spatial embeddings, edge maps, UI geometry, and temporal voting.
- Optionally consumes authoritative localhost telemetry for rooms and positions.
- Chooses state-specific actions through a replaceable policy.
- Sends controls only when `--live` is supplied.
- Records JSONL episode events and periodic screenshots under `runs/`.
- Includes an emergency stop: move the mouse to the upper-left corner, or press
  `Ctrl+C` in the controller terminal.

The starter policy advances dialogue, navigates menus, and moves during
battles. With telemetry v8 enabled, dialogue and choices are authoritative
states rather than screen guesses. Overworld navigation systematically maps
position cells and attempted edges, tries interaction once at genuine
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
space. If broad exploration stalls without a known useful exit, it chooses a
plausible opening from the outline of paths it has actually walked, keeps that
goal while routing there, and remembers unsuccessful edge probes.
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
re-engaged for the next untested pattern, up to three attempts. Existing long
dialogue records receive one migration retry so this works without clearing the
learned map.
These objectives are learned from visible scenes and observed consequences; no
room-specific route, NPC position, required response, or story coordinate is
supplied.
Camera telemetry tells the controller which 32-pixel world regions are
currently on screen. The screenshot is scored for anonymous visual structure
inside those regions, allowing the policy to form and test unconfirmed
possible-exit and possible-interactable guesses. It is not given room-warp
coordinates or nearby interactable identities/positions. Screen visibility,
visual guesses, inspections, and failed exit probes persist separately from
confirmed paths and objects.

The controller also builds a persistent visual memory under
`memory/room_views/`. Each captured camera frame is projected into the camera's
reported world coordinates and saved as small 32-pixel scene tiles. Scrolling
reveals and joins new tiles while anything that has never appeared on screen
remains transparent. The room's full art, undiscovered objects, and off-camera
areas are never read from game assets or filled from room dimensions. The tile
currently containing Kris is deferred until movement reveals it without
freezing the player sprite into the scenery.

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

Each run automatically reloads and updates `memory/navigation.json` and
`memory/visual_states.json`. The visual model learns from states confirmed by
telemetry, then remains usable if telemetry is temporarily unavailable. Delete
these files only when you intentionally want the controller to forget what it
has learned and begin a clean evaluation.

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

The **Live input** checkbox is off by default. Enable it before pressing
**Start AI** when you want controls sent to Deltarune; leave it off for a dry
run. The GUI has separate plain-language AI decision and telemetry output panes
and a live learned map. Repeated identical decisions are condensed so changes
of goal, path checks, exit searches, interactions, and loop recovery stand out.
Dark mode is enabled by default and can be toggled from the run controls.
The map loads persistent navigation and scene memory, follows the current room
by default, and shows the actual camera pixels the AI has seen behind visited
cells, observed paths, weighted blocked edges, unconfirmed visual guesses,
discovered interactables, confirmed room exits, and Kris's current cell. An
outlined **VISIBLE NOW** rectangle shows the exact telemetry camera footprint. Separate
toggles show or hide the remembered scene, navigation evidence, and guesses.
Use the mouse wheel to zoom around the pointer, drag with the middle or right
mouse button to move freely around the room, and press **Fit room** to restore
the automatic overview.
Unseen space stays blank. Dashed blue outlines are used as a fallback for older
screen observations that do not yet have image tiles, and cyan question marks
are the AI's own visual guesses; neither means the area is walkable or contains an object. Gold
`I` markers are interactables that produced dialogue or a menu, and numbered
purple diamonds are exits the AI has crossed. Nearby transition samples are
combined into one marker. The destination spawn position is kept as an arrival
observation and is not drawn as another exit. Click a mapped cell to inspect its
visit count, wall confidence, learned interaction approaches, and warp
destination. Frequently revisited cells become brighter, making navigation
loops visible, and Kris's marker points in the current facing direction. Its
prominent key identifies each mark, and **Clear learned map** deletes both
persistent navigation knowledge and remembered room images for a clean
exploration run.
Remembered scene tiles use four captured pixels per in-game world pixel and are
kept stable after completion, avoiding animation flicker and camera-edge tears.
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
detection can be fooled. The optional v8 patch in `mods/telemetry/` sends room,
position, camera view, collision/motion/animation details, dialogue, choices,
the game's player-control gate, and authoritative overworld/battle mode to
`127.0.0.1:42069`. See its README for the backup-first installation procedure.
It deliberately does not look up or transmit nearby interactable objects.
The controller listens automatically; use `--no-telemetry` to run vision-only.

After installing the patch on one chapter, test its output without controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

## Architecture

`observer.py` captures frames, `room_view.py` stitches only observed camera
pixels into persistent room memory, `perception.py` recognizes the current game
state, `visual_model.py` learns visual state prototypes from telemetry,
`policy.py` selects actions, `world_model.py` persists learned navigation,
`controller.py` sends keys, `progress.py` logs the episode, and `runner.py`
coordinates the loop. These boundaries let us later add OCR, a multimodal
planner, or reinforcement learning without changing capture or input code.

## Next milestones

1. Validate telemetry v8 camera/control visibility and its invisible native autosave on a clean Chapter 1 patch.
2. Use the persistent room graph to plan routes toward unexplored frontiers.
3. Add OCR and a semantic goal planner for dialogue and objectives.
4. Add policy evaluation and learned-progress metrics.
5. Add battle-phase and projectile-aware control.
