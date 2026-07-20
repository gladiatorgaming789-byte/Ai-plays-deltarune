using System;
using System.Linq;
using UndertaleModLib.Compiler;

EnsureDataLoaded();

const string marker = "DRTEL|6|";
if (Data.Strings.Any(item => item.Content.Contains(marker)))
{
    ScriptMessage("AI telemetry v6 is already present in this data.win. No changes were made.");
    return;
}

bool hasV2 = Data.Strings.Any(item => item.Content.Contains("DRTEL|2|"));
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

string Sender(string mode, string tickName, bool includeNearby = false) => @"
// AI_TELEMETRY_V6 - core, motion, then optional rich telemetry
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
    var _ai_core_message = ""DRTEL|6|" + mode + @"|"" + string(room) + ""|"" +
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
    var _ai_motion_buffer = buffer_create(384, buffer_grow, 1);
    var _ai_motion_message = ""DRTEL|6|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|"" + string(room_width) + ""|"" +
        string(room_height) + ""|"" + _ai_sprite + ""|"" + string(image_index) + ""|"" +
        string(direction) + ""|"" + string(hspeed) + ""|"" + string(vspeed) + ""|"" +
        string(speed) + ""|"" + string(image_speed) + ""|end"";
    buffer_write(_ai_motion_buffer, buffer_string, _ai_motion_message);
    network_send_udp(
        global.__ai_tel_socket,
        ""127.0.0.1"",
        42069,
        _ai_motion_buffer,
        buffer_tell(_ai_motion_buffer)
    );
    buffer_delete(_ai_motion_buffer);

    // These fields are useful but vary more between game builds. If one is not
    // available, the already-sent motion packet still supplies sprite and direction.
    var _ai_near_name = """";
    var _ai_near_id = -4;
    var _ai_near_x = 0;
    var _ai_near_y = 0;
    var _ai_near_distance = -1;
" + (includeNearby ? @"
    var _ai_near = instance_nearest(x, y, obj_interactable);
    if (_ai_near != noone)
    {
        _ai_near_name = object_get_name(_ai_near.object_index);
        _ai_near_id = _ai_near.id;
        _ai_near_x = _ai_near.x;
        _ai_near_y = _ai_near.y;
        _ai_near_distance = point_distance(x, y, _ai_near.x, _ai_near.y);
    }
" : "") + @"
    var _ai_buffer = buffer_create(768, buffer_grow, 1);
    var _ai_message = ""DRTEL|6|" + mode + @"|"" + string(room) + ""|"" +
        room_get_name(room) + ""|"" + string(x) + ""|"" + string(y) + ""|"" +
        object_get_name(object_index) + ""|"" + string(room_width) + ""|"" +
        string(room_height) + ""|"" + _ai_sprite + ""|"" + string(image_index) + ""|"" +
        string(direction) + ""|"" + string(hspeed) + ""|"" + string(vspeed) + ""|"" +
        string(speed) + ""|"" + string(image_speed) + ""|"" + string(id) + ""|"" +
        string(xprevious) + ""|"" + string(yprevious) + ""|"" + string(bbox_left) + ""|"" +
        string(bbox_top) + ""|"" + string(bbox_right) + ""|"" + string(bbox_bottom) + ""|"" +
        string(depth) + ""|"" + string(image_xscale) + ""|"" + string(image_yscale) + ""|"" +
        string(room_speed) + ""|"" + string(current_time) + ""|"" + _ai_near_name + ""|"" +
        string(_ai_near_id) + ""|"" + string(_ai_near_x) + ""|"" + string(_ai_near_y) + ""|"" +
        string(_ai_near_distance) + ""|end"";
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
imports.QueueAppend(overworld, Sender("overworld", "__ai_tel_overworld_tick_v6", true));
imports.QueueAppend(overworldStep, Autosave());
imports.QueueAppend(battle, Sender("battle", "__ai_tel_battle_tick_v6"));
imports.QueueAppend(saveMenu, Sender("choice", "__ai_tel_save_menu_tick_v6"));
if (!hasV2)
{
    imports.QueueAppend(dialogue, Sender("dialogue", "__ai_tel_dialogue_tick_v6"));
    imports.QueueAppend(choiceNeo, Sender("choice", "__ai_tel_choice_neo_tick_v6"));
    imports.QueueAppend(choiceOld, Sender("choice", "__ai_tel_choice_old_tick_v6"));
}
imports.Import();

ScriptMessage(
    "AI telemetry v6 and the invisible room_krisroom test autosave were installed. " +
    "Use Save As to write the patched data.win only after preserving the original."
);
