# DeltaMod telemetry package

The current user-facing release artifact is:

```text
AI-Telemetry-DeltaMod-v9.0.1.zip
```

Import that ZIP directly into DeltaMod, then enable or disable the mod there. **Do not extract it, do not run UndertaleModTool, and do not manually patch any `data.win` file.**

## Fixed in 9.0.1

The original generated payloads used valid VCDIFF data but placed each optional Adler-32 checksum at the end of its window. Standard xdelta3/DeltaMod expects the four checksum bytes immediately after the VCDIFF window-length fields and before the data, instruction, and address sections. DeltaMod therefore misread the old payload and reported that the xdelta was being applied to the wrong source file.

Version 9.0.1 moves every checksum into the standard position without changing the reconstructed telemetry targets.

Validation performed for all five chapters:

- The supplied clean `data.win` hashes match the expected source hashes.
- Every corrected payload passes strict standard VCDIFF window-layout parsing.
- Every corrected payload decodes against its matching clean chapter file.
- Each decoded result matches the previously verified UndertaleModTool telemetry target SHA-256.
- The decoder was cross-checked by successfully decoding the known-working Telegraphed Mew-Mew Bombs DeltaMod payload.
- ZIP entries, metadata checksums, `meta.json`, and `modding.xml` were validated.

The ZIP contains these files directly at its archive root:

```text
Chapter1DataPatch.xdelta
Chapter2DataPatch.xdelta
Chapter3DataPatch.xdelta
Chapter4DataPatch.xdelta
Chapter5DataPatch.xdelta
meta.json
modding.xml
```

## DeltaMod behavior

DeltaMod handles the game-file operation automatically. End users only import and toggle the mod. The `.xdelta` files are internal package payloads used by DeltaMod; users do not run a patcher themselves.

The telemetry GML remains version 9 and was not changed for this package repair. Only the VCDIFF/xdelta container layout and package metadata version changed.

Use `validate_vcdiff_layout.py` before packaging any future payloads so non-standard checksum placement is rejected before release.
