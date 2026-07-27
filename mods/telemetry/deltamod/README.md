# DeltaMod telemetry package

The user-facing release artifact is:

```text
AI-Telemetry-DeltaMod.zip
```

It is already built and ready to import into DeltaMod. **Do not extract it, do not run UndertaleModTool, and do not run the package builder.** Import the ZIP itself.

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

The telemetry-patched targets were produced with the official UndertaleModTool 0.9.1.2 CLI by applying `../AiTelemetry.csx`. The builder remains only for maintainer reproducibility and is not part of installation.

Optional chapter-specific ZIPs may also be provided for testing, but normal users should import the single all-chapters `AI-Telemetry-DeltaMod.zip` file.

Do not add `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or an enclosing folder. Those are not part of the supported package layout.
