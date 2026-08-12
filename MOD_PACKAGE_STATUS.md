# Mod package status

## Current state

The current runtime-test candidates are **Speed 1.3.1** and **Telemetry 9.2.1**.
Both package raw UndertaleModTool source scripts and declare those scripts with
DeltaMod's dedicated `type="csx"` patch type.

Four older package generations are withdrawn:

- Speed 1.2.0 / Telemetry 9.1.0: compiled-package merge corruption could leak
  telemetry variable references such as `bbox_top` into `obj_time`.
- Speed 1.3.0 / Telemetry 9.2.0: raw `.csx` scripts were incorrectly declared
  as `type="xdelta"`. DeltaMod routed them through G3MTool's ZIP-backed patch
  merger, which failed on Chapters 1-5 with `End of Central Directory record
  could not be found`.

The validated project baseline is DeltaMod target `1.05`, Steam build
`24484059`, with Chapter 5 marker `v0.0.253`.

## Clean chapter SHA-256

1. `82c2bb61b8d78cd287120f6301588fecba34ec5a890bac711b7a8774c760ec70`
2. `047c5ab003e3e017a709c02757e119c81e0327760169512110fd276b19241e68`
3. `c1a0925343694ec9b9adcbf2f916a720b02fd1b999286cfe8fe6a52f3320f714`
4. `ed64789586238b52375e994e1c1cf13694dd2d0dab57d13e639b9c892e37d8f2`
5. `370dfd141d2955d5a1960122919b16e4092b52ffbb85fda541bc4680c6b3b85c`

Corrected source archive SHA-256:
`a182babb54ed918700561d5ab60503dd272847a88c07027f09311b5101e0a7bc`

## Source-level validation

UndertaleModTool CLI 0.9.1.2 previously passed all 20 semantic cases:
telemetry only, speed only, telemetry then speed, and speed then telemetry for
Chapters 1-5. Combined results kept the speed hook in
`gml_Object_obj_time_Step_1`, telemetry in its intended events, and no telemetry
`bbox_*` reference in `obj_time`.

The 1.3.1 / 9.2.1 packaging correction does not alter those functional CSX
sources or telemetry protocol. It changes DeltaMod dispatch metadata so the raw
scripts are executed by the CSX/UTMT path instead of being handed to G3MTool's
ZIP patch merger.

## Deterministic runtime-test candidates

- `AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip`
  - size: 23,689 bytes
  - SHA-256: `bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d`
- `Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip`
  - size: 53,389 bytes
  - SHA-256: `609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1`

The release materializer canonicalizes CSX source to UTF-8/LF and writes a
byte-stable STORED ZIP with fixed timestamps, permissions, metadata ordering,
and entry ordering. Tests also execute the builders with Python `-S` so package
creation cannot silently depend on gameplay/UI site-packages.

Each package contains root-level `meta.json`, `modding.xml`, and five
chapter-specific CSX source entries. `neededFiles` pins the clean chapter hashes
above. Every `modding.xml` entry must use `type="csx"`; validation explicitly
rejects `type="xdelta"` for these raw scripts.

## Remaining runtime release gate

1. Remove the withdrawn 1.3.0 / 9.2.0 imports from DeltaMod.
2. Let DeltaMod restore/reconstruct from its clean protected copies.
3. Import Speed 1.3.1 and Telemetry 9.2.1.
4. Test telemetry by itself across Chapters 1-5.
5. Test speed by itself across Chapters 1-5, including F8/F9/F10.
6. Enable both and launch Chapters 1-5.
7. Confirm telemetry reaches localhost UDP 42069 and speed synchronization works.
8. Disable and re-enable once and confirm clean protected-copy restoration.
9. Require the current Recovery CI matrix to pass.

Until the live DeltaMod checks pass, 1.3.1 and 9.2.1 remain runtime-test
candidates rather than final runtime-verified releases.
