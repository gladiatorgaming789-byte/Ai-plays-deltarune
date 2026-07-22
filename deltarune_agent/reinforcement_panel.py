from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .reinforcement import (
    CUSTOM_PRESET,
    DEFAULT_PRESET,
    PRESETS,
    REINFORCEMENT_MEMORY_FILENAME,
    REINFORCEMENT_SETTINGS_FILENAME,
    REWARD_FIELD_SPECS,
    RewardSettings,
    load_reward_settings,
    save_reward_settings,
)


class ReinforcementSettingsPanel(ttk.Frame):
    """Editable per-profile reward settings for the contextual bandit."""

    def __init__(self, parent, *, project_root: Path) -> None:
        super().__init__(parent, padding=12)
        self.project_root = project_root
        self.enabled_var = tk.BooleanVar(value=True)
        self.preset_var = tk.StringVar(value=DEFAULT_PRESET)
        self.exploration_var = tk.StringVar()
        self.decay_var = tk.StringVar()
        self.trace_length_var = tk.StringVar()
        self.repeat_steps_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self.reward_vars = {
            key: tk.StringVar() for key, _label, _help in REWARD_FIELD_SPECS
        }
        self._loading = False
        self._build()
        self.reload()

    @property
    def settings_path(self) -> Path:
        return self.project_root / "memory" / REINFORCEMENT_SETTINGS_FILENAME

    @property
    def memory_path(self) -> Path:
        return self.project_root / "memory" / REINFORCEMENT_MEMORY_FILENAME

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)

        heading = ttk.Frame(self)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text="Reinforcement learning",
            style="Header.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            heading,
            text=(
                "Rewards affect only actions the AI actually attempts. They do not "
                "promote screen regions or identify a correct character."
            ),
            style="Subtitle.TLabel",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        general = ttk.LabelFrame(self, text="Learning behavior", padding=10)
        general.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for column in range(4):
            general.columnconfigure(column, weight=1 if column in {1, 3} else 0)

        ttk.Checkbutton(
            general,
            text="Enable reinforcement learning",
            variable=self.enabled_var,
            command=self._mark_custom,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(general, text="Preset").grid(row=1, column=0, sticky="w")
        preset = ttk.Combobox(
            general,
            textvariable=self.preset_var,
            values=(*PRESETS.keys(), CUSTOM_PRESET),
            state="readonly",
            width=18,
        )
        preset.grid(row=1, column=1, sticky="ew", padx=(8, 20))
        preset.bind("<<ComboboxSelected>>", self._preset_selected)

        fields = (
            (
                "Exploration constant",
                self.exploration_var,
                "Higher values test uncertain actions more often.",
            ),
            (
                "Eligibility decay",
                self.decay_var,
                "How much delayed reward reaches earlier decisions (0–1).",
            ),
            (
                "Trace length",
                self.trace_length_var,
                "Maximum high-level decisions receiving delayed credit.",
            ),
            (
                "Decision repeat steps",
                self.repeat_steps_var,
                "Minimum steps before the same decision counts as a new attempt.",
            ),
        )
        for index, (label, variable, help_text) in enumerate(fields, start=2):
            column = 0 if index % 2 == 0 else 2
            row = 2 + (index - 2) // 2
            ttk.Label(general, text=label).grid(row=row, column=column, sticky="w")
            entry = ttk.Entry(general, textvariable=variable, width=14)
            entry.grid(row=row, column=column + 1, sticky="ew", padx=(8, 20), pady=3)
            entry.bind("<KeyRelease>", self._mark_custom_event)
            ttk.Label(
                general,
                text=help_text,
                style="Subtitle.TLabel",
                wraplength=310,
                justify="left",
            ).grid(
                row=row + 2,
                column=column,
                columnspan=2,
                sticky="w",
                padx=(0, 20),
                pady=(0, 5),
            )

        rewards = ttk.LabelFrame(self, text="Reward values", padding=10)
        rewards.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        rewards.columnconfigure(1, weight=1)
        rewards.columnconfigure(4, weight=1)
        for index, (key, label, help_text) in enumerate(REWARD_FIELD_SPECS):
            group = index % 2
            row = index // 2
            base_column = group * 3
            ttk.Label(rewards, text=label).grid(
                row=row,
                column=base_column,
                sticky="w",
                padx=(0, 8),
                pady=4,
            )
            entry = ttk.Entry(
                rewards,
                textvariable=self.reward_vars[key],
                width=12,
            )
            entry.grid(
                row=row,
                column=base_column + 1,
                sticky="ew",
                padx=(0, 8),
                pady=4,
            )
            entry.bind("<KeyRelease>", self._mark_custom_event)
            ttk.Label(
                rewards,
                text=help_text,
                style="Subtitle.TLabel",
                wraplength=250,
                justify="left",
            ).grid(
                row=row,
                column=base_column + 2,
                sticky="w",
                padx=(0, 18),
                pady=4,
            )

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(
            actions,
            text="Save settings",
            style="Accent.TButton",
            command=self.save,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Reload",
            command=self.reload,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions,
            text="Reset learned rewards",
            command=self.reset_learning,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            actions,
            textvariable=self.status_var,
            style="Subtitle.TLabel",
        ).pack(side="right")

    def _apply_settings(self, settings: RewardSettings) -> None:
        self._loading = True
        try:
            self.enabled_var.set(settings.enabled)
            self.preset_var.set(settings.detect_preset())
            self.exploration_var.set(f"{settings.exploration_constant:g}")
            self.decay_var.set(f"{settings.eligibility_decay:g}")
            self.trace_length_var.set(str(settings.trace_length))
            self.repeat_steps_var.set(str(settings.decision_repeat_steps))
            for key, _label, _help in REWARD_FIELD_SPECS:
                self.reward_vars[key].set(f"{settings.reward(key):g}")
        finally:
            self._loading = False

    def _preset_selected(self, _event=None) -> None:
        name = self.preset_var.get()
        if name in PRESETS:
            self._apply_settings(RewardSettings.for_preset(name))
            self.status_var.set(f"Loaded {name}; press Save settings to apply.")

    def _mark_custom_event(self, _event=None) -> None:
        self._mark_custom()

    def _mark_custom(self) -> None:
        if not self._loading:
            self.preset_var.set(CUSTOM_PRESET)
            self.status_var.set("Custom values not saved yet.")

    def _settings_from_form(self) -> RewardSettings:
        rewards = {
            key: float(self.reward_vars[key].get().strip())
            for key, _label, _help in REWARD_FIELD_SPECS
        }
        settings = RewardSettings(
            enabled=bool(self.enabled_var.get()),
            preset=self.preset_var.get() or CUSTOM_PRESET,
            exploration_constant=float(self.exploration_var.get().strip()),
            eligibility_decay=float(self.decay_var.get().strip()),
            trace_length=int(self.trace_length_var.get().strip()),
            decision_repeat_steps=int(self.repeat_steps_var.get().strip()),
            rewards=rewards,
        )
        settings.validate()
        return settings

    def reload(self) -> None:
        settings = load_reward_settings(self.settings_path)
        self._apply_settings(settings)
        self.status_var.set(
            f"Loaded {settings.detect_preset()} for the active profile."
        )

    def save(self) -> None:
        try:
            settings = self._settings_from_form()
            save_reward_settings(self.settings_path, settings)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "Could not save reinforcement settings",
                str(exc),
                parent=self,
            )
            return
        self._apply_settings(settings)
        self.status_var.set(
            f"Saved {settings.detect_preset()}; used when the next run starts."
        )

    def reset_learning(self) -> None:
        if not self.memory_path.exists():
            self.status_var.set("No learned reward memory exists for this profile.")
            return
        confirmed = messagebox.askyesno(
            "Reset learned rewards?",
            (
                "This removes only reinforcement scores for the active profile. "
                "Maps, warps, screenshots, interactions, and runs are kept."
            ),
            parent=self,
        )
        if not confirmed:
            return
        try:
            self.memory_path.unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "Could not reset learned rewards",
                str(exc),
                parent=self,
            )
            return
        self.status_var.set("Learned reward scores reset for the active profile.")
