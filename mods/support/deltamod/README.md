# DeltaMod AI Support package

## Normal AI configuration

Use this package for normal operation and Population Training:

`AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip`

AI Support 2.0.1 combines Speed 1.4.0 and Telemetry 9.3.1 into one generated
UndertaleModTool CSX installer per chapter. Telemetry 9.3.1 fixes the training
startup checkpoint so it runs only for a process carrying a valid
`ai_instance_*` identity; ordinary single-instance play no longer reaches the
training autosave path.

AI Support 2.0.0 is **withdrawn** because it contained Telemetry 9.3.0's unsafe
unconditional room_krisroom startup autosave. The 2.0.0 ZIP was removed from the
`development` branch.

The atomic package remains necessary because current DeltaMod CSX handling can
load separate Speed and Telemetry patches from the same protected `data.win.bak`.
For combined operation, disable/remove both standalone packages and enable AI
Support alone.

Current components:

- AI Support: `2.0.1`
- Speed: `1.4.0`
- Telemetry: `9.3.1`
- telemetry protocol: `9`
- DeltaMod target: `1.05`
- Steam baseline: `24484059`
- patch type: `csx`

The package is generated from the committed `mods/speed/AiSpeed.csx` and
`mods/telemetry/AiTelemetry.csx` sources. While GitHub Actions runner
provisioning is blocked, `Start AI GUI.bat` materializes the 2.0.1 ZIP locally
before GUI startup and verifies its declared version, all five clean chapter
hashes, CSX routing, speed/telemetry/multi-instance markers, and
`AI_BACKGROUND_AUTOSAVE_V2` safety marker.

## Live-test procedure

1. Remove/disable AI Support 2.0.0 and standalone Telemetry 9.3.0 in DeltaMod.
2. Let DeltaMod restore/reconstruct its clean protected chapter copies.
3. Pull the latest `development` branch and run `Start AI GUI.bat` once to
   materialize AI Support 2.0.1.
4. Import/enable AI Support 2.0.1 only.
5. Launch Chapters 1-5 and confirm they start without patching errors.
6. Confirm telemetry protocol v9 identity/sequence health and F8/F9/F10 speed
   synchronization.
7. Confirm a normal single-instance run does not create/use an `ai_training`
   startup-save path.
8. Start a two-AI population run and confirm separate titles, saves, telemetry
   ports, controller inputs, and clean safe-stop behavior.
9. Disable and re-enable AI Support once and confirm DeltaMod restores/repatches cleanly.

AI Support remains a **runtime-test candidate** until these live checks pass.
