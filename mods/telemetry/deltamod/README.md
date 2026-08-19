# DeltaMod telemetry package

## Current candidate

Use:

`Telemetry-All-Chapters-DeltaMod-CSX-v9.3.1.zip`

Telemetry 9.3.1 retains the `DRTEL|9|` wire protocol, per-process IDs, dedicated
UDP ports, titled windows, and isolated save paths for independent training. It
also fixes the training startup checkpoint: `scr_save()` is now reachable only
when a valid `ai_instance_*` identity is present, so an ordinary single-instance
DELTARUNE session does not trigger the training autosave.

Telemetry 9.3.0 is **withdrawn** because its background autosave did not require
a training instance ID and could therefore invoke the normal save path. The
unsafe 9.3.0 ZIP was removed from `development`.

Every raw UndertaleModTool script is declared with DeltaMod `type="csx"`. The
five clean chapter SHA-256 values remain pinned through `neededFiles` against the
validated DELTARUNE 1.05 / Steam build 24484059 baseline.

Because GitHub Actions runner provisioning is currently blocked for this
repository account, the new binary ZIP is materialized locally from the committed
safe source. `Start AI GUI.bat` runs `mods/build_validated_packages.py` before
GUI startup; it builds 9.3.1 when missing and verifies package version, five CSX
declarations, clean chapter hashes, and the `AI_BACKGROUND_AUTOSAVE_V2` safety
marker before accepting it.

## Before testing

1. Remove/disable Telemetry 9.3.0 and AI Support 2.0.0 in DeltaMod.
2. Let DeltaMod restore/reconstruct clean protected chapter copies.
3. Pull the latest `development` branch and run `Start AI GUI.bat` once so the
   safe current packages are materialized.
4. For telemetry-only diagnostics, import Telemetry 9.3.1.
5. For normal combined operation and Population Training, use AI Support 2.0.1
   instead of enabling standalone Speed and Telemetry together.
6. Validate startup, telemetry identity/sequence health, saves, and disable /
   re-enable restoration across Chapters 1-5.

This remains a **runtime-test candidate** until live DeltaMod/game checks pass.
The source installer is `../AiTelemetry.csx`; the builder is
`../tools/build_packages.py`.
