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
battles. With telemetry v6 enabled, dialogue and choices are authoritative
states rather than screen guesses. Overworld navigation systematically maps
position cells and attempted edges, tries interaction once at genuine
collisions, remembers obstacles, seeks unexplored frontiers, and records room
transitions and their exact entry/exit cells. A blockage is accepted only after
three distinct, fresh stationary telemetry samples; later successful movement
through that edge removes the contradicted wall. There are no room-specific
routes or known exits in the policy. Its learned cells, blocked edges, attempted
paths, interactables, and room warps persist across runs in
`memory/navigation.json`. Decision reasons and a per-run map summary are also
saved with each run.

The explorer treats the reverse of an observed path as known rather than as a
new frontier. It searches genuinely unknown edges first, routes through its
learned path graph, and seeks a previously crossed room warp when local map
progress stalls. Graph routes replan after each telemetry sample so Kris stops
at intermediate frontiers instead of walking past them, and a repeated
two-endpoint corridor loop triggers a perpendicular escape. Warp locations are
never inferred from room boundaries, and an object is added to memory only
after an interaction actually opens dialogue or a menu.

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

Keep the game focused. `--live` is intentionally required every time.
In live mode the controller looks for `deltarune` in both the executable name
and the visible window title, brings that window forward, and waits three
seconds before acting. If neither contains that name, pass part of either one:

```powershell
python -m deltarune_agent run --live --game-window "SURVEY_PROGRAM"
```

If matching fails, the error lists every visible window as
`executable: title`; rerun using a distinctive piece of the correct entry.

The run stops immediately if another window takes focus, preventing controls
from being typed into an unrelated application.

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
run. The GUI has separate AI and telemetry output panes and a live learned map.
Dark mode is enabled by default and can be toggled from the run controls.
The map loads persistent navigation memory, follows the current room by
default, and shows visited cells, observed paths, weighted blocked edges,
discovered interactables, room-warp endpoints, and Kris's current cell. Gold
`I` markers are interactables that produced dialogue or a menu, and purple
diamonds are room warps the AI has crossed. Click a mapped cell to inspect its
visit count, wall confidence, learned interaction approaches, and warp
destination. Frequently revisited cells become brighter, making navigation
loops visible, and Kris's marker points in the current facing direction. Its
prominent key identifies each mark, and **Clear learned map** deletes only
persistent navigation knowledge for a clean exploration run.
**Stop AI** requests a graceful stop so any held movement key is released
before the controller exits.

When a run starts, the GUI focuses the matched game before launching the child
controller. Exact detected titles and executable names are remembered in
`memory/window_titles.json`, allowing later matches even when a chapter uses a
different visible title. The controller validates the game process rather than
requiring one unchanging window handle.

## Optional telemetry mod

The external controller works without modifying Deltarune, but visual state
detection can be fooled. The optional v6 patch in `mods/telemetry/` sends room,
position, collision/motion/animation details, nearby interactable context,
dialogue, choices, and authoritative overworld/battle mode to
`127.0.0.1:42069`. See its README for the backup-first installation procedure.
The controller listens automatically; use `--no-telemetry` to run vision-only.

After installing the patch on one chapter, test its output without controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

## Architecture

`observer.py` captures frames, `perception.py` recognizes the current game
state, `visual_model.py` learns visual state prototypes from telemetry,
`policy.py` selects actions, `world_model.py` persists learned navigation,
`controller.py` sends keys, `progress.py` logs the episode, and `runner.py`
coordinates the loop. These boundaries let us later add OCR, a multimodal
planner, or reinforcement learning without changing capture or input code.

## Next milestones

1. Validate telemetry v6 and its invisible native autosave on a clean Chapter 1 patch.
2. Use the persistent room graph to plan routes toward unexplored frontiers.
3. Add OCR and a semantic goal planner for dialogue and objectives.
4. Add policy evaluation and learned-progress metrics.
5. Add battle-phase and projectile-aware control.
