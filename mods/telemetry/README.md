# Deltarune telemetry mod

This optional source patch adds localhost-only runtime telemetry for the external
AI controller. Protocol v9 reports player-observable state without exposing
hidden progression instructions, dialogue text, choice text, option counts, or
nearby interactable identities.

Telemetry includes:

- authoritative overworld, battle, dialogue, choice, and save-menu state;
- room ID/name and instance origin;
- player collision-foot position and bounding box;
- camera geometry, facing, sprite, motion, render, and timing details;
- sequenced packet parts so delayed UDP datagrams cannot mix two frames;
- exact observed transition-source evidence;
- one native early-game autosave per session for repeatable testing.

Packets use UDP to `127.0.0.1:42069` only. The Python controller also binds only
to localhost.

## Important: compiled packages withdrawn

Do **not** enable telemetry v9.1.0's compiled `.g3mpatch` ZIP beside speed
v1.2.0. Combining those independently compiled packages can corrupt GameMaker's
shared variable indexes. The observed crash occurs in
`gml_Object_obj_time_Step_1` and incorrectly reads global `bbox_top`, even though
telemetry reads `self.bbox_top` only inside player and battle draw hooks and does
not modify `obj_time`.

Disable both old packages and make DeltaMod rebuild its protected chapter copies
from clean originals before testing another package. Never apply one old package
to a `data.win` already modified by the other.

## Direct-CSX package

DeltaMod's current modding standard supports `.csx` source patches through an
`xdelta` patch instruction. Package version 9.2.0 therefore includes
`AiTelemetry.csx` directly. DeltaMod runs the installer against each selected
chapter's current data model, allowing GameMaker variable and string indexes to
be assigned coherently with other source patches.

The installer checks every required event before making changes, refuses an
unsupported chapter, and refuses to layer v9 over an older telemetry sender.
Telemetry protocol remains v9, so the Python receiver does not require a
protocol change.

Build only after confirming the exact game version shown by the current
DeltaMod installation:

```powershell
python .\tools\build_packages.py --target-version "EXACT_VERSION"
```

To pin the package to refreshed clean files, provide a JSON object containing
exactly the included chapters and their SHA-256 values:

```powershell
python .\tools\build_packages.py `
  --target-version "EXACT_VERSION" `
  --clean-hashes .\clean_hashes.json
```

Without `--clean-hashes`, required-event checks still protect installation, but
the resulting ZIP must be runtime-tested before release.

## Manual UndertaleModTool installation

1. Close Deltarune and back up the chapter's clean `data.win`.
2. Open the clean file in UndertaleModTool.
3. Run `AiTelemetry.csx`.
4. Read the completion message and use **Save As** for a separate modded copy.
5. Start the controller and validate without sending controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

Room, origin, sprite, facing, and camera values should appear while Deltarune is
active. Restore a clean copy to uninstall. After a game update, reapply only to
the new clean file rather than reusing an older patched backup.
