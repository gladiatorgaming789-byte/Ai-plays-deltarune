# DeltaMod telemetry package

The current user-facing release is:

```text
Telemetry-All-Chapters-DeltaMod-v9.1.0.zip
```

Import that ZIP directly into DeltaMod and enable it. Do not extract the ZIP,
run a loose `.g3mpatch`, or overwrite an installed `data.win` by hand.

Version 9.1.0 preserves telemetry protocol v9 and targets Deltarune Steam build
24484059 (Chapter 5 v0.0.253). Chapter 1 is unchanged from the prior release;
the clean Chapter 2-5 files and their checksums changed in the game update.
The old v9.0.1/v9.0.2 packages therefore cannot be applied to the current
files and have been retired from this directory.

## Separate speed-mod compatibility

Telemetry and speed remain two independently enabled mods. This telemetry
release contains seven semantic `CodeEntries` changes and no sound, texture,
room, or other asset changes. The speed release changes only `obj_time` Begin
Step, so the packages do not replace the same event.

Combining code patches requires G3MTool 1.2.5 or newer. Older G3MTool releases
could corrupt GameMaker variable references while relinking two patches; that
was the source of the earlier `obj_time`/`bbox_top` crash. The current package
builder refuses payloads made by an older tool, and its manifest declares
`mergeSupport: true`.

## What is validated

The release builder:

- reads clean `chapterN_windows/data.win` files from the supplied
  `Deltarune.zip` without modifying the installed game;
- verifies the current SHA-256 and MD5 for every chapter;
- compiles `AiTelemetry.csx` separately against all five files;
- rejects changes outside the seven expected telemetry code events;
- applies each minimized payload back to its exact clean source;
- checks the protocol and autosave markers in every reconstructed file;
- records source, payload, and reconstructed-file hashes;
- rejects stale payloads if the source script, game build, hashes, or G3MTool
  version changed before packaging; and
- validates the finished root-only DeltaMod ZIP and its per-chapter targets.

Loose `.g3mpatch` files are reproducible build intermediates under the ignored
`mods/telemetry/.build` directory. Only the finished ZIP is kept in this
release directory, so GitHub never needs to handle loose patch files.

## Maintainer rebuild

With the clean `Deltarune.zip` in the game directory and DeltaMod's G3MTool
1.2.5+ installed:

```powershell
.\.venv\Scripts\python.exe .\mods\telemetry\tools\build_payloads.py
.\.venv\Scripts\python.exe .\mods\telemetry\tools\build_packages.py
```

The manual UndertaleModTool-compatible source remains at
`mods/telemetry/AiTelemetry.csx`.
