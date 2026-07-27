# DeltaMod telemetry package

> **Compatibility warning:** the currently generated Chapter 5 payload is stale and should not be distributed. It was built against an older Chapter 5 `data.win`. Deltarune Chapter 5 was updated after that source file was captured, so current installations can reject it as the wrong xdelta source. Regenerate the Chapter 5 payload and all-chapters ZIP from a clean current Chapter 5 file before release.

The intended user-facing release artifact is:

```text
AI-Telemetry-DeltaMod.zip
```

Once regenerated for the current game build, it should be ready to import into DeltaMod. **Do not extract it, do not run UndertaleModTool, and do not manually patch any `data.win` file.** Import the ZIP itself, then enable or disable the mod through DeltaMod.

## What “patch” means here

DeltaMod handles the game-file operation automatically. End users do not run a patcher and do not replace their game files by hand.

Because telemetry modifies GML code stored inside each chapter's `data.win`, the importable ZIP contains compact `.xdelta` payloads. When the mod is enabled, DeltaMod reads `modding.xml` and applies those payloads inside its managed game copy. Disabling the mod removes that modification through DeltaMod's normal mod-management workflow.

In other words:

- **User workflow:** import one ZIP and toggle the mod.
- **Package internals:** DeltaMod automatically applies the included binary differences.
- **Manual/non-DeltaMod workflow:** a user would need to patch or replace `data.win` themselves.

The ZIP contains these files directly at its archive root, with no enclosing folder:

```text
Chapter1DataPatch.xdelta
Chapter2DataPatch.xdelta
Chapter3DataPatch.xdelta
Chapter4DataPatch.xdelta
Chapter5DataPatch.xdelta
meta.json
modding.xml
```

`ready_packages.json` records the exact clean-file, patched-file, payload, and package SHA-256 values used for the generated test packages. Those payloads were round-trip verified against those exact supplied source files, but that does not make them compatible with later game updates. The recorded Chapter 5 source SHA-256 is `7e3e9c4a0ef84f0129b6a1c9e9f81091e83abbafbf66eb09893c2082cf5618de`.

The telemetry-patched targets were produced with the official UndertaleModTool 0.9.1.2 CLI by applying `../AiTelemetry.csx`. The maintainer builder only assembles already-created payloads into the importable ZIP; it is not part of installation.

Before releasing a regenerated package, test it in a fresh DeltaMod game copy with no other Chapter 5 mods enabled. If it works alone but not alongside another mod, treat that as a merge conflict rather than a source-version failure.

Do not add `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or another enclosing folder to this package layout.
