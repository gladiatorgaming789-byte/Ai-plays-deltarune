from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .build_status import BuildStatus
from .gui import AgentGUI, WallMapModel
from .profile_panel import ProfileBuildPanel
from .profiles import MigrationResult, Profile, ProfileStore
from .reinforcement_panel import ReinforcementSettingsPanel
from .version import AGENT_REVISION


class IntegratedAgentGUI(AgentGUI):
    """Controller with profile, build-safety, and reinforcement settings tabs."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        store: ProfileStore,
        migration_result: MigrationResult,
    ) -> None:
        self.profile_store = store
        self.migration_result = migration_result
        self.active_profile = store.active()
        self.build_status: BuildStatus | None = None
        self.profile_summary_var = tk.StringVar(
            value=f"Profile: {self.active_profile.name}"
        )
        self.build_summary_var = tk.StringVar(value="Build: checking development…")
        self.main_tabs: ttk.Notebook
        self.controller_tab: ttk.Frame
        self.profile_tab: ttk.Frame
        self.reinforcement_tab: ttk.Frame
        self.profile_panel: ProfileBuildPanel
        self.reinforcement_panel: ReinforcementSettingsPanel
        super().__init__(root)
        self._update_window_title()

    def _build_main_area(self) -> None:
        real_root = self.root

        summary = ttk.Frame(real_root, padding=(12, 6, 12, 0))
        summary.pack(fill="x")
        ttk.Label(
            summary,
            textvariable=self.profile_summary_var,
            style="Status.TLabel",
        ).pack(side="left")
        ttk.Label(
            summary,
            textvariable=self.build_summary_var,
            style="Status.TLabel",
        ).pack(side="right")

        self.main_tabs = ttk.Notebook(real_root)
        self.main_tabs.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.controller_tab = ttk.Frame(self.main_tabs)
        self.profile_tab = ttk.Frame(self.main_tabs)
        self.reinforcement_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.controller_tab, text="Controller")
        self.main_tabs.add(self.profile_tab, text="Profiles & Build")
        self.main_tabs.add(self.reinforcement_tab, text="Reinforcement")

        self.root = self.controller_tab  # type: ignore[assignment]
        try:
            super()._build_main_area()
        finally:
            self.root = real_root

        self.profile_panel = ProfileBuildPanel(
            self.profile_tab,
            project_root=self.project_root,
            store=self.profile_store,
            migration_result=self.migration_result,
            can_switch_profile=self._can_switch_profile,
            on_profile_activated=self._profile_activated,
            on_build_status=self._build_status_changed,
        )
        self.reinforcement_panel = ReinforcementSettingsPanel(
            self.reinforcement_tab,
            project_root=self.project_root,
        )
        self.reinforcement_panel.pack(fill="both", expand=True)

    def _can_switch_profile(self) -> bool:
        return self.process is None or self.process.poll() is not None

    def _profile_activated(self, profile: Profile) -> None:
        self.active_profile = profile
        self.profile_summary_var.set(f"Profile: {profile.name}")
        self.window_memory = self.project_root / "memory" / "window_titles.json"

        self.map_model = WallMapModel()
        self.map_model.load_memory(self.project_root / "memory" / "navigation.json")
        self.map_model.load_room_views(
            self.project_root / "memory" / "room_views" / "index.json"
        )
        self._map_transform = None
        self._map_images = []
        self._map_image_cache.clear()
        self._map_view_state.clear()
        self._map_pan_anchor = None
        self._selected_map_target = None
        self._selected_guess_key = None
        self.room_var.set("")
        self._refresh_room_choices(select_current=False)
        self._clear_output()
        self.current_decision_var.set("Waiting to start")
        self.current_reason_var.set(
            f'Profile "{profile.name}" is active. Start the AI to use this memory and run history.'
        )
        self.current_location_var.set("Room: not reported")
        self.current_capture_var.set("Scene capture: waiting")
        self._redraw_map()
        self._append(
            self.ai_output,
            f'--- Active save profile: {profile.name} ---\n',
        )
        if hasattr(self, "reinforcement_panel"):
            self.reinforcement_panel.reload()
        self._update_window_title()

    def _build_status_changed(self, status: BuildStatus) -> None:
        self.build_status = status
        self.build_summary_var.set(f"Build: {status.label}")
        self._update_window_title()

    def _update_window_title(self) -> None:
        status = self.build_status.label if self.build_status else "checking updates"
        self.root.title(
            f"Deltarune AI Controller • {self.active_profile.name} • "
            f"{status} • {AGENT_REVISION}"
        )

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if hasattr(self, "profile_panel") and not self.profile_panel.confirm_testing_build():
            self.main_tabs.select(self.profile_tab)
            return
        super().start()


def launch_integrated_gui() -> None:
    root = tk.Tk()
    root.withdraw()
    project_root = Path(__file__).resolve().parent.parent
    store = ProfileStore()
    try:
        migration_result = store.migrate_legacy_data(project_root)
        store.activate(project_root, store.active().id)
    except OSError as exc:
        messagebox.showerror("Profile setup failed", str(exc), parent=root)
        root.destroy()
        return

    root.deiconify()
    IntegratedAgentGUI(
        root,
        store=store,
        migration_result=migration_result,
    )
    root.mainloop()
