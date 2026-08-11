# DeltaMod telemetry package

The compiled telemetry v9.1.0 ZIP was withdrawn on August 6, 2026.

Do not restore or install it beside speed v1.2.0. Their independently compiled
GameMaker payloads can corrupt shared variable indexes and cause
`gml_Object_obj_time_Step_1` to read an unrelated `bbox_top` global.

## Current candidate

The replacement is the direct-CSX telemetry v9.2.0 package:

`Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip`

The package version changed for the new installation format; the localhost wire
protocol remains `DRTEL|9|` / protocol v9, so the Python receiver does not need
a protocol migration.

It targets DeltaMod game version `1.05` and the validated Steam build 24484059
baseline whose Chapter 5 data contains `v0.0.253`. All five clean `data.win`
SHA-256 values are pinned in `neededFiles`.

Source-level validation with UndertaleModTool CLI 0.9.1.2 passed on Chapters
1-5 both alone and with speed applied in either order. In every combined result,
telemetry remains in its intended events while `obj_time` contains no telemetry
marker, `bbox_top`, or other `bbox_*` reference.

Candidate SHA-256:

`01adbcfbbcf05e19453ff698575d5336bcd5ac1afb081d822cd39caa8469e442`

This is still a **runtime-test candidate** until it has been imported into the
current DeltaMod and launched in the real game alongside speed v1.3.0.

## Before testing

1. Disable and remove the withdrawn speed/telemetry packages.
2. Make DeltaMod rebuild its protected chapter copies from clean originals.
3. Import the v9.2.0 direct-CSX candidate.
4. Test telemetry alone with the controller in observation-only mode.
5. Then enable both candidates and launch every chapter.

The source installer is `../AiTelemetry.csx`; the reproducible package builder
is `../tools/build_packages.py`.
