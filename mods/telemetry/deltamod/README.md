# DeltaMod telemetry packages

The compiled telemetry v9.1.0 ZIP was withdrawn on August 6, 2026.

Do not restore or install it beside speed v1.2.0. Their independently compiled
GameMaker payloads can corrupt shared variable indexes and cause
`gml_Object_obj_time_Step_1` to read an unrelated `bbox_top` global.

The replacement path is the direct-CSX builder in `../tools/build_packages.py`.
It packages `../AiTelemetry.csx` as source for DeltaMod while keeping wire
protocol v9. An installable package is intentionally not committed until the
current Deltarune version and refreshed clean chapter files have been verified.

Before testing a replacement, disable both old mods and make DeltaMod rebuild
its protected chapter copies from clean originals.
