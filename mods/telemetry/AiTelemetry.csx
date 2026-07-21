using System;
using System.Linq;
using UndertaleModLib.Compiler;

EnsureDataLoaded();

const string marker = "DRTEL|9|";
if (Data.Strings.Any(item => item.Content.Contains(marker)))
{
    ScriptMessage("AI telemetry v9 is already present in this data.win. No changes were made.");
    return;
}

string[] olderMarkers =
{
    "DRTEL|1|", "DRTEL|2|", "DRTEL|3|", "DRTEL|4|", "DRTEL|5|",
    "DRTEL|6|", "DRTEL|7|", "DRTEL|8|"
};
if (Data.Strings.Any(item => olderMarkers.Any(markerText => item.Content.Contains(markerText))))
{
    ScriptMessage(
        "An older AI telemetry patch is already present. Restore the clean data.win backup " +
        "before applying v9 so two telemetry senders are not layered together."
    );
    return;
}

var overworldStep = Data.Code.ByName("gml_Object_obj_mainchara_Step_0");
var overworld = Data.Code.ByName("gml_Object_obj_mainchara_Draw_0");
var battle = Data.Code.ByName("gml_Object_obj_heart_Draw_0");
var dialogue = Data.Code.ByName("gml_Object_obj_writer_Draw_0");
var choiceNeo = Data.Code.ByName("gml_Object_obj_choicer_neo_Draw_0");
var choiceOld = Data.Code.ByName("gml_Object_obj_choicer_old_Draw_0");
var saveMenu = Data.Code.ByName("gml_Object_obj_savemenu_Draw_0");

var requiredEvents = new[]
{
    (Name: "obj_mainchara Step", Code: overworldStep),
    (Name: "obj_mainchara Draw", Code: overworld),
    (Name: "obj_heart Draw", Code: battle),
    (Name: "obj_writer Draw", Code: dialogue),
    (Name: "obj_choicer_neo Draw", Code: choiceNeo),
    (Name: "obj_choicer_old Draw", Code: choiceOld),
    (Name: "obj_savemenu Draw", Code: saveMenu),
};
var missingEvents = requiredEvents
    .Where(item => item.Code == null)
    .Select(item => item.Name)
    .ToArray();
if (missingEvents.Length > 0)
{
    throw new Exception(
        "This build is missing required events: " + string.Join(", ", missingEvents) + ". " +
        "The telemetry patch refused to modify it."
    );
}

string Sender(string mode, string tickName, string sequenceName) => @"
// AI_TELEMETRY_V9 - independently mergeable, visible-player telemetry packets
if (!variable_global_exists(""__ai_tel_socket""))
{
    global.__ai_tel_socket = network_create_socket(network_socket_udp);
}
if (!variable_global_exists(""" + tickName + @"""))
{
    global." + tickName + @" = 0;
}
if (!variable_global_exists(""" + sequenceName + @"""))
{
    global." + sequenceName + @" = 0;
}
global." + tickName + @" += 1;
global." + sequenceName + @" += 1;
var _ai_sequence = global." + sequenceName + @";
var _ai_prefix = ""DRTEL|9|" + mode + @"|"" + string(room) + ""|"" +
    room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
    object_get_name(object_index) + ""|"";

// Send room/position every drawn frame so an observed room transition retains
// the source position immediately before the warp. Camera, control, collision,
// and player render geometry use the same sequence; timing stays at ten samples
// per second to keep overhead small.
var _ai_core_buffer = buffer_create(256, buffer_grow, 1);
var _ai_core_message = _ai_prefix + ""part=core|seq="" +
    string(_ai_sequence) + ""|control="" + string(global.interact) + ""|end"";
buffer_write(_ai_core_buffer, buffer_string, _ai_core_message);
network_send_udp(
    global.__ai_tel_socket,
    ""127.0.0.1"",
    42069,
    _ai_core_buffer,
    buffer_tell(_ai_core_buffer)
);
buffer_delete(_ai_core_buffer);

    // Motion, camera, and global.interact were all observed working in v8.
    // A sequence number lets the Python receiver merge this with the matching
    // core packet without mixing two frames together.
    var _ai_sprite = """";
    if (sprite_index >= 0)
    {
        _ai_sprite = sprite_get_name(sprite_index);
    }
    var _ai_camera_x = 0;
    var _ai_camera_y = 0;
    var _ai_camera_width = room_width;
    var _ai_camera_height = room_height;
    var _ai_camera_angle = 0;
    var _ai_camera = view_camera[0];
    if (_ai_camera != -1)
    {
        _ai_camera_x = camera_get_view_x(_ai_camera);
        _ai_camera_y = camera_get_view_y(_ai_camera);
        _ai_camera_width = camera_get_view_width(_ai_camera);
        _ai_camera_height = camera_get_view_height(_ai_camera);
        _ai_camera_angle = camera_get_view_angle(_ai_camera);
    }
    var _ai_motion_buffer = buffer_create(640, buffer_grow, 1);
    var _ai_motion_message = _ai_prefix + ""part=motion|seq="" +
        string(_ai_sequence) + ""|room_width="" + string(room_width) +
        ""|room_height="" + string(room_height) + ""|sprite="" + _ai_sprite +
        ""|image_index="" + string(image_index) + ""|direction="" +
        string(direction) + ""|hspeed="" + string(hspeed) + ""|vspeed="" +
        string(vspeed) + ""|speed="" + string(speed) + ""|image_speed="" +
        string(image_speed) + ""|camera_x="" + string(_ai_camera_x) +
        ""|camera_y="" + string(_ai_camera_y) + ""|camera_width="" +
        string(_ai_camera_width) + ""|camera_height="" +
        string(_ai_camera_height) + ""|camera_angle="" +
        string(_ai_camera_angle) + ""|control="" + string(global.interact) + ""|end"";
    buffer_write(_ai_motion_buffer, buffer_string, _ai_motion_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_motion_buffer,
        buffer_tell(_ai_motion_buffer)
    );
    buffer_delete(_ai_motion_buffer);

    // Collision bounds are isolated from render/timing details. The v8 run
    // proved that its combined rich packet could fail as a whole; v9 keeps a
    // failure in one optional group from corrupting packets already sent.
    var _ai_collision_buffer = buffer_create(384, buffer_grow, 1);
    var _ai_collision_message = _ai_prefix + ""part=collision|seq="" +
        string(_ai_sequence) + ""|instance_id="" + string(id) +
        ""|bbox_left="" + string(bbox_left) + ""|bbox_top="" + string(bbox_top) +
        ""|bbox_right="" + string(bbox_right) + ""|bbox_bottom="" +
        string(bbox_bottom) + ""|end"";
    buffer_write(_ai_collision_buffer, buffer_string, _ai_collision_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_collision_buffer,
        buffer_tell(_ai_collision_buffer)
    );
    buffer_delete(_ai_collision_buffer);

    var _ai_sprite_width = 0;
    var _ai_sprite_height = 0;
    var _ai_sprite_xoffset = 0;
    var _ai_sprite_yoffset = 0;
    if (sprite_index >= 0)
    {
        _ai_sprite_width = sprite_get_width(sprite_index);
        _ai_sprite_height = sprite_get_height(sprite_index);
        _ai_sprite_xoffset = sprite_get_xoffset(sprite_index);
        _ai_sprite_yoffset = sprite_get_yoffset(sprite_index);
    }
    var _ai_render_buffer = buffer_create(512, buffer_grow, 1);
    var _ai_render_message = _ai_prefix + ""part=render|seq="" +
        string(_ai_sequence) + ""|depth="" + string(depth) +
        ""|image_xscale="" + string(image_xscale) + ""|image_yscale="" +
        string(image_yscale) + ""|image_alpha="" + string(image_alpha) +
        ""|visible="" + string(visible) + ""|sprite_width="" +
        string(_ai_sprite_width) + ""|sprite_height="" +
        string(_ai_sprite_height) + ""|sprite_xoffset="" +
        string(_ai_sprite_xoffset) + ""|sprite_yoffset="" +
        string(_ai_sprite_yoffset) + ""|end"";
    buffer_write(_ai_render_buffer, buffer_string, _ai_render_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_render_buffer,
        buffer_tell(_ai_render_buffer)
    );
    buffer_delete(_ai_render_buffer);

    var _ai_interval = max(1, room_speed div 10);
    if (global." + tickName + @" >= _ai_interval)
    {
        global." + tickName + @" = 0;

    // Lower-rate timing is isolated from the frame-accurate navigation packets.
    var _ai_timing_buffer = buffer_create(256, buffer_grow, 1);
    var _ai_timing_message = _ai_prefix + ""part=timing|seq="" +
        string(_ai_sequence) + ""|room_speed="" + string(room_speed) +
        ""|fps="" + string(fps) + ""|end"";
    buffer_write(_ai_timing_buffer, buffer_string, _ai_timing_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_timing_buffer,
        buffer_tell(_ai_timing_buffer)
    );
    buffer_delete(_ai_timing_buffer);
    }
";

string Autosave() => @"
// AI_BACKGROUND_AUTOSAVE_V1 - one invisible checkpoint per game session
if (!variable_global_exists(""__ai_start_autosave_done""))
{
    global.__ai_start_autosave_done = 0;
}
if (room == room_krisroom && global.__ai_start_autosave_done == 0)
{
    global.__ai_start_autosave_done = 1;
    scr_save();
}
";

// CodeImportGroup is the supported batched compiler API in the installed
// UndertaleModTool 0.9.1.2 and remains compatible with 0.8.4.1.
var imports = new CodeImportGroup(Data);
imports.QueueAppend(
    overworld,
    Sender("overworld", "__ai_tel_overworld_tick_v9", "__ai_tel_overworld_sequence_v9")
);
imports.QueueAppend(overworldStep, Autosave());
imports.QueueAppend(
    battle,
    Sender("battle", "__ai_tel_battle_tick_v9", "__ai_tel_battle_sequence_v9")
);
imports.QueueAppend(
    saveMenu,
    Sender("choice", "__ai_tel_save_menu_tick_v9", "__ai_tel_save_menu_sequence_v9")
);
imports.QueueAppend(
    dialogue,
    Sender("dialogue", "__ai_tel_dialogue_tick_v9", "__ai_tel_dialogue_sequence_v9")
);
imports.QueueAppend(
    choiceNeo,
    Sender("choice", "__ai_tel_choice_neo_tick_v9", "__ai_tel_choice_neo_sequence_v9")
);
imports.QueueAppend(
    choiceOld,
    Sender("choice", "__ai_tel_choice_old_tick_v9", "__ai_tel_choice_old_sequence_v9")
);
imports.Import();

ScriptMessage(
    "AI telemetry v9 packet merging, camera/player detail, collision bounds, " +
    "and the invisible room_krisroom test autosave were installed. " +
    "Use Save As to write the patched data.win only after preserving the original."
);
