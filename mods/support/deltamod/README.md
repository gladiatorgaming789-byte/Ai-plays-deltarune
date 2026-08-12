# DeltaMod AI Support package

## Normal AI configuration

Use this package for normal operation:

`AI-Support-All-Chapters-DeltaMod-CSX-v1.0.0.zip`

AI Support combines Speed 1.3.1 and Telemetry 9.2.1 into one generated
UndertaleModTool CSX installer per chapter. DeltaMod therefore performs both
component installations in a single UTMT invocation instead of applying two
independent CSX patches from the same `data.win.bak`.

This atomic package is necessary because current DeltaMod CSX handling loads
each separate CSX patch from the same protected `.bak`. If Speed and Telemetry
are installed as two independent CSX mods targeting the same `data.win`, the
later CSX result can replace the earlier result instead of accumulating it.

The standalone Speed 1.3.1 and Telemetry 9.2.1 packages remain useful for
isolated diagnostics, but **do not enable both standalone packages together**.
For the combined AI configuration, disable/remove both standalone packages and
use AI Support alone.

Validated candidate:

- version: `1.0.0`
- size: `81579` bytes
- SHA-256: `b017fe942d67c713b3c0ee7fe003787a024f600eed2ebb9314b33d67221ea5b5`
- generated combined CSX SHA-256: `14d82f34ef5e2c61e4abb486bdc6a22efc9056d10a23a8378e80134b64a9595e`
- Speed component: `1.3.1`
- Telemetry component: `9.2.1`
- telemetry protocol: `9`
- DeltaMod target: `1.05`
- Steam baseline: `24484059`
- patch type: `csx`

The package is generated deterministically from the committed
`mods/speed/AiSpeed.csx` and `mods/telemetry/AiTelemetry.csx` sources. The
component logic is not maintained as a separate third copy.

## Live-test procedure

1. Remove the withdrawn Speed 1.3.0 and Telemetry 9.2.0 imports from DeltaMod.
2. Disable/remove standalone Speed 1.3.1 and Telemetry 9.2.1 before combined testing.
3. Let DeltaMod restore/reconstruct its clean protected chapter copies.
4. Import AI Support 1.0.0 only.
5. Launch Chapters 1-5 and confirm they start without patching errors.
6. Confirm telemetry protocol v9 reaches the external controller.
7. Confirm F8/F9/F10 speed controls and synchronization work.
8. Disable and re-enable AI Support once and confirm DeltaMod restores/repatches cleanly.

AI Support remains a **runtime-test candidate** until these live checks pass.
