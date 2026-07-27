# DeltaMod telemetry package

Finished, ready-to-import DeltaMod ZIPs should live in `dist/` once they are generated from the exact clean and telemetry-patched chapter files.

A DeltaMod package must contain these files directly at the ZIP root:

```text
meta.json
modding.xml
ChapterNDataPatch.xdelta
```

The repository includes a builder for reproducibility, but end users should not need to run it. Release artifacts should already include the generated ZIPs.

## Maintainer-only generation

1. Apply `../AiTelemetry.csx` to each exact clean chapter `data.win` and save the patched result separately.
2. Generate an xdelta/VCDIFF payload from the clean file to the telemetry-patched file.
3. Run `build_package.py` to produce the finished DeltaMod ZIP.
4. Test the ZIP in a fresh DeltaMod game copy.
5. Commit or attach the finished package as a release artifact.

Do not use `_deltamodInfo.json`, `.disable_gb1click_deltahub`, or an enclosing folder. Those are not part of the official standard.
