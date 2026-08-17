# DeltaMod speed package

## Current candidate

Use:

`AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip`

Speed 1.3.1 is a packaging-route correction for the existing direct-CSX speed
source. Its `modding.xml` declares every raw UndertaleModTool script with
DeltaMod `type="csx"`.

Speed 1.3.0 is withdrawn. It incorrectly declared raw `.csx` scripts as
`type="xdelta"`, which caused DeltaMod to pass them to G3MTool's ZIP-backed
merge engine. G3MTool then failed with `End of Central Directory record could
not be found` because the inputs were source text, not G3MPatch ZIP archives.
The older compiled Speed 1.2.0 package also remains withdrawn because of the
separate shared-variable-index merge-corruption issue.

A fresh `development` clone includes the 1.3.1 ZIP. `Start AI GUI.bat` runs the
validated package materializer before GUI startup and accepts the package only
when it reproduces the release record exactly.

Validated candidate:

- size: `23689` bytes
- SHA-256: `bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d`
- DeltaMod target: `1.05`
- Steam baseline: `24484059`
- patch type: `csx`

The five clean chapter hashes are pinned through `neededFiles`. The package is
canonical UTF-8/LF and byte-stable across supported Python platforms.

## Before testing

1. Remove Speed 1.3.0 and Telemetry 9.2.0 from DeltaMod.
2. Let DeltaMod restore/reconstruct clean protected chapter copies.
3. Pull the latest `development` branch.
4. Import Speed 1.3.1.
5. Test Speed alone across Chapters 1-5, including F8/F9/F10.
6. Do **not** enable standalone Telemetry beside standalone Speed. Current
   DeltaMod CSX handling reloads both from the same `.bak`, so the later result
   replaces the earlier one. Use AI Support 1.0.0 for Speed + Telemetry.

This remains a **runtime-test candidate** until those live DeltaMod/game checks
pass. The source installer is `../AiSpeed.csx`; the reproducible builder is
`../tools/build_packages.py`.
