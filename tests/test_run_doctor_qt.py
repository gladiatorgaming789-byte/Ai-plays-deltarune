import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from deltarune_agent.qt_ui.run_doctor_extension import (  # noqa: E402
    doctor_badge,
    doctor_html,
    install_runs_page_extension,
)
from deltarune_agent.qt_ui.pages import RunsPage  # noqa: E402


def test_doctor_badge_uses_health_grade_and_score():
    assert doctor_badge({"health": {"grade": "B", "overall": 84.5}}) == (
        "Doctor B · 84.5 / 100"
    )


def test_doctor_html_escapes_artifact_text_and_shows_engineering_action():
    html = doctor_html(
        {
            "doctor_version": "0.4.0",
            "health": {"grade": "C", "overall": 72},
            "severity_counts": {"high": 1},
            "incidents": [],
            "findings": [
                {
                    "severity": "high",
                    "title": "Bad <capture>",
                    "explanation": "Observed & invalid",
                    "recommendation": "Inspect <fallback>",
                    "evidence": {"start_step": 10, "end_step": 20},
                }
            ],
        }
    )
    assert "Bad &lt;capture&gt;" in html
    assert "Observed &amp; invalid" in html
    assert "Inspect &lt;fallback&gt;" in html
    assert "Engineering action" in html


def test_runs_page_extension_is_idempotent():
    install_runs_page_extension()
    first_build = RunsPage._build
    install_runs_page_extension()
    assert RunsPage._build is first_build
    assert RunsPage._run_doctor_v05_installed is True
