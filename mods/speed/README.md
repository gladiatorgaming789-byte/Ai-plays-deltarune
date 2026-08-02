# AI Speed Mod

This is a separate, reversible Deltarune Chapters 1-5 speed mod. It changes the
GameMaker simulation rate, starts at 2x, and broadcasts the active rate to the
AI controller over localhost UDP port 42069.

Controls:

- F8: toggle between 1x and the previous faster speed
- F9: decrease toward 1x
- F10: increase toward 10x

The change covers simulation-driven overworld movement, dialogue, cutscenes,
menus, and battles. It does not change music or sound pitch.

## DeltaMod installation

Use `AI-Speed-All-Chapters-DeltaMod-v1.2.0.zip` in `deltamod/`. It is a real
standalone speed mod and can be enabled beside the separate telemetry v9.1.0
mod. Version 1.2.0 targets Steam build 24484059 (Chapter 5
v0.0.253); it will reject other chapter data instead of risking a bad patch.
DeltaMod works on protected copies of the chapter data, so the Steam originals
remain untouched.

Remove the superseded speed v1.1.0 package from DeltaMod before importing
v1.2.0. After updating Deltarune, use **Options > Advanced > Precalculate game
hashes** once. The current game download contains stale `data.win.hash`
sidecars for Chapters 2-5, while DeltaMod uses those cache files for advanced
compatibility checks. Recalculating refreshes the protected installation's
cache from the actual current files; the packages deliberately contain the
real current SHA-256 values rather than the stale sidecar values.

Multi-code-patch merging requires G3MTool 1.2.5 or newer. DeltaMod 2.0.1
originally bundled G3MTool 1.2.1, whose Undertale merge path can relink global
variables incorrectly even when two mods edit different events. Run the
backup-first `Update-DeltaMod-G3MTool.ps1` helper in `tools/` once if
DeltaMod still reports 1.2.1. No combined telemetry-and-speed package is used.
Close DeltaMod first, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Update-DeltaMod-G3MTool.ps1
```

The helper verifies both official-release SHA-256 hashes before replacing the
tool. To restore DeltaMod's original executable:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Update-DeltaMod-G3MTool.ps1 -Restore
```

## Reproducible ZIP-only releases

`deltamod/` intentionally contains only the six installable ZIP archives.
Loose `.g3mpatch` files are generated under the ignored `.build/payloads/`
directory, packaged into the ZIPs, and never need to be committed:

```powershell
python .\tools\build_payloads.py
python .\tools\build_packages.py
```

The text manifest `release_1.2.0.json` records the source, embedded payload,
verified clean-game, build provenance, and final ZIP hashes. Git treats ZIP
and G3M patch formats as binary through the repository's `.gitattributes`.

To build from the supplied clean game archive rather than the installed files:

```powershell
python .\tools\build_payloads.py --game-archive "C:\path\to\Deltarune.zip"
python .\tools\build_packages.py
```

The archive builder hashes the actual `chapterN_windows/data.win` bytes and
ignores the archive's `data.win.hash` sidecars. It extracts only temporary
verified chapter files and never writes into the archive or game directory.

## Manual UndertaleModTool installation

1. Back up the chapter's clean `data.win`.
2. Open that clean file in UndertaleModTool.
3. Run `AiSpeed.csx`.
4. Read the completion message, then use **Save As** to write a separate
   modded copy.

The script rejects unsupported files and duplicate installation. Restore the
clean backup to uninstall it; do not layer it repeatedly over an already
patched file. A successful script compile confirms event compatibility, but
the packaged v1.2.0 payloads are only for the verified build above.
