# DELTARUNE AI mod packages

This folder contains the source and reproducible build records for the two DeltaMod packages used by the controller:

- Speed: `mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip`
- Telemetry: `mods/telemetry/deltamod/Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip`

The ZIP files are generated locally rather than stored as generated binaries in Git. On a fresh clone, double-click `Start AI GUI.bat`; after dependency setup it runs `mods/build_validated_packages.py` before opening the GUI.

The materializer builds each ZIP from the committed CSX source and accepts it only when both its byte size and SHA-256 exactly match the checked-in release record. A mismatching package is deleted and startup stops instead of exposing an unverified mod.

Expected validated candidates:

- Speed v1.3.0 — 7,894 bytes — SHA-256 `ae2ad5ae5a3c30cf9c7e48d51b052cd10febb419514672760840ed7f99fb5283`
- Telemetry v9.2.0 — 15,293 bytes — SHA-256 `8464461d0e291f6a67b827be2cb4f06f2218a1ef8976ada9905b58c8b3e46255`

You can also generate/verify both packages manually from the repository root:

```powershell
.\.venv\Scripts\python.exe .\mods\build_validated_packages.py
```

These remain runtime-test candidates until live DeltaMod/game validation is complete.
