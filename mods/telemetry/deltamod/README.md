# DeltaMod packaging for AI telemetry

DeltaMod 2.x installs mods into its own copied Deltarune installation and applies
package instructions from `modding.xml`. That model is compatible with the
telemetry patch in principle because `AiTelemetry.csx` appends code to the
selected chapter's `data.win` rather than replacing the complete game archive.

This directory intentionally does **not** include a guessed `modding.xml`.
DeltaMod's official MiscTools generator is the authoritative way to create that
instruction file, and an incorrect hand-written patch could corrupt a copied
archive or silently install incomplete telemetry.

## Safe package workflow

1. Begin with the exact clean chapter build that will be declared by the package.
2. Apply `../AiTelemetry.csx` to a temporary copy with the supported
   UndertaleModTool version.
3. Validate telemetry with:

   ```powershell
   python -m deltarune_agent telemetry --seconds 30
   ```

4. Use DeltaMod MiscTools to generate `modding.xml` from the clean and validated
   patched archives. Prefer an additive G3M patch; do not package a complete
   replacement `data.win` unless DeltaMod's generated instruction explicitly
   requires it.
5. Generate `_deltamodInfo.json` with the exact Deltarune target version and the
   SHA-256 checksum of every required clean source file. The template in this
   directory is not installable until all placeholders are replaced.
6. Keep `.disable_gb1click_deltahub` in test packages until the package has been
   validated on a fresh DeltaMod copy.
7. Test installation, removal, reinstall, and coexistence with at least one
   unrelated additive mod before publishing.

## Compatibility rules

- Never layer telemetry v9 over telemetry v1-v8. The installer already refuses
  this condition.
- Never declare one chapter's checksum for another chapter.
- Do not use checksums from a telemetry-patched archive in `neededFiles`; those
  checks are meant to validate the clean input that DeltaMod will patch.
- Preserve the telemetry privacy boundary: do not add nearby interactable
  identities, positions, room-warp coordinates, hidden choice text, selection
  indexes, or option counts.
- DeltaMod compatibility does not change the Python controller protocol. The
  patched game still sends telemetry only to UDP `127.0.0.1:42069`.

## Current status

The repository now contains a standards-aligned package scaffold and validation
procedure. Full one-click compatibility remains unconfirmed until an official
MiscTools-generated `modding.xml` is produced and tested against the exact
current Deltarune archives. Do not rename the scaffold ZIP as a finished
DeltaMod package before that validation passes.
