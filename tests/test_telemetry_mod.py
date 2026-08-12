from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "mods"
    / "telemetry"
    / "AiTelemetry.csx"
).read_text(encoding="utf-8")


def test_v9_telemetry_reports_sequenced_camera_collision_and_control_state():
    assert 'const string marker = "DRTEL|9|"' in SCRIPT
    assert "part=core|seq=" in SCRIPT
    assert "part=motion|seq=" in SCRIPT
    assert "part=collision|seq=" in SCRIPT
    assert "part=render|seq=" in SCRIPT
    assert "part=timing|seq=" in SCRIPT
    assert "camera_get_view_x" in SCRIPT
    assert "camera_get_view_y" in SCRIPT
    assert "camera_get_view_width" in SCRIPT
    assert "camera_get_view_height" in SCRIPT
    assert "camera_get_view_angle" in SCRIPT
    assert "string(global.interact)" in SCRIPT
    assert "string(self.bbox_left)" in SCRIPT
    assert "string(self.bbox_top)" in SCRIPT
    assert "string(self.bbox_right)" in SCRIPT
    assert "string(self.bbox_bottom)" in SCRIPT
    assert SCRIPT.index("_ai_core_message") < SCRIPT.index("var _ai_interval")


def test_v9_telemetry_does_not_query_hidden_interactables_or_warps():
    assert "instance_nearest" not in SCRIPT
    assert "obj_interactable" not in SCRIPT
    assert "room_goto" not in SCRIPT
    assert "_ai_near" not in SCRIPT


def test_installer_refuses_to_layer_v9_over_older_telemetry():
    assert '"DRTEL|8|"' in SCRIPT
    assert "Restore the clean data.win backup" in SCRIPT


def test_v9_uses_current_undertale_modtool_batch_import_api():
    assert "new CodeImportGroup(Data)" in SCRIPT
    assert "imports.QueueAppend(" in SCRIPT
    assert "imports.Import();" in SCRIPT
    assert ".AppendGML(" not in SCRIPT


def test_navigation_core_camera_and_bbox_are_emitted_before_optional_render():
    assert SCRIPT.index("_ai_core_message") < SCRIPT.index("_ai_motion_message")
    assert SCRIPT.index("_ai_motion_message") < SCRIPT.index("_ai_collision_message")
    assert SCRIPT.index("_ai_collision_message") < SCRIPT.index("_ai_render_message")
    assert SCRIPT.index("_ai_render_message") < SCRIPT.index("var _ai_interval")
    assert SCRIPT.index("var _ai_interval") < SCRIPT.index("_ai_timing_message")


def test_csx_and_embedded_gml_delimiters_are_balanced():
    assert SCRIPT.count("{") == SCRIPT.count("}")
    assert SCRIPT.count("(") == SCRIPT.count(")")
    assert SCRIPT.count('"') % 2 == 0
