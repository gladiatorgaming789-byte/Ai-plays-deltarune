# DELTARUNE AI mod packages

A fresh `development` clone includes the two current DeltaMod runtime-test
candidates used by the controller:

- Speed: `mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip`
- Telemetry: `mods/telemetry/deltamod/Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip`

`Start AI GUI.bat` updates the checkout before package validation, then runs
`mods/build_validated_packages.py`. Existing ZIPs are accepted only when their
exact byte size and SHA-256 match the checked-in release records. A missing or
damaged current ZIP is rebuilt from committed CSX source and must reproduce the
expected bytes before startup continues.

Current candidates:

- Speed 1.3.1 — 23,689 bytes — SHA-256 `bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d`
- Telemetry 9.2.1 — 53,389 bytes — SHA-256 `609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1`

Both packages declare raw UndertaleModTool scripts with DeltaMod's dedicated
`type="csx"` patch type. This matters: Speed 1.3.0 and Telemetry 9.2.0 used
`type="xdelta"` for raw `.csx` source, causing DeltaMod to route those files to
G3MTool's ZIP-backed merge path and fail with `End of Central Directory record
could not be found`. Those versions are withdrawn and their ZIPs must remain
absent. The older compiled Speed 1.2.0 / Telemetry 9.1.0 packages are also
withdrawn due to the separate variable-index merge-corruption problem.

The current ZIPs use deterministic STORED entries with canonical metadata,
UTF-8/LF source, fixed timestamps and permissions, and stable entry ordering.
The package builders are also tested under Python `-S` so they do not rely on
Pillow, PySide6, or other gameplay/UI dependencies.

The packages can be regenerated/verified manually from the repository root:

```powershell
.\.venv\Scripts\python.exe .\mods\build_validated_packages.py
```

Before live testing, remove the withdrawn 1.3.0/9.2.0 imports from DeltaMod,
allow DeltaMod to reconstruct its protected clean copies, and import the new
1.3.1/9.2.1 ZIPs. These remain **runtime-test candidates** until telemetry-only,
speed-only, combined, and disable/re-enable checks pass in the real game.
