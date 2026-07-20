from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from uuid import uuid4

from .window import (
    find_window,
    focus_window,
    is_window_foreground,
    remember_window,
)
from .world_model import CELL_SIZE


DIRECTION_VECTORS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
EVENT_PREFIX = "AI_GUI_EVENT\t"

THEMES = {
    "dark": {
        "bg": "#10141d",
        "panel": "#181e2a",
        "panel_alt": "#202838",
        "field": "#0c1119",
        "text": "#e7edf7",
        "muted": "#9ba8bd",
        "border": "#354056",
        "accent": "#4c8dff",
        "accent_active": "#6aa2ff",
        "cell": "#293447",
        "cell_repeat": "#3b4a61",
        "cell_hot": "#65465f",
        "cell_outline": "#46546b",
        "grid": "#1d2634",
        "path": "#52d47d",
        "wall": "#ff626b",
        "interactable": "#f0c94b",
        "interactable_outline": "#8c6c10",
        "warp": "#b184f4",
        "warp_outline": "#7147a9",
        "player": "#55a7ff",
        "player_outline": "#d8ecff",
    },
    "light": {
        "bg": "#eef1f6",
        "panel": "#ffffff",
        "panel_alt": "#e6ebf3",
        "field": "#ffffff",
        "text": "#182033",
        "muted": "#5f6b7c",
        "border": "#aeb8c8",
        "accent": "#2868d8",
        "accent_active": "#1d56b8",
        "cell": "#dfe5ed",
        "cell_repeat": "#c9d6e6",
        "cell_hot": "#e4c2cd",
        "cell_outline": "#b9c4d2",
        "grid": "#e5e9ef",
        "path": "#248847",
        "wall": "#d73542",
        "interactable": "#d8aa16",
        "interactable_outline": "#765b06",
        "warp": "#8250c4",
        "warp_outline": "#4d287e",
        "player": "#2377df",
        "player_outline": "#0d3f81",
    },
}


@dataclass
class RoomMap:
    cells: set[tuple[int, int]] = field(default_factory=set)
    visits: dict[tuple[int, int], int] = field(default_factory=dict)
    open_edges: set[tuple[int, int, int, int]] = field(default_factory=set)
    blocked_edges: dict[tuple[int, int, str], int] = field(default_factory=dict)
    interactables: dict[tuple[int, int], dict[str, object]] = field(default_factory=dict)
    warps: dict[tuple[int, int, str, int, int], dict[str, object]] = field(
        default_factory=dict
    )


class WallMapModel:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomMap] = {}
        self.current_room: str | None = None
        self.current_cell: tuple[int, int] | None = None
        self.current_direction: str | None = None

    def room(self, name: str) -> RoomMap:
        return self.rooms.setdefault(name, RoomMap())

    def load_memory(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data.get("version")
            if data.get("cell_size") is not None:
                scale = int(data["cell_size"]) // CELL_SIZE
            elif version == 1:
                scale = 2
            else:
                scale = 1

            def coordinate(value: object) -> int:
                return int(value) * scale

            for item in data.get("cells", []):
                room = self.room(str(item["room"]))
                cell = (coordinate(item["x"]), coordinate(item["y"]))
                room.cells.add(cell)
                room.visits[cell] = int(item.get("visits", 1))
            for item in data.get("open_edges", []):
                room = self.room(str(item["room"]))
                source = (coordinate(item["from_x"]), coordinate(item["from_y"]))
                target = (coordinate(item["to_x"]), coordinate(item["to_y"]))
                self._add_open_path(room, source, str(item["direction"]), target)
            for item in data.get("blocked_edges", []):
                self.room(str(item["room"])).blocked_edges[
                    (
                        coordinate(item["x"]),
                        coordinate(item["y"]),
                        str(item["direction"]),
                    )
                ] = int(item.get("failures", 1))
            for item in data.get("interactables", []):
                self.room(str(item["room"])).interactables[
                    (coordinate(item["x"]), coordinate(item["y"]))
                ] = {
                    "name": str(item.get("name") or "interaction"),
                    "status": "confirmed",
                    "instance_id": item.get("instance_id"),
                    "confirmations": int(item.get("confirmations", 1)),
                    "approaches": list(item.get("approaches", [])),
                }
            for item in data.get("warps", []):
                source_room = str(item["from_room"])
                target_room = str(item["to_room"])
                source = (coordinate(item["from_x"]), coordinate(item["from_y"]))
                target = (coordinate(item["to_x"]), coordinate(item["to_y"]))
                action = str(item.get("action") or "event")
                count = int(item.get("count", 1))
                self._add_warp(source_room, source, target_room, target, action, count)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            # The GUI remains usable with live data if old memory is malformed.
            return

    @staticmethod
    def _add_open_path(
        room: RoomMap,
        source: tuple[int, int],
        direction: str,
        target: tuple[int, int],
    ) -> None:
        vector = DIRECTION_VECTORS.get(direction)
        if vector is None:
            return
        delta_x = target[0] - source[0]
        delta_y = target[1] - source[1]
        forward = delta_x * vector[0] + delta_y * vector[1]
        lateral = abs(delta_x * vector[1] - delta_y * vector[0])
        if forward <= 0 or lateral != 0:
            return
        current = source
        for _ in range(forward):
            following = (current[0] + vector[0], current[1] + vector[1])
            room.cells.update((current, following))
            endpoints = (current, following) if current <= following else (following, current)
            room.open_edges.add((*endpoints[0], *endpoints[1]))
            current = following

    def update(self, event: dict) -> None:
        map_updates = [
            update
            for update in event.get("map_updates") or []
            if isinstance(update, dict)
        ]
        for update in map_updates:
            self._apply_map_update(update)
        telemetry = event.get("telemetry")
        if not telemetry:
            return
        raw_room_name = telemetry.get("room_name")
        if not raw_room_name or str(raw_room_name).casefold() == "unknown":
            return
        room_name = str(raw_room_name)
        x = telemetry.get("x")
        y = telemetry.get("y")
        if telemetry.get("mode") != "overworld":
            x = telemetry.get("player_x")
            y = telemetry.get("player_y")
        if x is None or y is None:
            return

        cell = (int(float(x) // CELL_SIZE), int(float(y) // CELL_SIZE))
        room = self.room(room_name)
        room.cells.add(cell)
        room.visits[cell] = room.visits.get(cell, 0) + 1

        self.current_room = room_name
        self.current_cell = cell
        direction = telemetry.get("facing_direction")
        self.current_direction = (
            str(direction) if direction in DIRECTION_VECTORS else None
        )

    def _apply_map_update(self, update: dict) -> None:
        update_type = str(update.get("type") or "")
        if update_type == "warp":
            self._add_warp(
                str(update["from_room"]),
                tuple(int(value) for value in update["from_cell"]),
                str(update["to_room"]),
                tuple(int(value) for value in update["to_cell"]),
                str(update.get("action") or "event"),
                int(update.get("count", 1)),
            )
            return
        room_name = str(update.get("room") or "")
        if not room_name:
            return
        room = self.room(room_name)
        if update_type == "open_edge":
            source = tuple(int(value) for value in update["from_cell"])
            target = tuple(int(value) for value in update["to_cell"])
            room.cells.update((source, target))
            endpoints = (source, target) if source <= target else (target, source)
            room.open_edges.add((*endpoints[0], *endpoints[1]))
        elif update_type == "blocked":
            cell = tuple(int(value) for value in update["cell"])
            room.blocked_edges[(*cell, str(update["direction"]))] = int(
                update.get("failures", 1)
            )
        elif update_type == "unblocked":
            cell = tuple(int(value) for value in update["cell"])
            room.blocked_edges.pop((*cell, str(update["direction"])), None)
        elif update_type == "interactable":
            cell = tuple(int(value) for value in update["cell"])
            room.cells.add(cell)
            room.interactables[cell] = {
                "name": str(update.get("name") or "interaction"),
                "status": "confirmed",
                "instance_id": update.get("instance_id"),
                "confirmations": int(update.get("confirmations", 1)),
                "approaches": list(update.get("approaches", [])),
            }

    def _add_warp(
        self,
        source_room: str,
        source: tuple[int, int],
        target_room: str,
        target: tuple[int, int],
        action: str,
        count: int,
    ) -> None:
        source_map = self.room(source_room)
        target_map = self.room(target_room)
        source_map.cells.add(source)
        target_map.cells.add(target)
        source_map.warps[(*source, target_room, *target)] = {
            "action": action,
            "count": count,
            "kind": "exit",
        }
        target_map.warps[(*target, source_room, *source)] = {
            "action": action,
            "count": count,
            "kind": "entry",
        }


class AgentGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.project_root = Path(__file__).resolve().parent.parent
        self.window_memory = self.project_root / "memory" / "window_titles.json"
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.stop_file: Path | None = None
        self.closing = False
        self.close_deadline = 0.0
        self.map_model = WallMapModel()
        self.map_model.load_memory(self.project_root / "memory" / "navigation.json")
        self.style = ttk.Style(root)
        self.colors = THEMES["dark"]
        self.output_widgets: list[tk.Text] = []
        self.legend_swatches: dict[str, tk.Label] = {}

        root.title("Deltarune AI Controller")
        root.geometry("1280x820")
        root.minsize(920, 620)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.live_var = tk.BooleanVar(value=False)
        self.steps_var = tk.StringVar(value="2000")
        self.window_var = tk.StringVar(value="deltarune")
        self.follow_room_var = tk.BooleanVar(value=True)
        self.dark_mode_var = tk.BooleanVar(value=True)
        self.room_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Stopped")
        self.map_detail_var = tk.StringVar(value="No map data yet.")
        self._map_transform: tuple[int, int, float, float, float] | None = None

        self._build_controls()
        self._build_main_area()
        self._apply_theme()
        self._refresh_room_choices(select_current=False)
        self._redraw_map()
        root.after(50, self._poll_events)

    def _build_controls(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(fill="x")
        title_group = ttk.Frame(header)
        title_group.pack(side="left")
        ttk.Label(
            title_group,
            text="Deltarune AI Controller",
            style="Header.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text="Learned navigation, telemetry, and room-map viewer",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).pack(side="right", padx=(12, 0))

        controls = ttk.LabelFrame(self.root, text="Run controls", padding=(10, 7))
        controls.pack(fill="x", padx=10)

        ttk.Checkbutton(controls, text="Live input", variable=self.live_var).pack(
            side="left", padx=(0, 8)
        )
        ttk.Label(controls, text="Steps:").pack(side="left")
        ttk.Entry(controls, textvariable=self.steps_var, width=8).pack(
            side="left", padx=(3, 8)
        )
        ttk.Label(controls, text="Game window:").pack(side="left")
        ttk.Entry(controls, textvariable=self.window_var, width=22).pack(
            side="left", padx=(3, 8)
        )
        self.start_button = ttk.Button(
            controls,
            text="Start AI",
            command=self.start,
            style="Accent.TButton",
        )
        self.start_button.pack(side="left", padx=3)
        self.stop_button = ttk.Button(
            controls, text="Stop AI", command=self.stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=3)
        ttk.Button(controls, text="Clear output", command=self._clear_output).pack(
            side="left", padx=3
        )
        ttk.Checkbutton(
            controls,
            text="Dark mode",
            variable=self.dark_mode_var,
            command=self._apply_theme,
        ).pack(side="right", padx=(8, 2))

    def _build_main_area(self) -> None:
        panes = ttk.Panedwindow(self.root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=10)

        map_frame = ttk.LabelFrame(
            panes,
            text="Learned room map",
            padding=8,
        )
        map_tools = ttk.Frame(map_frame)
        map_tools.pack(fill="x", pady=(0, 4))
        ttk.Label(map_tools, text="Room:").pack(side="left")
        self.room_box = ttk.Combobox(
            map_tools,
            textvariable=self.room_var,
            state="readonly",
            width=28,
        )
        self.room_box.pack(side="left", padx=4)
        self.room_box.bind("<<ComboboxSelected>>", lambda _event: self._redraw_map())
        ttk.Checkbutton(
            map_tools,
            text="Follow current room",
            variable=self.follow_room_var,
        ).pack(side="left", padx=4)
        ttk.Button(
            map_tools,
            text="Clear learned map",
            command=self._clear_wall_map,
        ).pack(side="right", padx=4)
        self.map_canvas = tk.Canvas(
            map_frame,
            highlightthickness=1,
        )
        self.map_canvas.pack(fill="both", expand=True)
        self.map_canvas.bind("<Configure>", lambda _event: self._redraw_map())
        self.map_canvas.bind("<Button-1>", self._inspect_map_cell)
        self._build_map_legend(map_frame)
        ttk.Label(
            map_frame,
            textvariable=self.map_detail_var,
            wraplength=520,
        ).pack(fill="x", pady=(4, 0))
        panes.add(map_frame, weight=1)

        output_panes = ttk.Panedwindow(panes, orient="vertical")
        ai_frame = ttk.LabelFrame(output_panes, text="AI output", padding=3)
        telemetry_frame = ttk.LabelFrame(
            output_panes, text="Telemetry output", padding=3
        )
        self.ai_output = self._text_with_scrollbar(ai_frame)
        self.telemetry_output = self._text_with_scrollbar(telemetry_frame)
        output_panes.add(ai_frame, weight=1)
        output_panes.add(telemetry_frame, weight=1)
        panes.add(output_panes, weight=1)

    def _build_map_legend(self, parent: ttk.Frame) -> None:
        legend = ttk.LabelFrame(parent, text="Map legend", padding=5)
        legend.pack(fill="x", pady=(5, 0))
        items = [
            ("Visited (brighter = repeated)", "cell"),
            ("Observed path", "path"),
            ("Blocked edge", "wall"),
            ("Discovered interactable", "interactable"),
            ("Discovered room warp", "warp"),
            ("Kris", "player"),
        ]
        for index, (label, color) in enumerate(items):
            item = ttk.Frame(legend)
            item.grid(
                row=index // 4,
                column=index % 4,
                sticky="w",
                padx=(0, 14),
                pady=2,
            )
            swatch = tk.Label(item, width=2, relief="solid", borderwidth=1)
            swatch.pack(side="left", padx=(0, 4))
            self.legend_swatches[color] = swatch
            ttk.Label(item, text=label).pack(side="left")

    def _text_with_scrollbar(self, parent: ttk.Frame) -> tk.Text:
        scrollbar = ttk.Scrollbar(parent, orient="vertical")
        text = tk.Text(
            parent,
            wrap="none",
            font=("Consolas", 9),
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=text.yview)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self.output_widgets.append(text)
        return text

    def _apply_theme(self) -> None:
        mode = "dark" if self.dark_mode_var.get() else "light"
        self.colors = THEMES[mode]
        colors = self.colors
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.configure(background=colors["bg"])
        self.root.option_add("*TCombobox*Listbox.background", colors["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", colors["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure(
            "TLabel",
            background=colors["bg"],
            foreground=colors["text"],
        )
        self.style.configure(
            "Header.TLabel",
            background=colors["bg"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 16),
        )
        self.style.configure(
            "Subtitle.TLabel",
            background=colors["bg"],
            foreground=colors["muted"],
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background=colors["panel_alt"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 9),
            padding=(10, 5),
        )
        self.style.configure(
            "TLabelframe",
            background=colors["panel"],
            bordercolor=colors["border"],
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "TLabelframe.Label",
            background=colors["panel"],
            foreground=colors["text"],
            font=("Segoe UI Semibold", 9),
        )
        self.style.configure(
            "TCheckbutton",
            background=colors["panel"],
            foreground=colors["text"],
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", colors["panel"])],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "TButton",
            background=colors["panel_alt"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            padding=(9, 5),
        )
        self.style.map(
            "TButton",
            background=[
                ("active", colors["border"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[("disabled", colors["muted"])],
        )
        self.style.configure(
            "Accent.TButton",
            background=colors["accent"],
            foreground="#ffffff",
            bordercolor=colors["accent"],
            font=("Segoe UI Semibold", 9),
        )
        self.style.map(
            "Accent.TButton",
            background=[
                ("active", colors["accent_active"]),
                ("disabled", colors["border"]),
            ],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=colors["field"],
            foreground=colors["text"],
            bordercolor=colors["border"],
            insertcolor=colors["text"],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["field"],
            background=colors["panel_alt"],
            foreground=colors["text"],
            arrowcolor=colors["text"],
            bordercolor=colors["border"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["field"])],
            foreground=[("readonly", colors["text"])],
            selectbackground=[("readonly", colors["field"])],
            selectforeground=[("readonly", colors["text"])],
        )
        self.style.configure(
            "TScrollbar",
            background=colors["panel_alt"],
            troughcolor=colors["field"],
            bordercolor=colors["border"],
            arrowcolor=colors["text"],
        )
        self.style.configure("TPanedwindow", background=colors["bg"])

        if hasattr(self, "map_canvas"):
            self.map_canvas.configure(
                background=colors["field"],
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
            )
        for widget in self.output_widgets:
            widget.configure(
                background=colors["field"],
                foreground=colors["text"],
                insertbackground=colors["text"],
                selectbackground=colors["accent"],
                selectforeground="#ffffff",
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
                relief="flat",
                padx=7,
                pady=5,
            )
        for color_key, swatch in self.legend_swatches.items():
            swatch.configure(
                background=colors[color_key],
                highlightbackground=colors["border"],
            )
        if hasattr(self, "map_canvas"):
            self._redraw_map()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        try:
            steps = int(self.steps_var.get())
            if steps < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid steps", "Steps must be a positive whole number.")
            return

        game_window = self.window_var.get().strip() or "deltarune"
        detected_window = None
        try:
            if self.live_var.get():
                # The GUI owns foreground permission because the user clicked it.
                # Hand focus to Deltarune before the child controller starts.
                detected_window = focus_window(game_window, self.window_memory)
                if not is_window_foreground(detected_window):
                    raise RuntimeError(
                        "Windows did not allow the GUI to focus Deltarune. "
                        "Click Deltarune once, then press Start AI again."
                    )
            else:
                detected_window = find_window(game_window, self.window_memory)
        except RuntimeError as exc:
            messagebox.showerror("Could not focus Deltarune", str(exc))
            return
        if detected_window is not None:
            remember_window(self.window_memory, detected_window)
            self.window_var.set(detected_window.title)
            game_window = detected_window.executable or detected_window.title
            self._append(
                self.ai_output,
                f"Detected window: {detected_window.title} "
                f"({detected_window.executable or 'unknown executable'})\n",
            )
        stop_directory = self.project_root / "memory"
        stop_directory.mkdir(parents=True, exist_ok=True)
        self.stop_file = stop_directory / f"gui-stop-{uuid4().hex}.flag"
        self.stop_file.unlink(missing_ok=True)
        command = [
            sys.executable,
            "-u",
            "-m",
            "deltarune_agent",
            "run",
            "--steps",
            str(steps),
            "--game-window",
            game_window,
            "--event-stream",
            "--stop-file",
            str(self.stop_file),
            "--window-memory",
            str(self.window_memory),
        ]
        if self.live_var.get():
            command.append("--live")

        self._append(self.ai_output, "\n--- Starting AI ---\n")
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.process = None
            self.stop_file = None
            messagebox.showerror("Could not start AI", str(exc))
            return

        self.status_var.set("Running (LIVE)" if self.live_var.get() else "Running (dry)")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(
            target=self._read_process,
            args=(self.process,),
            daemon=True,
        ).start()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        if self.stop_file is not None:
            self.stop_file.write_text("stop\n", encoding="utf-8")
        self.status_var.set("Stopping safely...")
        self.stop_button.configure(state="disabled")

    def _read_process(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                if line.startswith(EVENT_PREFIX):
                    try:
                        self.events.put(("event", json.loads(line[len(EVENT_PREFIX) :])))
                    except json.JSONDecodeError:
                        self.events.put(("ai", f"Malformed GUI event: {line}"))
                else:
                    self.events.put(("ai", line))
        return_code = process.wait()
        self.events.put(("exit", return_code))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "ai":
                    self._append(self.ai_output, str(payload) + "\n")
                elif kind == "event":
                    self._handle_event(payload)
                elif kind == "exit":
                    self._handle_exit(int(payload))
        except queue.Empty:
            pass
        if self.closing:
            self._finish_close_if_ready()
        else:
            self.root.after(50, self._poll_events)

    def _handle_event(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        telemetry = payload.get("telemetry")
        if telemetry:
            nearby = ""
            if telemetry.get("nearest_interactable_name"):
                nearby = (
                    f" near={telemetry['nearest_interactable_name']}"
                    f"@{telemetry.get('nearest_interactable_distance')}"
                )
            display_room = (
                telemetry.get("room_name")
                or self.map_model.current_room
                or telemetry.get("room_id")
                or "transition"
            )
            line = (
                f"{payload.get('step', 0):04d} "
                f"v{telemetry.get('version')} {telemetry.get('mode')} "
                f"room={display_room} "
                f"pos=({telemetry.get('x')},{telemetry.get('y')}) "
                f"sprite={telemetry.get('sprite_name') or '-'} "
                f"dir={telemetry.get('facing_direction') or '-'}{nearby}\n"
            )
            self._append(self.telemetry_output, line)
        self.map_model.update(payload)
        self._refresh_room_choices(select_current=self.follow_room_var.get())
        self._redraw_map()

    def _handle_exit(self, return_code: int) -> None:
        self._append(self.ai_output, f"--- AI exited with code {return_code} ---\n")
        self.status_var.set("Stopped" if return_code == 0 else f"Stopped (error {return_code})")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.process = None
        if self.stop_file is not None:
            self.stop_file.unlink(missing_ok=True)
            self.stop_file = None

    def _refresh_room_choices(self, select_current: bool) -> None:
        rooms = sorted(self.map_model.rooms)
        self.room_box.configure(values=rooms)
        if select_current and self.map_model.current_room in rooms:
            self.room_var.set(self.map_model.current_room or "")
        elif self.room_var.get() not in rooms:
            preferred = "room_krisroom" if "room_krisroom" in rooms else (rooms[0] if rooms else "")
            self.room_var.set(preferred)

    def _redraw_map(self) -> None:
        canvas = self.map_canvas
        canvas.delete("all")
        room_name = self.room_var.get()
        room = self.map_model.rooms.get(room_name)
        if room is None or not room.cells:
            canvas.create_text(
                18,
                18,
                anchor="nw",
                text="No learned map data yet.",
                fill=self.colors["muted"],
                font=("Segoe UI", 10),
            )
            self.map_detail_var.set("No map data yet.")
            self._map_transform = None
            return

        points = set(room.cells)
        for source_x, source_y, target_x, target_y in room.open_edges:
            points.update(((source_x, source_y), (target_x, target_y)))
        for x, y, _direction in room.blocked_edges:
            points.add((x, y))
        points.update(room.interactables)
        for x, y, _target_room, _target_x, _target_y in room.warps:
            points.add((x, y))
        min_x = min(x for x, _y in points)
        max_x = max(x for x, _y in points)
        min_y = min(y for _x, y in points)
        max_y = max(y for _x, y in points)
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 300)
        offset_x = 32.0
        offset_y = 44.0
        scale = min(
            30.0,
            (width - offset_x * 2) / max(1, max_x - min_x + 2),
            (height - offset_y - 24) / max(1, max_y - min_y + 2),
        )
        scale = max(5.0, scale)
        self._map_transform = (min_x, min_y, scale, offset_x, offset_y)

        def center(cell: tuple[int, int]) -> tuple[float, float]:
            return (
                offset_x + (cell[0] - min_x + 0.5) * scale,
                offset_y + (cell[1] - min_y + 0.5) * scale,
            )

        canvas.create_text(
            16,
            13,
            anchor="nw",
            text=room_name,
            fill=self.colors["text"],
            font=("Segoe UI Semibold", 11),
        )
        canvas.create_text(
            width - 16,
            15,
            anchor="ne",
            text=f"8 px grid  •  {len(room.cells)} cells",
            fill=self.colors["muted"],
            font=("Segoe UI", 8),
        )

        left = offset_x
        top = offset_y
        right = offset_x + (max_x - min_x + 1) * scale
        bottom = offset_y + (max_y - min_y + 1) * scale
        for grid_x in range(min_x, max_x + 2):
            x = offset_x + (grid_x - min_x) * scale
            canvas.create_line(x, top, x, bottom, fill=self.colors["grid"])
        for grid_y in range(min_y, max_y + 2):
            y = offset_y + (grid_y - min_y) * scale
            canvas.create_line(left, y, right, y, fill=self.colors["grid"])

        for x, y in sorted(room.cells):
            cx, cy = center((x, y))
            half = scale * 0.42
            visits = room.visits.get((x, y), 1)
            cell_color = (
                self.colors["cell_hot"]
                if visits >= 20
                else self.colors["cell_repeat"] if visits >= 5 else self.colors["cell"]
            )
            canvas.create_rectangle(
                cx - half,
                cy - half,
                cx + half,
                cy + half,
                fill=cell_color,
                outline=self.colors["cell_outline"],
            )
        for source_x, source_y, target_x, target_y in sorted(room.open_edges):
            canvas.create_line(
                *center((source_x, source_y)),
                *center((target_x, target_y)),
                fill=self.colors["path"],
                width=max(2, round(scale * 0.16)),
            )
        for (x, y, direction), failures in sorted(room.blocked_edges.items()):
            cx, cy = center((x, y))
            half = scale * 0.48
            if direction == "up":
                coords = (cx - half, cy - half, cx + half, cy - half)
            elif direction == "down":
                coords = (cx - half, cy + half, cx + half, cy + half)
            elif direction == "left":
                coords = (cx - half, cy - half, cx - half, cy + half)
            else:
                coords = (cx + half, cy - half, cx + half, cy + half)
            canvas.create_line(
                *coords,
                fill=self.colors["wall"],
                width=min(8, 3 + max(1, failures)),
            )
        for (x, y), record in sorted(room.interactables.items()):
            cx, cy = center((x, y))
            radius = max(4.0, scale * 0.27)
            canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                fill=self.colors["interactable"],
                outline=self.colors["interactable_outline"],
                width=2,
            )
            canvas.create_text(
                cx,
                cy,
                text="I",
                fill="#17130a",
                font=("TkDefaultFont", max(7, round(scale * 0.32)), "bold"),
            )
        for (x, y, target_room, _target_x, _target_y), record in sorted(
            room.warps.items()
        ):
            cx, cy = center((x, y))
            radius = max(5.0, scale * 0.32)
            canvas.create_polygon(
                cx,
                cy - radius,
                cx + radius,
                cy,
                cx,
                cy + radius,
                cx - radius,
                cy,
                fill=self.colors["warp"],
                outline=self.colors["warp_outline"],
            )
            short_target = target_room.removeprefix("room_")
            canvas.create_text(
                cx + radius + 2,
                cy - radius,
                anchor="sw",
                text=f"→ {short_target}",
                fill=self.colors["warp"],
                font=("Segoe UI Semibold", 8),
            )
        if (
            self.map_model.current_room == room_name
            and self.map_model.current_cell is not None
        ):
            cx, cy = center(self.map_model.current_cell)
            radius = max(3.0, scale * 0.22)
            canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                fill=self.colors["player"],
                outline=self.colors["player_outline"],
                width=2,
            )
            direction = self.map_model.current_direction
            if direction in DIRECTION_VECTORS:
                dx, dy = DIRECTION_VECTORS[direction]
                canvas.create_line(
                    cx,
                    cy,
                    cx + dx * radius * 1.8,
                    cy + dy * radius * 1.8,
                    fill=self.colors["player_outline"],
                    width=2,
                    arrow="last",
                    arrowshape=(6, 7, 3),
                )
        self.map_detail_var.set(
            f"{room_name}: {len(room.cells)} visited cells, "
            f"{len(room.open_edges)} paths, {len(room.blocked_edges)} wall edges, "
            f"{len(room.interactables)} discovered interactables, "
            f"{len(room.warps)} warp endpoints. Click a map cell for details."
        )

    def _inspect_map_cell(self, event: tk.Event) -> None:
        room_name = self.room_var.get()
        room = self.map_model.rooms.get(room_name)
        if room is None or self._map_transform is None:
            return
        min_x, min_y, scale, offset_x, offset_y = self._map_transform
        cell = (
            round((event.x - offset_x) / scale + min_x - 0.5),
            round((event.y - offset_y) / scale + min_y - 0.5),
        )
        visits = room.visits.get(cell, 0)
        details = [f"{room_name} cell {cell}", f"visits={visits}"]
        interactable = room.interactables.get(cell)
        if interactable:
            approach_directions = sorted(
                {
                    str(approach.get("direction"))
                    for approach in interactable.get("approaches", [])
                    if isinstance(approach, dict) and approach.get("direction")
                }
            )
            approaches = (
                ", approached " + "/".join(approach_directions)
                if approach_directions
                else ""
            )
            details.append(
                f"interactable={interactable.get('name')} "
                f"(confirmed x{interactable.get('confirmations', 1)}{approaches})"
            )
        walls = [
            f"{direction}×{failures}"
            for (x, y, direction), failures in room.blocked_edges.items()
            if (x, y) == cell
        ]
        if walls:
            details.append("walls=" + ", ".join(sorted(walls)))
        warps = [
            f"{record.get('kind')} to {target_room} {target_cell} "
            f"via {record.get('action')} ×{record.get('count')}"
            for (x, y, target_room, target_x, target_y), record in room.warps.items()
            if (x, y) == cell
            for target_cell in [(target_x, target_y)]
        ]
        details.extend(warps)
        if visits == 0 and cell not in room.cells:
            details.append("unmapped")
        self.map_detail_var.set(" | ".join(details))

    @staticmethod
    def _append(widget: tk.Text, text: str) -> None:
        widget.insert("end", text)
        line_count = int(widget.index("end-1c").split(".")[0])
        if line_count > 2000:
            widget.delete("1.0", f"{line_count - 1800}.0")
        widget.see("end")

    def _clear_output(self) -> None:
        self.ai_output.delete("1.0", "end")
        self.telemetry_output.delete("1.0", "end")

    def _clear_wall_map(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showinfo(
                "Stop AI first",
                "Stop the AI before clearing its persistent learned map.",
            )
            return
        if not messagebox.askyesno(
            "Clear learned map",
            "Delete all learned cells, paths, walls, interactions, and room warps?",
        ):
            return
        memory_path = self.project_root / "memory" / "navigation.json"
        memory_path.unlink(missing_ok=True)
        memory_path.with_suffix(memory_path.suffix + ".tmp").unlink(missing_ok=True)
        self.map_model = WallMapModel()
        self.room_var.set("")
        self._refresh_room_choices(select_current=False)
        self._redraw_map()
        self._append(
            self.ai_output,
            "Learned map cleared. The next run will start clean.\n",
        )

    def _on_close(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.root.destroy()
            return
        self.closing = True
        self.close_deadline = time.monotonic() + 3.0
        self.stop()

    def _finish_close_if_ready(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.root.destroy()
            return
        if time.monotonic() >= self.close_deadline:
            self.process.terminate()
            self.root.destroy()
            return
        self.root.after(50, self._poll_events)


def launch_gui() -> None:
    root = tk.Tk()
    AgentGUI(root)
    root.mainloop()
