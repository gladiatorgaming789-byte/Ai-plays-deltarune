# DeltaMod telemetry package

## Current candidate

Use:

`Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip`

Telemetry 9.2.1 corrects the DeltaMod dispatch metadata while retaining the
existing `DRTEL|9|` localhost wire protocol and functional CSX source.
Every raw UndertaleModTool script is declared with DeltaMod `type="csx"`.

Telemetry 9.2.0 is withdrawn. It incorrectly declared raw `.csx` scripts as
`type="xdelta"`, which caused DeltaMod to pass them to G3MTool's ZIP-backed
merge engine. G3MTool then failed with `End of Central Directory record could
not be found` because the inputs were source text rather than G3MPatch ZIP
archives. The older compiled Telemetry 9.1.0 package also remains withdrawn due
to the separate shared-variable-index merge-corruption issue.

A fresh `development` clone includes the 9.2.1 ZIP. `Start AI GUI.bat` runs the
validated package materializer before GUI startup and accepts the package only
when it reproduces the release record exactly.

Validated candidate:

- size: `53389` bytes
- SHA-256: `609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1`
- telemetry protocol: `9`
- DeltaMod target: `1.05`
- Steam baseline: `24484059`
- patch type: `csx`

The five clean chapter hashes are pinned through `neededFiles`. The package is
canonical UTF-8/LF and byte-stable across supported Python platforms.

## Before testing

1. Remove Speed 1.3.0 and Telemetry 9.2.0 from DeltaMod.
2. Let DeltaMod restore/reconstruct clean protected chapter copies.
3. Pull the latest `development` branch.
4. Import Telemetry 9.2.1.
5. Test Telemetry alone across Chapters 1-5 with the controller observing.
6. Do **not** enable standalone Speed beside standalone Telemetry. Current
   DeltaMod CSX handling reloads both from the same `.bak`, so the later result
   replaces the earlier one. Use AI Support 1.0.0 for Speed + Telemetry.

This remains a **runtime-test candidate** until those live DeltaMod/game checks
pass. The source installer is `../AiTelemetry.csx`; the reproducible builder is
`../tools/build_packages.py`.
