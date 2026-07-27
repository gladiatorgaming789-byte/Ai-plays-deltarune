# DeltaMod telemetry package

The user-facing release artifact is:

```text
AI-Telemetry-DeltaMod.zip
```

It is already built and ready to import into DeltaMod. **Do not extract it, do not run UndertaleModTool, and do not manually patch any `data.win` file.** Import the ZIP itself, then enable or disable the mod through DeltaMod.

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

The package targets the exact supplied Deltarune 1.05 clean files. `ready_packages.json` records the clean-file, patched-file, payload, and package SHA-256 values. Every payload was decoded against its exact clean chapter file, and the reconstructed file matched the UndertaleModTool-patched target byte-for-byte.

The telemetry-patched targets were produced with the official UndertaleModTool 0.9.1.2 CLI by applying `../AiTelemetry.csx`. The maintainer builder only assembles already-created payloads into the importable ZIP; it is not part of installation.

Optional chapter-specific ZIPs may also be provided for testing, but normal users should import the single all-chapters `AI-Telemetry-DeltaMod.zip` file.

Do not add `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or another enclosing folder to this package layout.
