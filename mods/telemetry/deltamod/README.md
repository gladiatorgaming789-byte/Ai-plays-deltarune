# DeltaMod telemetry packages

Ready-to-import DeltaMod packages have been generated for Deltarune Chapters 1–5 from the exact clean `data.win` files supplied for Deltarune version 1.05.

Available release artifacts:

```text
AI-Telemetry-Chapter-1-DeltaMod.zip
AI-Telemetry-Chapter-2-DeltaMod.zip
AI-Telemetry-Chapter-3-DeltaMod.zip
AI-Telemetry-Chapter-4-DeltaMod.zip
AI-Telemetry-Chapter-5-DeltaMod.zip
AI-Telemetry-All-Chapters-DeltaMod.zip
```

Each package contains these files directly at its ZIP root:

```text
meta.json
modding.xml
ChapterNDataPatch.xdelta
```

The all-chapters package contains all five chapter payloads plus one `meta.json` and one `modding.xml`.

`ready_packages.json` records the clean-file, patched-file, payload, and package SHA-256 values. Every payload was decoded against its exact clean chapter file, and the reconstructed file matched the UndertaleModTool-patched target byte-for-byte.

The telemetry-patched targets were produced with the official UndertaleModTool 0.9.1.2 CLI by applying `../AiTelemetry.csx`. The package builder remains available for maintainers and reproducibility; end users should install the finished ZIPs rather than run it themselves.

These patches target the exact supplied Deltarune 1.05 files. A changed Steam build or different game-file revision may require regenerated patches.

Do not add `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or an enclosing folder. Those are not part of the supported package layout.
