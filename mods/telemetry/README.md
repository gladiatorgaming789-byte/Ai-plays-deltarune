# Deltarune telemetry mod

This optional UndertaleModTool script adds small GML telemetry hooks:

- `obj_mainchara` sends `overworld`, room ID/name, and position.
- `obj_heart` sends `battle`, room ID/name, and soul position.
- `obj_writer` sends `dialogue` while text is active.
- Deltarune's two choice objects send `choice` while a choice menu is active.
- `obj_savemenu` sends `choice`, so natural save menus are never mistaken for
  movement or a collision.

Telemetry v9 uses named, independently mergeable packet parts. Each part has a
per-object sequence number, so a delayed UDP datagram cannot combine camera or
collision data from two different frames:

- `core`: mode, room ID/name, instance origin, and object name;
- `motion`: room and camera bounds/angle, sprite, animation, velocity, and
  Deltarune's `global.interact` player-control gate;
- `collision`: current instance ID and collision bounding box;
- `render`: depth, scale, alpha, visibility, and sprite size/origin;
- `timing`: room speed and measured game FPS.

The `core`, `motion`, `collision`, and `render` parts are emitted every drawn
frame. This preserves the last player-observable source position and geometry
immediately before a room transition instead of assigning a warp to an older
ten-Hz sample. Timing details remain limited to about ten samples per second.
The receiver combines matching parts, rejects older sequence numbers, derives
the observed movement delta between samples, and records both the raw GameMaker instance
origin and the collision-foot point (bounding-box center/bottom). On a room
change it records the last observed source room, origin, foot, and facing as
transition evidence.

One optional group cannot invalidate an already-received core packet. This is
important because the recorded v8 diagnostic run received all 2,000 core/motion/control
samples but none of its all-or-nothing rich packets. v9 isolates collision,
render, and timing expressions and parses each named optional value
independently.

The patch deliberately does not query or transmit nearby interactable
instances, identities, positions, room-warp objects, choice text, selection
indexes, or option counts. The controller must form visual guesses and verify
them through play. While dialogue or a menu is active, the receiver attaches the
latest player origin, foot, sprite/facing, collision box, and camera context
alongside the state object's own origin.

Deltarune does not keep Kris's overworld facing in GameMaker's built-in
`direction` variable. The controller derives facing from the verified Chapter 1
sprite families `spr_krisd`, `spr_krisl`, `spr_krisr`, and `spr_krisu`, including
their underscore-suffixed variants. The raw GameMaker value remains logged as
`direction`, while the useful value is logged as `facing_direction`.

For repeatable early-game testing, the patch calls Deltarune's native
`scr_save()` once per game session when `room_krisroom` begins. It creates no
visible or collidable object, so it cannot alter navigation or become an
artificial interaction target. This intentionally writes the current save slot.

Packets use UDP to `127.0.0.1:42069` only. The Python controller binds only to
localhost. The installer uses `CodeImportGroup`, verified against the installed
UndertaleModTool 0.9.1.2 scripts, and remains compatible with 0.8.4.1.

Apply v9 to a clean `data.win` or restored unmodded backup. Restore the clean
file first if any earlier telemetry version is installed, so obsolete appended
code is removed rather than layered underneath v9. The installer refuses to
append v9 when it detects an older telemetry sender.

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

Switch to Deltarune during those 30 seconds. Room, origin position, sprite,
facing, and camera values should appear. Normal-run event records additionally
contain packet sequence/parts, control state, collision-foot position, and
observed transition-source evidence. If collision fields remain blank,
state/camera telemetry will continue working and the missing packet part will
identify the failed optional group. After that succeeds, use the normal
`run --live` command.

Repeat only for chapters you intend to play. Steam updates may replace modified
files; reapply against the new original rather than an old backup.

## Removal

Close the game, remove the patched `data.win`, and rename `data.unmodded.win`
back to `data.win`. Steam's Verify integrity command is a final recovery option.
