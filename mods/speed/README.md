# AI Speed Mod

This reversible Deltarune speed mod changes the GameMaker simulation rate,
starts at 2x, and broadcasts the active rate to the external controller over
localhost UDP port 42069.

Controls:

- F8 toggles between 1x and the previous faster speed.
- F9 decreases toward 1x.
- F10 increases toward 10x.

The change covers simulation-driven movement, dialogue, cutscenes, menus, and
battles. It does not change music or sound pitch.

## Important: compiled packages withdrawn

Do **not** enable the old speed v1.2.0 `.g3mpatch` ZIP beside telemetry v9.1.0.
Testing found that combining the independently compiled packages can corrupt
GameMaker's shared variable indexes. The visible symptom is a crash in
`gml_Object_obj_time_Step_1` claiming that global `bbox_top` was read before it
was set, even though the speed source never reads that variable.

Disable both old packages and make DeltaMod rebuild its protected game copies
from clean originals before testing another package. Do not apply either old
package to a `data.win` already modified by the other.

## Direct-CSX package

DeltaMod's current modding standard supports `.csx` source patches through an
`xdelta` patch instruction. Version 1.3.0 therefore packages `AiSpeed.csx`
directly instead of distributing independently compiled GameMaker payloads.
DeltaMod runs the installer against each selected chapter's current data model,
so variable and string indexes are assigned coherently with other source mods.

The script still fails safely when required Deltarune events are missing and
refuses duplicate installation.

Build a package only after confirming the exact game version shown by the
current DeltaMod installation:

```powershell
python .\tools\build_packages.py --target-version "EXACT_VERSION"
```

To pin the package to freshly measured clean chapter files, create a JSON file
containing exactly the included chapters:

```json
{
  "1": "SHA256_OF_CHAPTER_1_DATA_WIN",
  "2": "SHA256_OF_CHAPTER_2_DATA_WIN",
  "3": "SHA256_OF_CHAPTER_3_DATA_WIN",
  "4": "SHA256_OF_CHAPTER_4_DATA_WIN",
  "5": "SHA256_OF_CHAPTER_5_DATA_WIN"
}
```

Then run:

```powershell
python .\tools\build_packages.py `
  --target-version "EXACT_VERSION" `
  --clean-hashes .\clean_hashes.json
```

Without `--clean-hashes`, the CSX installer remains guarded by its required-event
checks, but the resulting ZIP must still be runtime-tested before release.

## Manual UndertaleModTool installation

1. Back up the chapter's clean `data.win`.
2. Open the clean file in UndertaleModTool.
3. Run `AiSpeed.csx`.
4. Read the completion message and use **Save As** for a separate modded copy.

Restore a clean copy to uninstall. Never layer the script repeatedly over an
already patched file.
