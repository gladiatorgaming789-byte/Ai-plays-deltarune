from deltarune_agent.run_doctor import EvidenceRange, RunDoctorFinding
from deltarune_agent.run_doctor_incidents import group_findings, health_scores


def _finding(
    finding_id,
    finding_type,
    subsystem,
    severity="high",
    start=0,
    end=10,
    room="room_a",
):
    return RunDoctorFinding(
        finding_id=finding_id,
        finding_type=finding_type,
        title=finding_type,
        severity=severity,
        confidence=0.9,
        subsystem=subsystem,
        explanation="fixture",
        recommendation="fixture",
        evidence=EvidenceRange(start, end),
        room=room,
    )


def test_overlapping_symptoms_group_but_unrelated_interval_stays_separate():
    incidents = group_findings(
        [
            _finding(
                "capture",
                "invalid_visual_streak",
                "capture/perception",
                start=10,
                end=80,
            ),
            _finding(
                "blind",
                "blind_search_streak",
                "planning/evidence utilization",
                start=60,
                end=100,
            ),
            _finding(
                "portal",
                "rapid_room_return",
                "navigation/portal handling",
                start=500,
                end=510,
            ),
        ]
    )
    assert len(incidents) == 2
    grouped = next(
        incident
        for incident in incidents
        if set(incident.finding_ids) == {"capture", "blind"}
    )
    assert "plausible contributor" in grouped.causal_note
    assert "not proof" in grouped.causal_note


def test_health_scores_are_category_specific_and_bounded():
    scores = health_scores(
        [
            _finding(
                "capture-critical",
                "capture",
                "capture/perception",
                "critical",
            ),
            _finding(
                "capture-high",
                "capture",
                "capture/perception",
                "high",
            ),
            _finding(
                "interaction-medium",
                "interaction",
                "interaction/planning",
                "medium",
            ),
        ]
    )
    assert scores["categories"]["perception_capture"] == 45
    assert scores["categories"]["interaction"] == 90
    assert 0 <= scores["overall"] <= 100


def test_incident_ids_are_deterministic_independent_of_input_order():
    first = _finding("a", "blind_search_streak", "planning/evidence utilization")
    second = _finding("b", "room_stall", "navigation/planning", start=5, end=20)
    forward = group_findings([first, second])
    reverse = group_findings([second, first])
    assert [incident.incident_id for incident in forward] == [
        incident.incident_id for incident in reverse
    ]
