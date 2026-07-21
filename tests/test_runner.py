from contextlib import redirect_stdout
from io import StringIO
import json

from deltarune_agent.runner import _runtime_status


def test_background_runtime_status_is_a_gui_event():
    output = StringIO()
    with redirect_stdout(output):
        _runtime_status(True, "background", "targeted input active")

    prefix, payload = output.getvalue().strip().split("\t", 1)
    assert prefix == "AI_GUI_EVENT"
    assert json.loads(payload) == {
        "kind": "runtime_status",
        "status": "background",
        "message": "targeted input active",
    }
