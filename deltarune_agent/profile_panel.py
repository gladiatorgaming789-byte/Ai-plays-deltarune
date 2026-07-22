from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import queue
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from .build_status import BuildStatus, DEVELOPMENT_BRANCH, inspect_build
from .profiles import MigrationResult, Profile, ProfileStore
from .version import AGENT_REVISION


class ProfileBuildPanel(ttk.Frame):
    """Profile management and build-safety controls embedded in the main GUI."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        project_root: Path,
        store: ProfileStore,
        migration_result: MigrationResult | None = None,
        can_switch_profile: Callable[[], bool],
        on_profile_activated: Callable[[Profile], None],
        on_build_status: Callable[[BuildStatus], None],
    ) -> None:
        super().__init__(parent, padding=16)
        self.project_root = project_root
        self.store = store
        self.can_switch_profile = can_switch_profile
        self.on_profile_activated = on_profile_activated
        self.on_build_status = on_build_status
        self.profiles_by_name: dict[str, Profile] = {}
        self.status: BuildStatus | None = None
        self._checking_build = False
        self._build_results: queue.Queue[BuildStatus] = queue.Queue()

        self.profile_var = tk.StringVar()
        self.profile_detail_var = tk.StringVar()
        self.branch_var = tk.StringVar(value="Checking branch and updates…")
        self.revision_var = tk.StringVar(value=f"Agent revision: {AGENT_REVISION}")
        self.notice_var = tk.StringVar()

        self._build_ui()
        self._refresh_profiles(select_active=True)
        if migration_result and migration_result.migrated:
            self.notice_var.set(
                "Moved "
                + ", ".join(migration_result.migrated)
                + " into the Default AppData profile. "
                + f"A verified backup was saved at {migration_result.backup_directory}."
            )
        else:
            active = self.store.active()
            self.notice_var.set(
                f"Active profile: {active.name}. Its memory and runs are stored in AppData."
            )
        self.after(100, self.refresh_build_status)

    def _build_ui(self) -> None:
        self.pack(fill="both", expand=True)

        ttk.Label(
            self,
            text="Profiles & Build Safety",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Keep separate AI memories and run histories, and verify that this "
                "checkout is the current development build before testing."
            ),
            style="Subtitle.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(2, 14))

        status_frame = ttk.LabelFrame(self, text="Build safety", padding=12)
        status_frame.pack(fill="x")
        self.branch_label = ttk.Label(
            status_frame,
            textvariable=self.branch_var,
            font=("Segoe UI", 11, "bold"),
        )
        self.branch_label.pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.revision_var).pack(
            anchor="w", pady=(3, 0)
        )
        self.check_button = ttk.Button(
            status_frame,
            text="Check for updates",
            command=self.refresh_build_status,
        )
        self.check_button.pack(anchor="e", pady=(7, 0))

        profile_frame = ttk.LabelFrame(self, text="Save profile", padding=12)
        profile_frame.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(profile_frame)
        row.pack(fill="x")
        ttk.Label(row, text="Profile:").pack(side="left")
        self.profile_box = ttk.Combobox(
            row,
            textvariable=self.profile_var,
            state="readonly",
            width=30,
        )
        self.profile_box.pack(side="left", padx=(6, 8))
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_selected)
        ttk.Button(row, text="New", command=self._new_profile).pack(
            side="left", padx=2
        )
        ttk.Button(row, text="Duplicate", command=self._duplicate_profile).pack(
            side="left", padx=2
        )
        ttk.Button(row, text="Rename", command=self._rename_profile).pack(
            side="left", padx=2
        )
        ttk.Button(row, text="Delete", command=self._delete_profile).pack(
            side="left", padx=2
        )
        ttk.Button(row, text="Open folder", command=self._open_profile_folder).pack(
            side="left", padx=2
        )
        ttk.Label(
            profile_frame,
            textvariable=self.profile_detail_var,
            wraplength=900,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(
            self,
            text=(
                "Profile switching is disabled while the AI is running so one run "
                "cannot write into two different save profiles."
            ),
            style="Subtitle.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(10, 0))
        ttk.Label(
            self,
            textvariable=self.notice_var,
            wraplength=900,
        ).pack(anchor="w", pady=(8, 0))

    def _refresh_profiles(self, *, select_active: bool = False) -> None:
        profiles = self.store.profiles()
        self.profiles_by_name = {profile.name: profile for profile in profiles}
        self.profile_box["values"] = tuple(self.profiles_by_name)
        selected = (
            self.store.active()
            if select_active
            else self.profiles_by_name.get(self.profile_var.get())
        )
        if selected is None:
            selected = profiles[0]
        self.profile_var.set(selected.name)
        self._update_profile_detail(selected)

    def _selected_profile(self) -> Profile:
        profile = self.profiles_by_name.get(self.profile_var.get())
        if profile is None:
            raise ValueError("Select a profile first.")
        return profile

    def _update_profile_detail(self, profile: Profile) -> None:
        memory_files, run_folders = self.store.profile_file_counts(profile.id)
        self.profile_detail_var.set(
            f"{memory_files} memory file(s), {run_folders} run folder(s) • "
            f"Stored in {self.store.profile_directory(profile.id)}"
        )

    def _allow_profile_change(self) -> bool:
        if self.can_switch_profile():
            return True
        messagebox.showinfo(
            "Stop AI first",
            "Stop the AI before changing, creating, duplicating, renaming, or deleting profiles.",
            parent=self.winfo_toplevel(),
        )
        self._refresh_profiles(select_active=True)
        return False

    def _activate(self, profile: Profile, notice: str) -> bool:
        try:
            activated = self.store.activate(self.project_root, profile.id)
        except OSError as exc:
            messagebox.showerror(
                "Could not activate profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            self._refresh_profiles(select_active=True)
            return False
        self._refresh_profiles(select_active=True)
        self.notice_var.set(notice)
        self.on_profile_activated(activated)
        return True

    def _profile_selected(self, _event: object = None) -> None:
        if not self._allow_profile_change():
            return
        try:
            profile = self._selected_profile()
        except ValueError as exc:
            messagebox.showerror(
                "Could not switch profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return
        self._activate(profile, f"Active profile changed to {profile.name}.")

    def _new_profile(self) -> None:
        if not self._allow_profile_change():
            return
        name = simpledialog.askstring(
            "New profile",
            "Profile name:",
            parent=self.winfo_toplevel(),
        )
        if name is None:
            return
        try:
            profile = self.store.create(name)
        except ValueError as exc:
            messagebox.showerror(
                "Could not create profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return
        self._activate(profile, f'Created and activated profile "{profile.name}".')

    def _duplicate_profile(self) -> None:
        if not self._allow_profile_change():
            return
        source = self._selected_profile()
        name = simpledialog.askstring(
            "Duplicate profile",
            "Name for the copy (memory and runs will be copied):",
            initialvalue=f"{source.name} Copy",
            parent=self.winfo_toplevel(),
        )
        if name is None:
            return
        try:
            profile = self.store.create(
                name,
                source_profile_id=source.id,
                include_runs=True,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not duplicate profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return
        self._activate(
            profile,
            f'Duplicated "{source.name}" into active profile "{profile.name}".',
        )

    def _rename_profile(self) -> None:
        if not self._allow_profile_change():
            return
        profile = self._selected_profile()
        name = simpledialog.askstring(
            "Rename profile",
            "New profile name:",
            initialvalue=profile.name,
            parent=self.winfo_toplevel(),
        )
        if name is None:
            return
        try:
            renamed = self.store.rename(profile.id, name)
        except ValueError as exc:
            messagebox.showerror(
                "Could not rename profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return
        self._refresh_profiles(select_active=True)
        self.notice_var.set(f'Profile renamed to "{renamed.name}".')
        self.on_profile_activated(renamed)

    def _delete_profile(self) -> None:
        if not self._allow_profile_change():
            return
        profile = self._selected_profile()
        confirmed = messagebox.askyesno(
            "Delete profile",
            f'Delete "{profile.name}" and all of its memory and runs? This cannot be undone.',
            parent=self.winfo_toplevel(),
        )
        if not confirmed:
            return
        try:
            self.store.delete(profile.id)
            replacement = self.store.active()
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not delete profile",
                str(exc),
                parent=self.winfo_toplevel(),
            )
            return
        self._activate(
            replacement,
            f'Deleted "{profile.name}". Active profile is now "{replacement.name}".',
        )

    def _open_profile_folder(self) -> None:
        path = self.store.profile_directory(self._selected_profile().id)
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(
                "Could not open profile folder",
                str(exc),
                parent=self.winfo_toplevel(),
            )

    def refresh_build_status(self) -> None:
        if self._checking_build:
            return
        self._checking_build = True
        self.branch_var.set("Checking branch and origin…")
        self.check_button.configure(state="disabled")

        def worker() -> None:
            self._build_results.put(
                inspect_build(
                    self.project_root,
                    AGENT_REVISION,
                    fetch_remote=True,
                )
            )

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_build_status)

    def _poll_build_status(self) -> None:
        try:
            status = self._build_results.get_nowait()
        except queue.Empty:
            if self._checking_build:
                self.after(100, self._poll_build_status)
            return
        self._apply_build_status(status)

    def _apply_build_status(self, status: BuildStatus) -> None:
        self._checking_build = False
        self.status = status
        self.branch_var.set(status.label)
        self.revision_var.set(f"Agent revision: {AGENT_REVISION}")
        self.check_button.configure(state="normal")
        if status.detail:
            self.notice_var.set(status.detail)
        self.on_build_status(status)

    def confirm_testing_build(self) -> bool:
        status = self.status
        if status is not None and status.safe_for_testing:
            return True
        reasons: list[str] = []
        if status is None:
            reasons.append(
                "The development branch and remote version have not finished checking."
            )
        else:
            if not status.on_development_branch:
                reasons.append(
                    f'Current branch is "{status.branch or "unknown"}", not "{DEVELOPMENT_BRANCH}".'
                )
            if status.outdated:
                reasons.append(
                    f"This checkout is {status.behind} commit(s) behind origin."
                )
            if not status.remote_checked:
                reasons.append("The latest remote version could not be verified.")
            if status.diverged:
                reasons.append(
                    "The local and remote development histories have diverged."
                )
        return messagebox.askyesno(
            "Unsafe testing build",
            "\n\n".join(reasons)
            + "\n\nStart the AI anyway? The title and build status will remain marked unsafe.",
            icon="warning",
            parent=self.winfo_toplevel(),
        )
