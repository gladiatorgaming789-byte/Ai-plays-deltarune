using System;
using System.Linq;
using UndertaleModLib.Compiler;

EnsureDataLoaded();

const string marker = "AI_SPEED_MOD|1|";
if (Data.Strings.Any(item => item.Content.Contains(marker)))
{
    ScriptMessage(
        "AI speed mod is already present in this data.win. " +
        "No changes were made."
    );
    return;
}

var beginStep = Data.Code.ByName("gml_Object_obj_time_Step_1");
var identityEvents = new[]
{
    Data.Code.ByName("gml_Object_obj_mainchara_Create_0"),
    Data.Code.ByName("gml_Object_obj_mainchara_Step_0"),
};
if (beginStep == null || identityEvents.Any(item => item == null))
{
    throw new Exception(
        "This is not a supported Deltarune Chapters 1-5 data.win. " +
        "The required obj_time Begin Step and obj_mainchara events were not found, " +
        "so no changes were made."
    );
}

string speedHook = @"
// AI_SPEED_MOD|1| - simulation speed and localhost synchronization
if (!variable_global_exists(""__ai_speed_initialized""))
{
    global.__ai_speed_initialized = 1;
    global.__ai_speed_marker = ""AI_SPEED_MOD|1|"";
    global.__ai_speed_base_fps = game_get_speed(gamespeed_fps);
    if (global.__ai_speed_base_fps <= 0)
    {
        global.__ai_speed_base_fps = 30;
    }
    global.__ai_speed_multiplier = 2;
    global.__ai_speed_previous = 2;
    global.__ai_speed_target_fps = -1;
    global.__ai_speed_announce_tick = 999999;
    global.__ai_speed_force_announce = 1;
    global.__ai_speed_socket = network_create_socket(network_socket_udp);
}

var _ai_speed_changed = 0;
if (keyboard_check_pressed(vk_f8))
{
    if (global.__ai_speed_multiplier == 1)
    {
        global.__ai_speed_multiplier = max(2, global.__ai_speed_previous);
    }
    else
    {
        global.__ai_speed_previous = global.__ai_speed_multiplier;
        global.__ai_speed_multiplier = 1;
    }
    _ai_speed_changed = 1;
}
if (keyboard_check_pressed(vk_f9))
{
    global.__ai_speed_multiplier = max(1, global.__ai_speed_multiplier - 1);
    if (global.__ai_speed_multiplier > 1)
    {
        global.__ai_speed_previous = global.__ai_speed_multiplier;
    }
    _ai_speed_changed = 1;
}
if (keyboard_check_pressed(vk_f10))
{
    global.__ai_speed_multiplier = min(10, global.__ai_speed_multiplier + 1);
    if (global.__ai_speed_multiplier > 1)
    {
        global.__ai_speed_previous = global.__ai_speed_multiplier;
    }
    _ai_speed_changed = 1;
}

global.__ai_speed_multiplier = clamp(global.__ai_speed_multiplier, 1, 10);
var _ai_speed_target = round(
    global.__ai_speed_base_fps * global.__ai_speed_multiplier
);
var _ai_speed_current = game_get_speed(gamespeed_fps);
if (
    _ai_speed_target != global.__ai_speed_target_fps
    || abs(_ai_speed_current - _ai_speed_target) > 0.5
    || _ai_speed_changed
)
{
    global.__ai_speed_target_fps = _ai_speed_target;
    game_set_speed(global.__ai_speed_target_fps, gamespeed_fps);
    global.__ai_speed_force_announce = 1;
}

global.__ai_speed_announce_tick += 1;
var _ai_speed_announce_interval = max(
    1,
    round(global.__ai_speed_target_fps / 2)
);
if (
    global.__ai_speed_force_announce
    || global.__ai_speed_announce_tick >= _ai_speed_announce_interval
)
{
    global.__ai_speed_force_announce = 0;
    global.__ai_speed_announce_tick = 0;
    var _ai_speed_buffer = buffer_create(192, buffer_grow, 1);
    var _ai_speed_message = ""DRSPEED|1|multiplier="" +
        string(global.__ai_speed_multiplier) + ""|base_fps="" +
        string(global.__ai_speed_base_fps) + ""|target_fps="" +
        string(global.__ai_speed_target_fps) + ""|end"";
    buffer_write(_ai_speed_buffer, buffer_string, _ai_speed_message);
    network_send_udp(
        global.__ai_speed_socket,
        ""127.0.0.1"",
        42069,
        _ai_speed_buffer,
        buffer_tell(_ai_speed_buffer)
    );
    buffer_delete(_ai_speed_buffer);
}
";

var imports = new CodeImportGroup(Data);
imports.QueueAppend(beginStep, speedHook);
imports.Import();

ScriptMessage(
    "AI speed mod v1.2.0 was installed at 2x. " +
    "F8 toggles 1x/previous, " +
    "F9 decreases, and F10 increases up to 10x. Use Save As only after " +
    "preserving the original data.win."
);
