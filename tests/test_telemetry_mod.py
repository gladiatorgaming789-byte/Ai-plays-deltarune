from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "mods"
    / "telemetry"
    / "AiTelemetry.csx"
).read_text(encoding="utf-8")


def test_v8_telemetry_reports_camera_bounds_and_control_state():
    assert 'const string marker = "DRTEL|8|"' in SCRIPT
    assert "camera_get_view_x" in SCRIPT
    assert "camera_get_view_y" in SCRIPT
    assert "camera_get_view_width" in SCRIPT
    assert "camera_get_view_height" in SCRIPT
    assert "string(global.interact)" in SCRIPT
    assert "_ai_control_message" in SCRIPT
    assert SCRIPT.index("_ai_control_message") < SCRIPT.index("_ai_near_name")


def test_v8_telemetry_does_not_query_hidden_interactables():
    assert "instance_nearest" not in SCRIPT
    assert "obj_interactable" not in SCRIPT


def test_installer_refuses_to_layer_v8_over_older_telemetry():
    assert '"DRTEL|7|"' in SCRIPT
    assert "Restore the clean data.win backup" in SCRIPT
