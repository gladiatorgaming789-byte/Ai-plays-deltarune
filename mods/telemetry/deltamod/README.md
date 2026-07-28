# DeltaMod telemetry package

The current user-facing release artifact is:

```text
Telemetry-DeltaMod-v9.0.2.zip
```

Import that ZIP directly into DeltaMod, then enable or disable the mod there. **Do not extract it, do not run UndertaleModTool, and do not manually patch any `data.win` file.**

This remains a separate telemetry mod. It can be enabled beside
`mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-v1.1.0.zip` after updating
DeltaMod's merge tool to G3MTool 1.2.5 or newer. G3MTool 1.2.1 corrupts
GameMaker variable references when it combines two code patches; use the
backup-first updater supplied with the speed mod. There is no combined package.

## Fixed in 9.0.2

Version 9.0.2 is a metadata-only repair. It gives telemetry its own valid
three-part package ID, points `neededFiles` at the installed Chapter 1–5
`data.win` files with their verified clean SHA-256 hashes, and explicitly
enables merge support. The telemetry GML and the verified v9 VCDIFF payloads
are unchanged from 9.0.1.

## VCDIFF repair retained from 9.0.1

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

The telemetry GML remains version 9 and was not changed for this package repair.

Use `validate_vcdiff_layout.py` before packaging any future payloads so non-standard checksum placement is rejected before release.
