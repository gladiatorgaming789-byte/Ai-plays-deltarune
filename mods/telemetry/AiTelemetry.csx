using System;
using System.Linq;
using UndertaleModLib.Compiler;

EnsureDataLoaded();

const string marker = "DRTEL|8|";
if (Data.Strings.Any(item => item.Content.Contains(marker)))
{
    ScriptMessage("AI telemetry v8 is already present in this data.win. No changes were made.");
    return;
}

string[] olderMarkers = { "DRTEL|1|", "DRTEL|2|", "DRTEL|3|", "DRTEL|4|", "DRTEL|5|", "DRTEL|6|", "DRTEL|7|" };
if (Data.Strings.Any(item => olderMarkers.Any(markerText => item.Content.Contains(markerText))))
{
    ScriptMessage(
        "An older AI telemetry patch is already present. Restore the clean data.win backup " +
        "before applying v8 so two telemetry senders are not layered together."
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
if (overworldStep == null || overworld == null || battle == null || dialogue == null ||
    choiceNeo == null || choiceOld == null || saveMenu == null)
{
    throw new Exception(
        "This build does not contain the expected movement, dialogue, and choice events. " +
        "The telemetry patch refused to modify it."
    );
}

string Sender(string mode, string tickName) => @"
// AI_TELEMETRY_V8 - core, motion/camera, control, then optional rich telemetry
if (!variable_global_exists(""__ai_tel_socket""))
{
    global.__ai_tel_socket = network_create_socket(network_socket_udp);
}
if (!variable_global_exists(""" + tickName + @"""))
{
    global." + tickName + @" = 0;
}
global." + tickName + @" += 1;
var _ai_interval = max(1, room_speed div 10);
if (global." + tickName + @" >= _ai_interval)
{
    global." + tickName + @" = 0;
    // Send the proven minimal payload before evaluating any optional fields.
    var _ai_core_buffer = buffer_create(256, buffer_grow, 1);
    var _ai_core_message = ""DRTEL|8|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|end"";
    buffer_write(_ai_core_buffer, buffer_string, _ai_core_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_core_buffer,
        buffer_tell(_ai_core_buffer)
    );
    buffer_delete(_ai_core_buffer);

    // Keep animation and movement separate from the more fragile rich fields.
    var _ai_sprite = """";
    if (sprite_index >= 0)
    {
        _ai_sprite = sprite_get_name(sprite_index);
    }
    var _ai_camera_x = 0;
    var _ai_camera_y = 0;
    var _ai_camera_width = room_width;
    var _ai_camera_height = room_height;
    var _ai_camera = view_camera[0];
    if (_ai_camera != -1)
    {
        _ai_camera_x = camera_get_view_x(_ai_camera);
        _ai_camera_y = camera_get_view_y(_ai_camera);
        _ai_camera_width = camera_get_view_width(_ai_camera);
        _ai_camera_height = camera_get_view_height(_ai_camera);
    }
    var _ai_motion_buffer = buffer_create(384, buffer_grow, 1);
    var _ai_motion_message = ""DRTEL|8|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|"" + string(room_width) + ""|"" +
        string(room_height) + ""|"" + _ai_sprite + ""|"" + string(image_index) + ""|"" +
        string(direction) + ""|"" + string(hspeed) + ""|"" + string(vspeed) + ""|"" +
        string(speed) + ""|"" + string(image_speed) + ""|"" +
        string(_ai_camera_x) + ""|"" + string(_ai_camera_y) + ""|"" +
        string(_ai_camera_width) + ""|"" + string(_ai_camera_height) + ""|end"";
    buffer_write(_ai_motion_buffer, buffer_string, _ai_motion_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_motion_buffer,
        buffer_tell(_ai_motion_buffer)
    );
    buffer_delete(_ai_motion_buffer);

    // Send the verified control gate before the optional collision fields.
    // Some builds cannot evaluate every rich field, but global.interact is
    // still valuable for distinguishing gameplay from a real control lock.
    var _ai_control_buffer = buffer_create(384, buffer_grow, 1);
    var _ai_control_message = ""DRTEL|8|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|"" + string(room_width) + ""|"" +
        string(room_height) + ""|"" + _ai_sprite + ""|"" + string(image_index) + ""|"" +
        string(direction) + ""|"" + string(hspeed) + ""|"" + string(vspeed) + ""|"" +
        string(speed) + ""|"" + string(image_speed) + ""|"" +
        string(_ai_camera_x) + ""|"" + string(_ai_camera_y) + ""|"" +
        string(_ai_camera_width) + ""|"" + string(_ai_camera_height) + ""|"" +
        string(global.interact) + ""|end"";
    buffer_write(_ai_control_buffer, buffer_string, _ai_control_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_control_buffer,
        buffer_tell(_ai_control_buffer)
    );
    buffer_delete(_ai_control_buffer);

    // Keep the old field positions for parser compatibility, but deliberately
    // leave nearby interactable identity and coordinates empty. The agent must
    // form and test its own visual hypotheses.
    var _ai_near_name = """";
    var _ai_near_id = -4;
    var _ai_near_x = 0;
    var _ai_near_y = 0;
    var _ai_near_distance = -1;
    var _ai_buffer = buffer_create(768, buffer_grow, 1);
    var _ai_message = ""DRTEL|8|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|"" + string(room_width) + ""|"" +
        string(room_height) + ""|"" + _ai_sprite + ""|"" + string(image_index) + ""|"" +
        string(direction) + ""|"" + string(hspeed) + ""|"" + string(vspeed) + ""|"" +
        string(speed) + ""|"" + string(image_speed) + ""|"" +
        string(_ai_camera_x) + ""|"" + string(_ai_camera_y) + ""|"" +
        string(_ai_camera_width) + ""|"" + string(_ai_camera_height) + ""|"" +
        string(id) + ""|"" +
        string(xprevious) + ""|"" + string(yprevious) + ""|"" + string(bbox_left) + ""|"" +
        string(bbox_top) + ""|"" + string(bbox_right) + ""|"" + string(bbox_bottom) + ""|"" +
        string(depth) + ""|"" + string(image_xscale) + ""|"" + string(image_yscale) + ""|"" +
        string(room_speed) + ""|"" + string(current_time) + ""|"" + _ai_near_name + ""|"" +
        string(_ai_near_id) + ""|"" + string(_ai_near_x) + ""|"" + string(_ai_near_y) + ""|"" +
        string(_ai_near_distance) + ""|"" + string(global.interact) + ""|end"";
    buffer_write(_ai_buffer, buffer_string, _ai_message);
    network_send_udp(global.__ai_tel_socket, ""127.0.0.1"", 42069, _ai_buffer, buffer_tell(_ai_buffer));
    buffer_delete(_ai_buffer);
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

var imports = new CodeImportGroup(Data);
imports.QueueAppend(overworld, Sender("overworld", "__ai_tel_overworld_tick_v8"));
imports.QueueAppend(overworldStep, Autosave());
imports.QueueAppend(battle, Sender("battle", "__ai_tel_battle_tick_v8"));
imports.QueueAppend(saveMenu, Sender("choice", "__ai_tel_save_menu_tick_v8"));
imports.QueueAppend(dialogue, Sender("dialogue", "__ai_tel_dialogue_tick_v8"));
imports.QueueAppend(choiceNeo, Sender("choice", "__ai_tel_choice_neo_tick_v8"));
imports.QueueAppend(choiceOld, Sender("choice", "__ai_tel_choice_old_tick_v8"));
imports.Import();

ScriptMessage(
    "AI telemetry v8 camera visibility, control state, and the invisible room_krisroom test autosave were installed. " +
    "Use Save As to write the patched data.win only after preserving the original."
);
