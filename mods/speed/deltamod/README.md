# DeltaMod speed package

The compiled v1.2.0 ZIPs were withdrawn on August 6, 2026.

Do not restore or install them beside telemetry v9.1.0. Their independently
compiled GameMaker payloads can corrupt shared variable indexes and cause
`gml_Object_obj_time_Step_1` to read an unrelated `bbox_top` global.

## Current candidate

The replacement is the direct-CSX speed v1.3.0 package:

`AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip`

It targets DeltaMod game version `1.05` and the validated Steam build 24484059
baseline whose Chapter 5 data contains `v0.0.253`. All five clean `data.win`
SHA-256 values are pinned in `neededFiles`.

Source-level validation with UndertaleModTool CLI 0.9.1.2 passed on Chapters
1-5 both alone and with telemetry applied in either order. In every combined
result, `obj_time` contains the speed hook and no telemetry marker, `bbox_top`,
or other `bbox_*` reference.

Candidate SHA-256:

`d9c9a3fca7ff3a610f4b4de1578a75f5b233ffaa86c281e71cd1032f2d622ced`

This is still a **runtime-test candidate** until it has been imported into the
current DeltaMod and launched in the real game alongside telemetry v9.2.0.

## Before testing

1. Disable and remove the withdrawn speed/telemetry packages.
2. Make DeltaMod rebuild its protected chapter copies from clean originals.
3. Import the v1.3.0 direct-CSX candidate.
4. Test speed alone before enabling telemetry.
5. Then enable both candidates and launch every chapter.

The source installer is `../AiSpeed.csx`; the reproducible package builder is
`../tools/build_packages.py`.
