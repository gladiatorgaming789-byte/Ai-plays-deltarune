# Mod package status

**Current status: compiled packages remain withdrawn; direct-CSX replacements passed source-level validation on the corrected v0.0.253 files and are ready for DeltaMod runtime testing.**

## Reported failure

Enabling speed v1.2.0 and telemetry v9.1.0 together caused every chapter to
abort in `gml_Object_obj_time_Step_1` with an unset global named `bbox_top`.
Neither source mod intentionally performs that read in `obj_time`:

- `AiSpeed.csx` modifies only `gml_Object_obj_time_Step_1` and uses
  `global.__ai_speed_*` names.
- `AiTelemetry.csx` reads `self.bbox_top` only in player/battle draw telemetry
  and does not modify `obj_time`.

This identifies a corrupted GameMaker variable-table reference created while
combining independently compiled code patches, not a missing initialization in
the original source.

## Repository response

- Removed all committed speed v1.2.0 and telemetry v9.1.0 ZIPs.
- Marked both old release manifests as withdrawn.
- Added `deltarune_agent/deltamod_csx_package.py`.
- Migrated both package builders to direct `.csx` source payloads.
- Speed package version is now v1.3.0; telemetry package version is v9.2.0
  while the wire protocol remains v9.
- Requires an explicit Deltarune target version instead of guessing it.
- Supports freshly measured clean `data.win` SHA-256 pins.
- Added package-layout, source-validation, real-script build, and withdrawal
  regression tests.

## Corrected game baseline

The corrected `DeltaruneMin.zip` supplied on 2026-08-11 was validated as the
intended current project baseline:

- archive SHA-256:
  `a182babb54ed918700561d5ab60503dd272847a88c07027f09311b5101e0a7bc`
- DeltaMod target version: `1.05`
- Steam build ID recorded by the project: `24484059`
- Chapter 5 embedded version marker: `v0.0.253`

Clean `data.win` SHA-256 values measured from the actual bytes:

1. Chapter 1: `82c2bb61b8d78cd287120f6301588fecba34ec5a890bac711b7a8774c760ec70`
2. Chapter 2: `047c5ab003e3e017a709c02757e119c81e0327760169512110fd276b19241e68`
3. Chapter 3: `c1a0925343694ec9b9adcbf2f916a720b02fd1b999286cfe8fe6a52f3320f714`
4. Chapter 4: `ed64789586238b52375e994e1c1cf13694dd2d0dab57d13e639b9c892e37d8f2`
5. Chapter 5: `370dfd141d2955d5a1960122919b16e4092b52ffbb85fda541bc4680c6b3b85c`

The Chapter 2-5 `data.win.hash` sidecars in the supplied archive are stale; the
values above were calculated from the real `data.win` bytes and are the values
used by the release candidates.

## Source-level compatibility proof

UndertaleModTool CLI 0.9.1.2 was used to compile and semantically inspect all
five corrected chapter files. All 20 primary cases passed:

- telemetry only, Chapters 1-5;
- speed only, Chapters 1-5;
- telemetry then speed, Chapters 1-5; and
- speed then telemetry, Chapters 1-5.

After the speed installer message was aligned with v1.3.0, both combined orders
were rerun across all five chapters and all ten cases passed again with the
exact committed source.

For every combined result:

- `gml_Object_obj_time_Step_1` contains the speed hook;
- `gml_Object_obj_time_Step_1` contains no `DRTEL|9|` telemetry marker;
- `gml_Object_obj_time_Step_1` contains no `bbox_top` or other `bbox_*` read;
- the telemetry autosave remains in `obj_mainchara` Step; and
- the intended telemetry draw events contain telemetry/collision data and no
  speed marker.

The original `obj_time`/`bbox_top` corruption is therefore not reproducible
when both mods are applied from source CSX on this baseline.

## Runtime-test candidates

Two direct-CSX DeltaMod ZIPs have been generated and structurally validated:

- `AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip`
  - size: 7,894 bytes
  - SHA-256:
    `d9c9a3fca7ff3a610f4b4de1578a75f5b233ffaa86c281e71cd1032f2d622ced`
- `Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip`
  - size: 15,293 bytes
  - SHA-256:
    `01adbcfbbcf05e19453ff698575d5336bcd5ac1afb081d822cd39caa8469e442`

Each ZIP contains only root-level `meta.json`, `modding.xml`, and one identical
CSX source member per targeted chapter. `neededFiles` pins all five clean
chapter hashes. Under DeltaMod Standard Revision 3, `.csx` patch files are
dispatched through the `xdelta` patch type in `modding.xml`; that is intentional
and does not mean the payload is a binary xdelta stream.

## Immediate recovery from the withdrawn packages

1. Disable and remove both old compiled packages from DeltaMod.
2. Make DeltaMod rebuild its protected chapter copies from clean originals.
3. Do not apply either old package to a `data.win` already changed by the other.
4. Do not reuse an older modded backup.

The crash occurs in patched runtime code and does not by itself show that save
files were damaged. Clean protected chapter copies are still required before
retesting.

## Remaining release gate

The source-level gate is complete. The only compatibility claim still withheld
is real DeltaMod/live-game runtime behavior. Before marking v1.3.0/v9.2.0 final:

1. Import the two direct-CSX ZIP candidates into a clean DeltaMod setup.
2. Test telemetry by itself and speed by itself.
3. Enable both together and launch Chapters 1-5.
4. Confirm no `obj_time`/`bbox_top` failure in any chapter.
5. Verify telemetry packets arrive on localhost UDP 42069.
6. Verify F8/F9/F10 speed controls and 1x-10x synchronization.
7. Disable and re-enable the mods and confirm DeltaMod restores/rebuilds clean
   protected copies correctly.

Until those runtime checks pass, the candidates are **source-validated test
builds**, not final runtime-verified releases.
