# DeltaMod telemetry package

This directory follows the official DeltaMod Modding Standard.

A finished ZIP must contain these files directly at the archive root:

```text
meta.json
modding.xml
ChapterNDataPatch.xdelta
```

Do not use `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or an enclosing
folder. Those are not part of the standard.

## Build process

1. Apply `../AiTelemetry.csx` to a clean chapter `data.win` and save the patched
   result separately.
2. Use MiscTools or another xdelta/VCDIFF creator to generate an `.xdelta` file
   from the clean file to the telemetry-patched file.
3. Build the DeltaMod ZIP:

```powershell
python mods/telemetry/deltamod/build_package.py `
  --target-version 1.05 `
  --patch 5=path\to\Chapter5DataPatch.xdelta `
  --output dist\AI-Plays-Deltarune-Telemetry-Ch5.zip
```

Repeat `--patch` for more chapters. The builder writes `meta.json`, writes one
`modding.xml` instruction per chapter, calculates every patch checksum, and keeps
all files at the ZIP root.

A Chapter 5 instruction is:

```xml
<patch type="xdelta" patch="./Chapter5DataPatch.xdelta" to="./chapter5_windows/data.win"/>
```

Use the exact Deltarune version from the clean files used to create the patches.
Test the finished ZIP in a fresh DeltaMod game copy before publishing it.
