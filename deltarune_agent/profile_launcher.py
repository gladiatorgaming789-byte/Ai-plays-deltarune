from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .build_status import BuildStatus, DEVELOPMENT_BRANCH, inspect_build
from .profiles import Profile, ProfileStore
from .version import AGENT_REVISION


PROFILE_ENV = "DELTARUNE_AGENT_PROFILE_NAME"
BUILD_ENV = "DELTARUNE_AGENT_BUILD_STATUS"
SAFE_LAUNCHER_NAME = "Start Deltarune Agent Safe.cmd"


class ProfileLauncher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.project_root = Path(__file__).resolve().parent.parent
        self.store = ProfileStore()
        self.profiles_by_name: dict[str, Profile] = {}
        self.status: BuildStatus | None = None

        root.title("Deltarune Agent • Safe Launcher")
        root.geometry("760x430")
        root.minsize(680, 390)

        self.profile_var = tk.StringVar()
        self.profile_detail_var = tk.StringVar()
        self.branch_var = tk.StringVar(value="Checking branch and updates…")
        self.revision_var = tk.StringVar(value=f"Agent revision: {AGENT_REVISION}")
        self.notice_var = tk.StringVar()

        self._build_ui()
        self._refresh_profiles(select_active=True)
        self._migrate_and_activate_current()
        self._install_persistent_safe_launcher()
        root.after(100, self.refresh_build_status)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="Deltarune Agent Safe Launcher",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Choose an isolated save profile and verify the testing build before opening the controller.",
            wraplength=700,
        ).pack(anchor="w", pady=(2, 14))

        status_frame = ttk.LabelFrame(outer, text="Build safety", padding=10)
        status_frame.pack(fill="x")
        self.branch_label = ttk.Label(
            status_frame,
            textvariable=self.branch_var,
            font=("Segoe UI", 11, "bold"),
        )
        self.branch_label.pack(anchor="w")
        ttk.Label(status_frame, textvariable=self.revision_var).pack(anchor="w", pady=(3, 0))
        ttk.Button(
            status_frame,
            text="Check for updates",
            command=self.refresh_build_status,
        ).pack(anchor="e", pady=(6, 0))

        profile_frame = ttk.LabelFrame(outer, text="Save profile", padding=10)
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
        ttk.Button(row, text="New", command=self._new_profile).pack(side="left", padx=2)
        ttk.Button(row, text="Duplicate", command=self._duplicate_profile).pack(side="left", padx=2)
        ttk.Button(row, text="Rename", command=self._rename_profile).pack(side="left", padx=2)
        ttk.Button(row, text="Delete", command=self._delete_profile).pack(side="left", padx=2)
        ttk.Button(row, text="Open folder", command=self._open_profile_folder).pack(side="left", padx=2)
        ttk.Label(
            profile_frame,
            textvariable=self.profile_detail_var,
            wraplength=690,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(
            outer,
            textvariable=self.notice_var,
            wraplength=700,
        ).pack(anchor="w", pady=(10, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", side="bottom", pady=(14, 0))
        self.launch_button = ttk.Button(
            buttons,
            text="Open Controller",
            command=self.launch_controller,
        )
        self.launch_button.pack(side="right")
        ttk.Button(buttons, text="Close", command=self.root.destroy).pack(side="right", padx=(0, 8))

    def _refresh_profiles(self, *, select_active: bool = False) -> None:
        profiles = self.store.profiles()
        self.profiles_by_name = {profile.name: profile for profile in profiles}
        self.profile_box["values"] = tuple(self.profiles_by_name)
        selected = self.store.active() if select_active else self.profiles_by_name.get(self.profile_var.get())
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

    def _migrate_and_activate_current(self) -> None:
        try:
            result = self.store.migrate_legacy_data(self.project_root)
            profile = self.store.activate(self.project_root, self.store.active().id)
        except OSError as exc:
            messagebox.showerror("Profile setup failed", str(exc), parent=self.root)
            self.launch_button.configure(state="disabled")
            return
        if result.migrated:
            self.notice_var.set(
                "Moved " + ", ".join(result.migrated) + " into the Default AppData profile. "
                f"A verified backup was saved at {result.backup_directory}."
            )
        else:
            self.notice_var.set(
                f"Active profile: {profile.name}. Branch switching will leave its AppData saves untouched."
            )
        self._refresh_profiles(select_active=True)

    def _profile_selected(self, _event: object = None) -> None:
        try:
            profile = self._selected_profile()
            self.store.activate(self.project_root, profile.id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not switch profile", str(exc), parent=self.root)
            self._refresh_profiles(select_active=True)
            return
        self._update_profile_detail(profile)
        self.notice_var.set(f"Active profile changed to {profile.name}.")

    def _new_profile(self) -> None:
        name = simpledialog.askstring("New profile", "Profile name:", parent=self.root)
        if name is None:
            return
        try:
            profile = self.store.create(name)
            self.store.activate(self.project_root, profile.id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create profile", str(exc), parent=self.root)
            return
        self._refresh_profiles(select_active=True)

    def _duplicate_profile(self) -> None:
        source = self._selected_profile()
        name = simpledialog.askstring(
            "Duplicate profile",
            "Name for the copy (memory and runs will be copied):",
            initialvalue=f"{source.name} Copy",
            parent=self.root,
        )
        if name is None:
            return
        try:
            profile = self.store.create(
                name,
                source_profile_id=source.id,
                include_runs=True,
            )
            self.store.activate(self.project_root, profile.id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not duplicate profile", str(exc), parent=self.root)
            return
        self._refresh_profiles(select_active=True)

    def _rename_profile(self) -> None:
        profile = self._selected_profile()
        name = simpledialog.askstring(
            "Rename profile",
            "New profile name:",
            initialvalue=profile.name,
            parent=self.root,
        )
        if name is None:
            return
        try:
            self.store.rename(profile.id, name)
        except ValueError as exc:
            messagebox.showerror("Could not rename profile", str(exc), parent=self.root)
            return
        self._refresh_profiles(select_active=True)

    def _delete_profile(self) -> None:
        profile = self._selected_profile()
        confirmed = messagebox.askyesno(
            "Delete profile",
            f'Delete "{profile.name}" and all of its memory and runs? This cannot be undone.',
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            self.store.delete(profile.id)
            self.store.activate(self.project_root, self.store.active().id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not delete profile", str(exc), parent=self.root)
            return
        self._refresh_profiles(select_active=True)

    def _open_profile_folder(self) -> None:
        path = self.store.profile_directory(self._selected_profile().id)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def refresh_build_status(self) -> None:
        self.branch_var.set("Checking branch and origin…")
        self.root.update_idletasks()
        self.status = inspect_build(self.project_root, AGENT_REVISION, fetch_remote=True)
        self.branch_var.set(self.status.label)
        self.revision_var.set(f"Agent revision: {AGENT_REVISION}")
        if self.status.detail:
            self.notice_var.set(self.status.detail)

    def _confirm_unsafe_launch(self) -> bool:
        status = self.status
        if status is None:
            return False
        if status.safe_for_testing:
            return True
        reasons: list[str] = []
        if not status.on_development_branch:
            reasons.append(
                f'Current branch is "{status.branch or "unknown"}", not "{DEVELOPMENT_BRANCH}".'
            )
        if status.outdated:
            reasons.append(f"This checkout is {status.behind} commit(s) behind origin.")
        if not status.remote_checked:
            reasons.append("The remote version could not be verified.")
        if status.diverged:
            reasons.append("The local and remote development histories have diverged.")
        return messagebox.askyesno(
            "Unsafe testing build",
            "\n\n".join(reasons)
            + "\n\nOpen the controller anyway? The window title will remain marked unsafe.",
            icon="warning",
            parent=self.root,
        )

    def launch_controller(self) -> None:
        self.refresh_build_status()
        if not self._confirm_unsafe_launch():
            return
        profile = self._selected_profile()
        try:
            self.store.activate(self.project_root, profile.id)
        except OSError as exc:
            messagebox.showerror("Could not activate profile", str(exc), parent=self.root)
            return

        status_label = self.status.label if self.status else "update unverified"
        os.environ[PROFILE_ENV] = profile.name
        os.environ[BUILD_ENV] = status_label
        self.root.withdraw()
        try:
            self._launch_decorated_gui(profile.name, status_label)
        finally:
            self.root.deiconify()
            self.root.lift()
            self.refresh_build_status()

    @staticmethod
    def _launch_decorated_gui(profile_name: str, status_label: str) -> None:
        import tkinter as tk_module
        from .gui import launch_gui

        original_title = tk_module.Tk.title

        def decorated_title(window: tk.Tk, text: str | None = None):
            if text == "Deltarune AI Controller":
                text = (
                    f"Deltarune AI Controller • {profile_name} • "
                    f"{status_label} • {AGENT_REVISION}"
                )
            if text is None:
                return original_title(window)
            return original_title(window, text)

        tk_module.Tk.title = decorated_title  # type: ignore[assignment]
        try:
            launch_gui()
        finally:
            tk_module.Tk.title = original_title  # type: ignore[assignment]

    def _install_persistent_safe_launcher(self) -> None:
        command_path = self.project_root / SAFE_LAUNCHER_NAME
        command = (
            "@echo off\r\n"
            "cd /d \"%~dp0\"\r\n"
            "if exist \".venv\\Scripts\\python.exe\" (\r\n"
            "  \".venv\\Scripts\\python.exe\" -m deltarune_agent gui\r\n"
            ") else (\r\n"
            "  py -m deltarune_agent gui\r\n"
            ")\r\n"
            "if errorlevel 1 pause\r\n"
        )
        try:
            if not command_path.exists():
                command_path.write_text(command, encoding="utf-8")
            exclude = self.project_root / ".git" / "info" / "exclude"
            if exclude.is_file():
                current = exclude.read_text(encoding="utf-8", errors="replace")
                entry = f"/{SAFE_LAUNCHER_NAME}"
                if entry not in current.splitlines():
                    with exclude.open("a", encoding="utf-8") as stream:
                        if current and not current.endswith("\n"):
                            stream.write("\n")
                        stream.write(entry + "\n")
        except OSError:
            pass


def launch_profile_launcher() -> None:
    root = tk.Tk()
    ProfileLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    launch_profile_launcher()
