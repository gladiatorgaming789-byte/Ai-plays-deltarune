# DELTARUNE AI mod packages

A fresh `development` clone now includes the two DeltaMod packages used by the controller:

- Speed: `mods/speed/deltamod/AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip`
- Telemetry: `mods/telemetry/deltamod/Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip`

`Start AI GUI.bat` still runs `mods/build_validated_packages.py` after dependency setup and before opening the GUI. Existing ZIPs are accepted only when their exact byte size and SHA-256 match the checked-in release records. A missing or damaged ZIP is rebuilt from the committed CSX source and must reproduce those exact bytes before startup continues.

Current validated candidates:

- Speed v1.3.0 — 23,704 bytes — SHA-256 `08ee5fcb0278c97cd2197b97df23c2be852eefd630be1d1f146bbaab1300c842`
- Telemetry v9.2.0 — 53,404 bytes — SHA-256 `c66e2f679ce8892c6aaefc6dddb47efef571e60b239714528fde962c99f9a710`

The ZIPs use `STORED` entries (no compression) with canonical metadata. This is intentional: DEFLATE streams can vary across zlib versions, and Python's ZIP writer can vary header bytes across patch versions. The uncompressed canonical form keeps the package hash stable across Windows and Linux. The `meta.json`, `modding.xml`, and CSX payload contents are the same validated direct-CSX material; only the outer ZIP storage representation changed.

The packages can also be regenerated/verified manually from the repository root:

```powershell
.\.venv\Scripts\python.exe .\mods\build_validated_packages.py
```

Only these two current validated ZIPs belong in the repository. The withdrawn speed v1.2.0 and telemetry v9.1.0 compiled packages must remain absent.

These are still **runtime-test candidates** until live DeltaMod/game validation is complete.
