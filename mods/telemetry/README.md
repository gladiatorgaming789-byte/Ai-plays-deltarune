# Deltarune telemetry mod

This optional UndertaleModTool script adds small GML telemetry hooks:

- `obj_mainchara` sends `overworld`, room ID/name, and position.
- `obj_heart` sends `battle`, room ID/name, and soul position.
- `obj_writer` sends `dialogue` while text is active.
- Deltarune's two choice objects send `choice` while a choice menu is active.
- `obj_savemenu` sends `choice`, so natural save menus are never mistaken for
  movement or a collision.

Telemetry v8 sends four independent packets in order: core room/position/state,
motion/animation/camera view, Deltarune's player-control gate, then rich player
collision context. If a field in the final layer is unavailable in a particular
game build, the controller still receives sprite, facing direction, velocity,
camera bounds, and the control gate from the earlier layers. The rich layer adds
the player's previous position, collision bounding box, instance ID, depth,
sprite scale, and game timing. It deliberately does
not query or transmit nearby interactable instances, identities, or positions;
the controller must form visual guesses and verify them through play. While dialogue or a menu is active, the receiver keeps
the latest player position alongside the state object's own position. The rich
packet also retains the control gate for compatibility. The controller uses a
sustained control-locked overworld sequence or dialogue that began automatically
as cutscene evidence. Dialogue opened by the agent's own object interaction
remains dialogue, and a missing player packet is never proof of a cutscene.

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

Apply v8 to a clean `data.win` or restored unmodded backup. Restore the clean
file first if any earlier telemetry version is installed, so obsolete appended
code is removed rather than layered underneath v8. The installer refuses to
append v8 when it detects an older telemetry sender.

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

Switch to Deltarune during those 30 seconds. Room, position, and four camera
values should appear in PowerShell. After that succeeds, use the normal
`run --live` command.

Repeat only for chapters you intend to play. Steam updates may replace modified
files; reapply against the new original rather than an old backup.

## Removal

Close the game, remove the patched `data.win`, and rename `data.unmodded.win`
back to `data.win`. Steam's Verify integrity command is a final recovery option.
