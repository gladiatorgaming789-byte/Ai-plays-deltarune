from deltarune_agent.telemetry_compat import telemetry_update_warning


def test_current_v9_telemetry_does_not_request_reinstallation():
    packet = (
        b"DRTEL|9|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=core|seq=31|control=0|end"
    )

    assert telemetry_update_warning(packet) is None


def test_older_telemetry_protocol_explains_data_win_update():
    warning = telemetry_update_warning(
        b"DRTEL|8|overworld|7|room_home|120|42|obj_mainchara|end"
    )

    assert warning is not None
    assert "protocol v8" in warning
    assert "does not update data.win" in warning
    assert "AiTelemetry.csx" in warning


def test_newer_telemetry_protocol_explains_controller_update():
    warning = telemetry_update_warning(
        b"DRTEL|10|overworld|7|room_home|120|42|obj_mainchara|"
        b"part=core|seq=31|end"
    )

    assert warning is not None
    assert "newer" in warning
    assert "Update the Python project" in warning


def test_unrelated_udp_packet_has_no_warning():
    assert telemetry_update_warning(b"not telemetry") is None
