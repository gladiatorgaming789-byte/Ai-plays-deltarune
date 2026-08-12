# DELTARUNE AI mod packages

A fresh `development` clone includes three current DeltaMod runtime-test
packages:

- **Normal combined AI package:**
  `mods/support/deltamod/AI-Support-All-Chapters-DeltaMod-CSX-v1.0.0.zip`
- Standalone Speed diagnostic:
  `mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip`
- Standalone Telemetry diagnostic:
  `mods/telemetry/deltamod/Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip`

For normal AI use, install **AI Support 1.0.0 only**. Do not enable the
standalone Speed and Telemetry packages together. Current DeltaMod executes each
separate `type="csx"` patch from the same `data.win.bak`, so later independent
CSX patches can replace earlier results. AI Support generates one atomic CSX
installer from the exact Speed and Telemetry sources and runs both components
in a single UndertaleModTool invocation per chapter.

`Start AI GUI.bat` updates the checkout before package validation, then runs
`mods/build_validated_packages.py`. Existing ZIPs are accepted only when their
exact byte size and SHA-256 match checked-in release records. Missing or damaged
current ZIPs are rebuilt from committed source and must reproduce the expected
bytes before startup continues.

Current candidates:

- AI Support 1.0.0 — 81,579 bytes — SHA-256 `b017fe942d67c713b3c0ee7fe003787a024f600eed2ebb9314b33d67221ea5b5`
- Speed 1.3.1 — 23,689 bytes — SHA-256 `bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d`
- Telemetry 9.2.1 — 53,389 bytes — SHA-256 `609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1`

All three packages declare raw UndertaleModTool scripts with DeltaMod's
dedicated `type="csx"` patch type. Speed 1.3.0 and Telemetry 9.2.0 incorrectly
used `type="xdelta"` for raw `.csx` source, causing G3MTool to fail with `End of
Central Directory record could not be found`; those releases are withdrawn.
The older compiled Speed 1.2.0 / Telemetry 9.1.0 releases also remain withdrawn
because of the separate shared-variable-index merge-corruption problem.

The current ZIPs use deterministic STORED entries with canonical metadata,
UTF-8/LF source, fixed timestamps and permissions, and stable entry ordering.
All package builders are tested under Python `-S`, without gameplay/UI
site-packages.

The packages can be regenerated/verified manually from the repository root:

```powershell
.\.venv\Scripts\python.exe .\mods\build_validated_packages.py
```

Before live testing, remove the withdrawn 1.3.0/9.2.0 imports and let DeltaMod
restore/reconstruct clean protected copies. The standalone 1.3.1/9.2.1 packages
may be tested one at a time. For the combined test, remove/disable both
standalone packages and import AI Support 1.0.0 only.

These remain **runtime-test candidates** until the real DeltaMod/game checks
pass.
