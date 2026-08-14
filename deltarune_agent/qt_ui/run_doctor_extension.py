"""Qt Runs-page integration for Automatic Run Doctor v0.5.

The extension is installed before :mod:`qt_ui.app` imports ``RunsPage``. Keeping
Doctor UI behavior here avoids coupling the large existing pages module to the
analysis engine and gives the feature a small, independently testable surface.
"""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import run_doctor as foundation
from .. import run_doctor_compare as comparison_engine
from .. import run_doctor_incidents as incident_engine
from . import pages


RUN_DOCTOR_GUI_VERSION = "0.5.0"


class _DoctorSignals(QObject):
    loaded = Signal(str, object, str)


class _DoctorTask(QRunnable):
    def __init__(self, candidate: Path, baseline: Path | None = None) -> None:
        super().__init__()
        self.candidate = candidate
        self.baseline = baseline
        self.signals = _DoctorSignals()

    def run(self) -> None:
        try:
            candidate_run = foundation.load_run(self.candidate)
            comparison = None
            if self.baseline is not None:
                baseline_run = foundation.load_run(self.baseline)
                report, comparison = comparison_engine.compare_runs(
                    baseline_run,
                    candidate_run,
                )
            else:
                report = incident_engine.analyze_run(candidate_run)
            comparison_engine.write_report(report, comparison)
            payload = report.as_dict()
            payload["doctor_version"] = comparison_engine.RUN_DOCTOR_VERSION
            if comparison is not None:
                payload["comparison"] = comparison.as_dict()
            self.signals.loaded.emit(str(self.candidate), payload, "")
        except Exception as exc:  # keep a failed analysis from taking down Qt
            self.signals.loaded.emit(
                str(self.candidate),
                {},
                f"{type(exc).__name__}: {exc}",
            )


def _severity_summary(payload: Mapping[str, Any]) -> str:
    counts = payload.get("severity_counts")
    if not isinstance(counts, Mapping):
        return "No severity summary"
    values = []
    for key in ("critical", "high", "medium", "low", "info"):
        try:
            count = int(counts.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            count = 0
        if count:
            values.append(f"{count} {key}")
    return " · ".join(values) if values else "No findings"


def doctor_badge(payload: Mapping[str, Any]) -> str:
    health = payload.get("health")
    if not isinstance(health, Mapping):
        return "Doctor analyzed"
    grade = str(health.get("grade") or "?")
    overall = health.get("overall")
    return f"Doctor {grade} · {overall if overall is not None else '?'} / 100"


def doctor_html(payload: Mapping[str, Any]) -> str:
    health = payload.get("health") if isinstance(payload.get("health"), Mapping) else {}
    grade = escape(str(health.get("grade") or "?"))
    overall = escape(str(health.get("overall") if health.get("overall") is not None else "?"))
    version = escape(str(payload.get("doctor_version") or "unknown"))
    incidents = payload.get("incidents") if isinstance(payload.get("incidents"), list) else []
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []

    parts = [
        f"<h2>Run Doctor <small>v{version}</small></h2>",
        f"<p><b>Health:</b> {overall}/100 · grade {grade}<br>",
        f"<b>Findings:</b> {len(findings)} · <b>Incidents:</b> {len(incidents)}<br>",
        f"<b>Severity:</b> {escape(_severity_summary(payload))}</p>",
    ]

    comparison = payload.get("comparison")
    if isinstance(comparison, Mapping):
        parts.append(
            "<h3>Previous-run comparison</h3>"
            f"<p><b>Comparability:</b> {escape(str(comparison.get('comparability') or '?'))}<br>"
            f"<b>Verdict:</b> {escape(str(comparison.get('verdict') or '?'))}<br>"
            f"<b>Health delta:</b> {escape(str(comparison.get('health_delta') or 0))}</p>"
        )

    parts.append("<h3>Incidents</h3>")
    if not incidents:
        parts.append("<p>No grouped incidents.</p>")
    for incident in incidents[:50]:
        if not isinstance(incident, Mapping):
            continue
        title = escape(str(incident.get("title") or "Incident"))
        severity = escape(str(incident.get("severity") or "info").upper())
        start = incident.get("start_step")
        end = incident.get("end_step")
        causal = incident.get("causal_note")
        parts.append(
            f"<p><b>[{severity}] {title}</b><br>"
            f"Steps {escape(str(start if start is not None else '?'))}–"
            f"{escape(str(end if end is not None else '?'))}<br>"
            f"Subsystem: {escape(str(incident.get('likely_primary_subsystem') or '?'))}"
            + (f"<br><i>{escape(str(causal))}</i>" if causal else "")
            + "</p>"
        )

    parts.append("<h3>Findings</h3>")
    if not findings:
        parts.append("<p>No Doctor findings for this run.</p>")
    for finding in findings[:100]:
        if not isinstance(finding, Mapping):
            continue
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), Mapping) else {}
        parts.append(
            f"<p><b>[{escape(str(finding.get('severity') or 'info').upper())}] "
            f"{escape(str(finding.get('title') or finding.get('finding_type') or 'Finding'))}</b><br>"
            f"Steps {escape(str(evidence.get('start_step') if evidence.get('start_step') is not None else '?'))}–"
            f"{escape(str(evidence.get('end_step') if evidence.get('end_step') is not None else '?'))}<br>"
            f"{escape(str(finding.get('explanation') or ''))}<br>"
            f"<b>Engineering action:</b> {escape(str(finding.get('recommendation') or ''))}</p>"
        )
    return "".join(parts)


def _read_doctor(path: Path) -> dict[str, Any]:
    report_path = path / "run_doctor.json"
    if not report_path.is_file():
        return {}
    try:
        value = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _install_methods() -> None:
    runs_page = pages.RunsPage
    if getattr(runs_page, "_run_doctor_v05_installed", False):
        return

    original_build = runs_page._build
    original_reload = runs_page.reload
    original_show_overview = runs_page._show_overview

    def _build(self) -> None:
        original_build(self)
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        controls = QHBoxLayout()
        label = QLabel("Read-only post-run diagnosis. Doctor reports never change learned memory.")
        label.setWordWrap(True)
        controls.addWidget(label, 1)
        analyze = QPushButton("Analyze / refresh")
        compare = QPushButton("Compare previous")
        controls.addWidget(analyze)
        controls.addWidget(compare)
        layout.addLayout(controls)
        self.doctor_view = QTextBrowser()
        self.doctor_view.setOpenExternalLinks(False)
        layout.addWidget(self.doctor_view, 1)
        self.tabs.addTab(panel, "Run Doctor")
        self._doctor_tasks = []
        analyze.clicked.connect(lambda: self._run_doctor_analysis(False))
        compare.clicked.connect(lambda: self._run_doctor_analysis(True))

    def _reload(self) -> None:
        original_reload(self)
        for row in range(self.run_list.count()):
            item = self.run_list.item(row)
            path_text = str(item.data(pages.Qt.ItemDataRole.UserRole) or "")
            if not path_text:
                continue
            payload = _read_doctor(Path(path_text))
            if payload:
                item.setText(item.text() + "\n" + doctor_badge(payload))

    def _show_overview(self, run) -> None:
        original_show_overview(self, run)
        payload = _read_doctor(run.directory)
        if payload:
            self.doctor_view.setHtml(doctor_html(payload))
        else:
            self.doctor_view.setHtml(
                "<h2>Run Doctor</h2><p>This run has not been analyzed yet. "
                "Choose <b>Analyze / refresh</b> to create a read-only diagnosis.</p>"
            )

    def _previous_run_path(self) -> Path | None:
        row = self.run_list.currentRow()
        if row < 0 or row + 1 >= self.run_list.count():
            return None
        item = self.run_list.item(row + 1)
        value = str(item.data(pages.Qt.ItemDataRole.UserRole) or "")
        return Path(value) if value else None

    def _run_doctor_analysis(self, compare_previous: bool) -> None:
        if not self._selected_path:
            return
        candidate = Path(self._selected_path)
        baseline = self._previous_run_path() if compare_previous else None
        if compare_previous and baseline is None:
            self.doctor_view.setHtml(
                "<h2>Run Doctor</h2><p>No older run is available in this profile for comparison.</p>"
            )
            return
        self.doctor_view.setHtml("<h2>Run Doctor</h2><p>Analyzing saved run artifacts…</p>")
        task = _DoctorTask(candidate, baseline)
        task.signals.loaded.connect(self._doctor_loaded)
        self._doctor_tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _doctor_loaded(self, directory: str, payload: object, error: str) -> None:
        if directory != self._selected_path:
            return
        if error:
            self.doctor_view.setHtml(
                "<h2>Run Doctor</h2><p><b>Analysis failed:</b> "
                + escape(error)
                + "</p>"
            )
            return
        if isinstance(payload, Mapping):
            self.doctor_view.setHtml(doctor_html(payload))
        self.reload()

    runs_page._build = _build
    runs_page.reload = _reload
    runs_page._show_overview = _show_overview
    runs_page._previous_run_path = _previous_run_path
    runs_page._run_doctor_analysis = _run_doctor_analysis
    runs_page._doctor_loaded = _doctor_loaded
    runs_page._run_doctor_v05_installed = True


def install_runs_page_extension() -> None:
    """Install the v0.5 Runs-page extension exactly once."""
    _install_methods()


__all__ = [
    "RUN_DOCTOR_GUI_VERSION",
    "doctor_badge",
    "doctor_html",
    "install_runs_page_extension",
]
