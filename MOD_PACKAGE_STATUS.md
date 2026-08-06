# Mod package status

**Current status: compiled DeltaMod packages withdrawn; direct-CSX replacements pending runtime validation.**

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
- Requires an explicit Deltarune target version instead of guessing the stale
  version previously recorded in the repository.
- Supports optional freshly measured clean `data.win` SHA-256 pins.
- Added package-layout, source-validation, real-script build, and withdrawal
  regression tests.

## Immediate recovery

1. Disable and remove both old packages from DeltaMod.
2. Make DeltaMod rebuild its protected chapter copies from clean originals.
3. Do not apply either old package to a `data.win` already changed by the other.
4. Do not reuse an older modded backup after the Deltarune update.

The crash occurs in patched runtime code and does not by itself show that save
files were damaged. Clean chapter copies are still required before retesting.

## Replacement validation gate

No replacement ZIP is committed yet. Before release:

1. Record the exact current Deltarune/DeltaMod target version.
2. Hash refreshed clean Chapter 1-5 `data.win` files.
3. Build direct-CSX speed v1.3.0 and telemetry package v9.2.0.
4. Test telemetry only, speed only, and both enabled together in a clean
   DeltaMod setup.
5. Launch every chapter and confirm no `obj_time`/`bbox_top` failure.
6. Verify telemetry packets, speed controls, disable/re-enable behavior, and
   restoration to clean protected copies.

Until those checks pass, compatibility with the updated game is **not claimed**.
