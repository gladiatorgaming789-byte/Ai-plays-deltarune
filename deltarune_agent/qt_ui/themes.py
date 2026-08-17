"""Theme manifests and background preferences for the Qt console."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Mapping


SUPPORTED_BACKGROUND_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".mp4",
    ".webm",
    ".mkv",
}


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    description: str
    colors: dict[str, str]
    map_colors: dict[str, str]
    gradient: tuple[str, str]
    default_dim: float = 0.40
    default_parallax: bool = True
    default_animation: bool = False
    default_background: str = ""
    attribution: str = "Original application theme"

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BackgroundSettings:
    path: str = ""
    mode: str = "cover"
    dim: float = 0.40
    tint: str = "#08101f"
    parallax: bool = True
    animation: bool = False
    reduce_motion: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> "BackgroundSettings":
        value = value or {}
        mode = str(value.get("mode") or "cover").casefold()
        if mode not in {"cover", "contain", "stretch"}:
            mode = "cover"
        try:
            dim = min(0.95, max(0.0, float(value.get("dim", 0.40))))
        except (TypeError, ValueError):
            dim = 0.40
        path = str(value.get("path") or "")
        return cls(
            path=path,
            mode=mode,
            dim=dim,
            tint=_color(value.get("tint"), "#08101f"),
            parallax=bool(value.get("parallax", True)),
            animation=bool(value.get("animation", False)),
            reduce_motion=bool(value.get("reduce_motion", False)),
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


COMMON_MAP_COLORS = {
    "grid": "#40506b",
    "cell": "#3284d8",
    "cell_repeat": "#d19b35",
    "cell_hot": "#db5e50",
    "path": "#33d69f",
    "wall": "#ff5c75",
    "interactable": "#ffd45a",
    "warp": "#a486ff",
    "warp_progression": "#49da8d",
    "warp_new_area": "#39cfe0",
    "warp_optional": "#5d91ff",
    "warp_return": "#bc8de8",
    "warp_loop": "#ff6674",
    "camera": "#56a0ff",
    "guess_exit": "#f7bd45",
    "guess_character": "#d47bff",
    "guess_object": "#55d8dc",
    "selection": "#ffffff",
    "player": "#45a1ff",
}


def _theme(
    identifier: str,
    name: str,
    description: str,
    *,
    accent: str,
    accent_hover: str,
    base: str,
    panel: str,
    panel_alt: str,
    border: str,
    text: str,
    muted: str,
    success: str,
    warning: str,
    danger: str,
    gradient: tuple[str, str],
    default_background: str = "",
    default_animation: bool = False,
) -> Theme:
    return Theme(
        id=identifier,
        name=name,
        description=description,
        colors={
            "base": base,
            "panel": panel,
            "panel_alt": panel_alt,
            "field": "#090f1b",
            "border": border,
            "text": text,
            "muted": muted,
            "accent": accent,
            "accent_hover": accent_hover,
            "accent_text": "#07101f",
            "success": success,
            "warning": warning,
            "danger": danger,
            "scrim": "rgba(7, 12, 23, 188)",
        },
        map_colors=dict(COMMON_MAP_COLORS),
        gradient=gradient,
        default_background=default_background,
        default_animation=default_animation,
    )


BUILTIN_THEMES: dict[str, Theme] = {
    "castle_town": _theme(
        "castle_town",
        "Castle Town",
        "Deep indigo with warm gold navigation accents.",
        accent="#f2bf59",
        accent_hover="#ffd67b",
        base="#070b19",
        panel="rgba(15, 21, 43, 222)",
        panel_alt="rgba(23, 31, 58, 232)",
        border="#38486f",
        text="#f5f7ff",
        muted="#a9b4d3",
        success="#5ee0a0",
        warning="#f4c65a",
        danger="#ff7085",
        gradient=("#17143c", "#080b1a"),
        default_background="epic_wallpaper.mp4",
    ),
    "cyber_city": _theme(
        "cyber_city",
        "Cyber City",
        "Dark electric blue with cyan and magenta highlights.",
        accent="#4ee6ef",
        accent_hover="#83f7ff",
        base="#050d17",
        panel="rgba(7, 27, 39, 224)",
        panel_alt="rgba(11, 42, 56, 232)",
        border="#1f7180",
        text="#effeff",
        muted="#91c5cc",
        success="#65f6a7",
        warning="#ffe274",
        danger="#ff59a7",
        gradient=("#0a3544", "#07101c"),
    ),
    "hometown_sunset": _theme(
        "hometown_sunset",
        "Hometown Sunset",
        "Warm amber, rose, and plum inspired by a quiet sunset.",
        accent="#ffb96a",
        accent_hover="#ffd099",
        base="#1b101c",
        panel="rgba(48, 25, 45, 222)",
        panel_alt="rgba(67, 34, 54, 232)",
        border="#9a526f",
        text="#fff5ee",
        muted="#dab7c2",
        success="#b6e879",
        warning="#ffd16f",
        danger="#ff7892",
        gradient=("#743d58", "#241428"),
        default_background="second_wallpaper.gif",
    ),
    "operator": _theme(
        "operator",
        "Operator",
        "Artwork-free, low-distraction navy console.",
        accent="#5b9cff",
        accent_hover="#83b5ff",
        base="#090e18",
        panel="rgba(16, 23, 36, 242)",
        panel_alt="rgba(24, 33, 50, 244)",
        border="#37465f",
        text="#f3f7ff",
        muted="#a5b2c7",
        success="#55d691",
        warning="#ecc55e",
        danger="#f06b7e",
        gradient=("#17243b", "#090e18"),
    ),
}


def _color(value: object, default: str) -> str:
    text = str(value or "")
    if text.startswith("#") and len(text) in {4, 7, 9}:
        return text
    if text.startswith("rgba(") and text.endswith(")"):
        return text
    return default


def validate_theme_manifest(payload: Mapping[str, object]) -> list[str]:
    """Return validation errors without throwing on user-authored themes."""

    errors: list[str] = []
    identifier = str(payload.get("id") or "")
    if not identifier or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in identifier):
        errors.append("id must contain only lowercase letters, digits, hyphens, and underscores")
    if not str(payload.get("name") or "").strip():
        errors.append("name is required")
    colors = payload.get("colors")
    required = {
        "base",
        "panel",
        "panel_alt",
        "field",
        "border",
        "text",
        "muted",
        "accent",
        "accent_hover",
        "accent_text",
        "success",
        "warning",
        "danger",
        "scrim",
    }
    if not isinstance(colors, Mapping):
        errors.append("colors must be an object")
    else:
        missing = required - {str(key) for key in colors}
        if missing:
            errors.append(f"colors is missing: {', '.join(sorted(missing))}")
        for key, value in colors.items():
            if _color(value, "") == "":
                errors.append(f"colors.{key} is not a supported color")
    gradient = payload.get("gradient")
    if not isinstance(gradient, (list, tuple)) or len(gradient) != 2:
        errors.append("gradient must contain exactly two colors")
    return errors


def theme_from_manifest(payload: Mapping[str, object]) -> Theme:
    errors = validate_theme_manifest(payload)
    if errors:
        raise ValueError("; ".join(errors))
    colors = {str(key): str(value) for key, value in dict(payload["colors"]).items()}
    map_colors = dict(COMMON_MAP_COLORS)
    supplied_map = payload.get("map_colors")
    if isinstance(supplied_map, Mapping):
        for key, value in supplied_map.items():
            color = _color(value, "")
            if color:
                map_colors[str(key)] = color
    gradient = tuple(str(value) for value in payload["gradient"])
    return Theme(
        id=str(payload["id"]),
        name=str(payload["name"]),
        description=str(payload.get("description") or "Custom theme"),
        colors=colors,
        map_colors=map_colors,
        gradient=(gradient[0], gradient[1]),
        default_dim=min(0.95, max(0.0, float(payload.get("default_dim", 0.40)))),
        default_parallax=bool(payload.get("default_parallax", True)),
        default_animation=bool(payload.get("default_animation", False)),
        default_background=str(payload.get("default_background") or ""),
        attribution=str(payload.get("attribution") or "User-provided theme"),
    )


def load_theme_file(path: Path) -> Theme:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("theme manifest root must be an object")
    return theme_from_manifest(payload)


def discover_themes(directory: Path | None) -> tuple[dict[str, Theme], list[str]]:
    themes = dict(BUILTIN_THEMES)
    warnings: list[str] = []
    if directory is None or not directory.is_dir():
        return themes, warnings
    for path in sorted(directory.glob("*.json")):
        try:
            theme = load_theme_file(path)
            themes[theme.id] = theme
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            warnings.append(f"{path.name}: {exc}")
    return themes, warnings


def supported_background(path: Path) -> bool:
    return path.suffix.casefold() in SUPPORTED_BACKGROUND_SUFFIXES


def stylesheet(theme: Theme) -> str:
    """Generate the application stylesheet from semantic theme tokens."""

    c = theme.colors
    return f"""
    QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
    QMainWindow, QWidget#backdrop {{ background: transparent; }}
    QWidget#chrome {{ background: transparent; }}
    QWidget#sidebar, QFrame#topBar {{ background: {c['panel']}; }}
    QFrame#card, QFrame#inspectorCard, QFrame#pageHeader {{
        background: {c['panel_alt']}; border: 1px solid {c['border']}; border-radius: 10px;
    }}
    QFrame#candidateCard {{
        background: {c['panel_alt']}; border: 1px solid {c['border']}; border-radius: 8px;
    }}
    QFrame#candidateCard[active="true"] {{ border-color: {c['accent']}; }}
    QFrame#candidateCard[leader="true"], QFrame#candidateCard[winner="true"] {{
        background: {c['field']}; border: 2px solid {c['success']};
    }}
    QFrame#candidateCard[provisional="true"] {{ border: 2px solid {c['warning']}; }}
    QFrame#candidateCard[disqualified="true"] {{ border: 1px solid {c['danger']}; }}
    QLabel#candidateScore {{ font-size: 14pt; font-weight: 700; }}
    QLabel#title {{ font-size: 16pt; font-weight: 700; }}
    QLabel#pageTitle {{ font-size: 20pt; font-weight: 700; }}
    QLabel#sectionTitle {{ font-size: 12pt; font-weight: 650; }}
    QLabel#muted, QLabel#meta {{ color: {c['muted']}; }}
    QLabel#success {{ color: {c['success']}; font-weight: 600; }}
    QLabel#warning {{ color: {c['warning']}; font-weight: 600; }}
    QLabel#danger {{ color: {c['danger']}; font-weight: 600; }}
    QPushButton, QToolButton {{
        background: {c['panel_alt']}; border: 1px solid {c['border']}; border-radius: 7px;
        padding: 7px 11px;
    }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; background: {c['panel']}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {c['field']}; }}
    QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 700; }}
    QPushButton#dangerButton {{ color: {c['danger']}; }}
    QPushButton#nav {{ text-align: left; padding: 11px 14px; border: 0; border-radius: 8px; }}
    QPushButton#nav:checked {{ background: {c['accent']}; color: {c['accent_text']}; font-weight: 700; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser,
    QListWidget, QTreeWidget, QTableWidget {{
        background: {c['field']}; border: 1px solid {c['border']}; border-radius: 6px;
        selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
    }}
    QTableWidget {{ alternate-background-color: {c['panel_alt']}; gridline-color: {c['border']}; }}
    QHeaderView::section {{
        background: {c['panel_alt']}; color: {c['text']}; border: 0;
        border-right: 1px solid {c['border']}; border-bottom: 1px solid {c['border']};
        padding: 6px 8px; font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {c['panel_alt']}; border: 1px solid {c['border']}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ padding: 6px; min-height: 20px; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; border-radius: 7px; }}
    QTabBar::tab {{ background: {c['panel_alt']}; padding: 8px 12px; border: 1px solid {c['border']}; }}
    QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; }}
    QMenu {{ background: {c['panel_alt']}; border: 1px solid {c['border']}; padding: 5px; }}
    QMenu::item {{ padding: 7px 24px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_text']}; }}
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 28px; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 28px; }}
    QSplitter::handle {{ background: {c['border']}; width: 1px; height: 1px; }}
    QToolTip {{ background: {c['panel_alt']}; color: {c['text']}; border: 1px solid {c['border']}; }}
    """
