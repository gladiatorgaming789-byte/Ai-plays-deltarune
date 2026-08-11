# Mod package status

## Current state

The older compiled speed v1.2.0 and telemetry v9.1.0 packages are retired and
must not be used together. The replacement path uses direct CSX source so both
installers are compiled against the same protected `data.win` state inside
DeltaMod.

The corrected project baseline is DeltaMod target `1.05`, project Steam build
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

UndertaleModTool CLI 0.9.1.2 passed all 20 primary semantic cases:

- telemetry only on Chapters 1-5;
- speed only on Chapters 1-5;
- telemetry then speed on Chapters 1-5; and
- speed then telemetry on Chapters 1-5.

Every combined result kept the speed hook in
`gml_Object_obj_time_Step_1`, kept telemetry in its intended events, and placed
no `bbox_top` or other `bbox_*` telemetry reference in `obj_time`.

Both combined orders were rerun after the speed installer message was changed
to v1.3.0, and all ten combined cases passed again with the exact committed
source.

## Deterministic runtime-test candidates

- `AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip`
  - size: 7,894 bytes
  - SHA-256: `ae2ad5ae5a3c30cf9c7e48d51b052cd10febb419514672760840ed7f99fb5283`
- `Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip`
  - size: 15,293 bytes
  - SHA-256: `8464461d0e291f6a67b827be2cb4f06f2218a1ef8976ada9905b58c8b3e46255`

The builder canonicalizes CSX source to UTF-8 with LF line endings and uses a
fixed ZIP timestamp, permissions, compression level, and entry order. Tests
require LF and CRLF checkouts to generate byte-identical packages.

Each candidate contains only root-level `meta.json`, `modding.xml`, and five
chapter-specific CSX source entries. `neededFiles` pins the clean chapter hashes
above. DeltaMod Standard Revision 3 intentionally dispatches `.csx` files using
`type="xdelta"` in `modding.xml`.

## Remaining runtime release gate

1. Start from clean DeltaMod protected chapter copies.
2. Test telemetry by itself.
3. Test speed by itself, including F8/F9/F10.
4. Enable both and launch Chapters 1-5.
5. Confirm telemetry reaches localhost UDP 42069.
6. Confirm speed synchronization remains correct from 1x through 10x.
7. Disable and re-enable the mods and confirm clean protected-copy restoration.
8. Require the current Recovery CI matrix to pass.

Until the live DeltaMod checks pass, v1.3.0 and v9.2.0 remain runtime-test
candidates rather than final runtime-verified releases.
