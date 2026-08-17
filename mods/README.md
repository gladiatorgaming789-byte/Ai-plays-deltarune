# DELTARUNE AI mod packages

A fresh `development` clone includes three current DeltaMod runtime-test
packages:

- **Normal combined AI package:**
  `mods/support/deltamod/AI-Support-All-Chapters-DeltaMod-CSX-v2.0.0.zip`
- Standalone Speed diagnostic:
  `mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-CSX-v1.4.0.zip`
- Standalone Telemetry diagnostic:
  `mods/telemetry/deltamod/Telemetry-All-Chapters-DeltaMod-CSX-v9.3.0.zip`

For normal AI use, install **AI Support 2.0.0 only**. It adds per-process
identity, telemetry ports, and save isolation for independent training. Do not enable the
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

- AI Support 2.0.0 — 27,503 bytes — SHA-256 `aa6c7e23f77207c5bcf11e8c5701e96c414af222e73add6d70975c1e763de571`
- Speed 1.4.0 — 9,009 bytes — SHA-256 `927ec13f0187225eb5c0277d3154747bb9e9ada11135b1a97528a94d1bccb3b9`
- Telemetry 9.3.0 — 22,590 bytes — SHA-256 `17d16270731dd44b347f8b42b73bab198cc08a3d0860673271953d639f319784`

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
restore/reconstruct clean protected copies. The standalone 1.4.0/9.3.0 packages
may be tested one at a time. For the combined test, remove/disable both
standalone packages and import AI Support 2.0.0 only.

These remain **runtime-test candidates** until the real DeltaMod/game checks
pass.
