from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "mods"
    / "speed"
    / "AiSpeed.csx"
)
UPDATER = (
    Path(__file__).resolve().parents[1]
    / "mods"
    / "speed"
    / "tools"
    / "Update-DeltaMod-G3MTool.ps1"
)


def test_speed_mod_uses_verified_begin_step_and_supported_import_api():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'Data.Code.ByName("gml_Object_obj_time_Step_1")' in source
    assert "new CodeImportGroup(Data)" in source
    assert "imports.QueueAppend(beginStep, speedHook)" in source
    assert "imports.Import()" in source
    assert "AppendGML" not in source


def test_speed_mod_is_guarded_and_defaults_to_two_x():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const string marker = "AI_SPEED_MOD|1|"' in source
    assert 'global.__ai_speed_marker = ""AI_SPEED_MOD|1|""' in source
    assert "identityEvents.Any(item => item == null)" in source
    assert "global.__ai_speed_multiplier = 2;" in source
    assert "game_get_speed(gamespeed_fps)" in source
    assert "game_set_speed(global.__ai_speed_target_fps, gamespeed_fps)" in source


def test_speed_mod_controls_and_packet_are_complete():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "keyboard_check_pressed(vk_f8)" in source
    assert "keyboard_check_pressed(vk_f9)" in source
    assert "keyboard_check_pressed(vk_f10)" in source
    assert "min(10, global.__ai_speed_multiplier + 1)" in source
    assert "clamp(global.__ai_speed_multiplier, 1, 10)" in source
    assert '""DRSPEED|1|multiplier=""' in source
    assert '""|base_fps=""' in source
    assert '""|target_fps=""' in source
    assert '""127.0.0.1""' in source
    assert "42069" in source


def test_merge_tool_updater_is_verified_reversible_and_game_independent():
    source = UPDATER.read_text(encoding="utf-8")

    assert "G3MTool/releases/download/1.2.5/" in source
    assert (
        "408B09B8D43416C4C05779329887D3A2D53C7C6C2FE8C240CD3BC2B1E41C5AB6"
        in source
    )
    assert (
        "3D313FABBF0454DB9837196F2A7039DEFFE7013E02AA0ED3AAC1C546EAA242E6"
        in source
    )
    assert "$ReleaseArchivePath" in source
    assert "G3MTool-win32.exe.pre-1.2.5.bak" in source
    assert "[switch]$Restore" in source
    assert "[IO.File]::Replace" in source
    assert "data.win" not in source
