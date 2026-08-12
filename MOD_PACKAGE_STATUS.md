# Mod package status

## Current state

The normal combined AI runtime-test candidate is **AI Support 1.0.0**. It
atomically composes Speed 1.3.1 and Telemetry 9.2.1 into one UndertaleModTool
CSX installer per chapter.

Speed 1.3.1 and Telemetry 9.2.1 remain available as **standalone diagnostic
packages only**. Do not enable both standalone packages together. Current
DeltaMod applies each separate `type="csx"` patch from the same `data.win.bak`,
so a later CSX patch can replace the result of an earlier CSX patch instead of
accumulating both changes.

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

## Why AI Support is atomic

Current DeltaMod has three relevant patching stages:

1. override/copy patches;
2. `xdelta` and `g3mpatch` patches merged by G3MTool; and
3. `csx` patches executed by UndertaleModCli.

For a CSX target, DeltaMod creates `data.win.bak` once and then invokes UTMT as
`load <backup> --output <target> --scripts <patch>` for each CSX patch. Because
each separate CSX invocation starts from the same backup, two independent CSX
mods targeting the same `data.win` are not a reliable composition mechanism.

AI Support solves this without duplicating component logic. Its builder reads
the exact committed Speed and Telemetry CSX sources, canonicalizes them,
component-scopes their installer bodies, and generates one combined source that
runs Speed and Telemetry in a single UTMT invocation. Telemetry remains protocol
v9.

## Source-level validation

UndertaleModTool CLI 0.9.1.2 previously passed all 20 semantic cases using the
underlying Speed and Telemetry sources: telemetry only, speed only, telemetry
then speed, and speed then telemetry for Chapters 1-5. Combined results kept
the speed hook in `gml_Object_obj_time_Step_1`, telemetry in its intended
events, and no telemetry `bbox_*` reference in `obj_time`.

The current packaging fixes do not alter those functional component sources.
They correct DeltaMod dispatch and composition behavior.

## Deterministic runtime-test candidates

### Normal combined package

- `AI-Support-All-Chapters-DeltaMod-CSX-v1.0.0.zip`
  - size: 81,579 bytes
  - SHA-256: `b017fe942d67c713b3c0ee7fe003787a024f600eed2ebb9314b33d67221ea5b5`
  - generated combined CSX SHA-256: `14d82f34ef5e2c61e4abb486bdc6a22efc9056d10a23a8378e80134b64a9595e`
  - Speed component: 1.3.1
  - Telemetry component: 9.2.1 / protocol 9

### Standalone diagnostics

- `AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip`
  - size: 23,689 bytes
  - SHA-256: `bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d`
- `Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip`
  - size: 53,389 bytes
  - SHA-256: `609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1`

The release materializer canonicalizes CSX source to UTF-8/LF and writes
byte-stable STORED ZIPs with fixed timestamps, permissions, metadata ordering,
and entry ordering. Tests execute all three builders with Python `-S` so
package creation cannot silently depend on gameplay/UI site-packages.

Every `modding.xml` entry uses `type="csx"`; validation explicitly rejects
`type="xdelta"` for these raw scripts.

## Remaining runtime release gate

1. Remove the withdrawn Speed 1.3.0 and Telemetry 9.2.0 imports from DeltaMod.
2. Let DeltaMod restore/reconstruct from its clean protected copies.
3. Optional standalone diagnostic: import Speed 1.3.1 alone, test Chapters 1-5
   and F8/F9/F10, then remove/disable it and restore clean.
4. Optional standalone diagnostic: import Telemetry 9.2.1 alone, test Chapters
   1-5 and protocol v9, then remove/disable it and restore clean.
5. For normal combined use, disable/remove both standalone packages and import
   **AI Support 1.0.0 only**.
6. Launch Chapters 1-5 and confirm both telemetry v9 and speed controls work.
7. Disable and re-enable AI Support once and confirm clean protected-copy
   restoration/repatching.
8. Require the current Recovery CI matrix to pass.

Until the live DeltaMod checks pass, AI Support 1.0.0 remains a runtime-test
candidate rather than a final runtime-verified release.
