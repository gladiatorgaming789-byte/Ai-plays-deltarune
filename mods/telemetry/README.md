# Deltarune telemetry mod

This optional UndertaleModTool script adds small GML telemetry hooks:

- `obj_mainchara` sends `overworld`, room ID/name, and position.
- `obj_heart` sends `battle`, room ID/name, and soul position.
- `obj_writer` sends `dialogue` while text is active.
- Deltarune's two choice objects send `choice` while a choice menu is active.
- `obj_savemenu` sends `choice`, so natural save menus are never mistaken for
  movement or a collision.

Telemetry v6 sends three independent packets in order: core room/position/state,
motion and animation, then rich collision/interaction context. If a field in
the final layer is unavailable in a particular game build, the controller still
receives sprite, facing direction, and velocity from the motion layer. The rich
layer adds previous position, collision bounding box, instance ID, depth,
sprite scale, game timing, and the closest `obj_interactable` identity,
position, and distance. While dialogue or a menu is active, the receiver keeps
the latest player position alongside the state object's own position.

Deltarune does not keep Kris's overworld facing in GameMaker's built-in
`direction` variable. The controller derives facing from the verified Chapter 1
sprite families `spr_krisd`, `spr_krisl`, `spr_krisr`, and `spr_krisu`, including
their underscore-suffixed variants. The raw GameMaker value remains logged as
`direction`, while the useful value is logged as `facing_direction`.

For repeatable early-game testing, the patch calls Deltarune's native
`scr_save()` once per game session when `room_krisroom` begins. It creates no
visible or collidable object, so it cannot alter navigation or become an
artificial interaction target. This intentionally writes the current save slot.

Packets are sent about ten times per second by UDP to `127.0.0.1:42069` only.
The Python controller accepts no remote traffic.
The installer uses the `CodeImportGroup` API available in UndertaleModTool
0.8.4.1 and newer.

Apply v6 to a clean `data.win` or restored unmodded backup. Restore the clean
file first if any earlier telemetry version is installed, so obsolete appended
code is removed rather than layered underneath v6.

## Safe installation, one chapter at a time

1. Close Deltarune.
2. Download the current UndertaleModTool release from its official GitHub page.
3. Choose one chapter folder, such as `chapter1_windows`.
4. Copy its clean `data.win` to `data.original.win` in that same folder. Never skip this.
5. Open `data.win` in UndertaleModTool.
6. Run `AiTelemetry.csx` using Scripts > Run other script.
7. Use **Save As**, writing the result as `data.telemetry.win` first.
8. Close the tool, rename `data.win` to `data.unmodded.win`, then rename
   `data.telemetry.win` to `data.win`.
9. Start the controller, then launch that chapter through Deltarune.

Validate the mod without sending any controls:

```powershell
python -m deltarune_agent telemetry --seconds 30
```

Switch to Deltarune during those 30 seconds. Room and position lines should
appear in PowerShell. After that succeeds, use the normal `run --live` command.

Repeat only for chapters you intend to play. Steam updates may replace modified
files; reapply against the new original rather than an old backup.

## Removal

Close the game, remove the patched `data.win`, and rename `data.unmodded.win`
back to `data.win`. Steam's Verify integrity command is a final recovery option.
