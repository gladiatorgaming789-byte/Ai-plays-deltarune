"""A scene-graph room map with one coordinate system for every overlay."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from ..gui import DIRECTION_VECTORS, WallMapModel, warp_role_badge
from ..map_guesses import VisualGuessEntry, visual_guess_entries
from ..world_model import CELL_SIZE, EXPLORATION_REGION_CELLS


CELL_PIXELS = 18.0


def _scene_point_from_cell(cell: tuple[float, float]) -> QPointF:
    return QPointF(cell[0] * CELL_PIXELS, cell[1] * CELL_PIXELS)


def _scene_point_from_world(point: tuple[float, float]) -> QPointF:
    return _scene_point_from_cell((point[0] / CELL_SIZE, point[1] / CELL_SIZE))


def _scene_world_rect(box: tuple[float, float, float, float]) -> QRectF:
    first = _scene_point_from_world((box[0], box[1]))
    second = _scene_point_from_world((box[2], box[3]))
    return QRectF(first, second).normalized()


def _scene_cell_rect(x: float, y: float, width: float = 1, height: float = 1) -> QRectF:
    return QRectF(x * CELL_PIXELS, y * CELL_PIXELS, width * CELL_PIXELS, height * CELL_PIXELS)


def _pen(color: str, width: float = 1.0, *, dashed: bool = False) -> QPen:
    value = QPen(QColor(color), width)
    value.setCosmetic(True)
    if dashed:
        value.setStyle(Qt.PenStyle.DashLine)
    return value


class RoomMapScene(QGraphicsScene):
    """Materialize remembered evidence as selectable scene items."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.guess_items: dict[str, list[QGraphicsItem]] = {}
        self._room_name = ""
        self._palette: Mapping[str, str] = {}
        self._pixmap_cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()

    @staticmethod
    def _metadata(item: QGraphicsItem, payload: dict[str, object]) -> None:
        item.setData(0, payload)
        item.setToolTip(str(payload.get("tooltip") or payload.get("label") or ""))
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def rebuild(
        self,
        model: WallMapModel,
        room_name: str,
        *,
        layers: Mapping[str, bool],
        palette: Mapping[str, str],
    ) -> list[VisualGuessEntry]:
        self.clear()
        self.guess_items.clear()
        self._room_name = room_name
        self._palette = palette
        room = model.rooms.get(room_name)
        if room is None:
            self.setSceneRect(QRectF(-160, -100, 320, 200))
            return []

        points = set(room.cells)
        for source_x, source_y, target_x, target_y in room.open_edges:
            points.update(((source_x, source_y), (target_x, target_y)))
        points.update((x, y) for x, y, _direction in room.blocked_edges)
        points.update(room.interactables)
        points.update((x, y) for x, y, _target in room.warps)
        for region in set(room.screen_regions) | set(room.view_tiles):
            x = region[0] * EXPLORATION_REGION_CELLS
            y = region[1] * EXPLORATION_REGION_CELLS
            points.update(
                {
                    (x, y),
                    (
                        x + EXPLORATION_REGION_CELLS - 1,
                        y + EXPLORATION_REGION_CELLS - 1,
                    ),
                }
            )
        if model.current_camera is not None and model.current_camera[0] == room_name:
            _name, x, y, width, height = model.current_camera
            points.update(
                {
                    (int(x // CELL_SIZE), int(y // CELL_SIZE)),
                    (int((x + width) // CELL_SIZE), int((y + height) // CELL_SIZE)),
                }
            )
        if not points:
            points.add((0, 0))
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        room_rect = _scene_cell_rect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
        room_rect.adjust(-CELL_PIXELS * 2, -CELL_PIXELS * 2, CELL_PIXELS * 2, CELL_PIXELS * 2)
        self.setSceneRect(room_rect)
        background = self.addRect(room_rect, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#02050a")))
        background.setZValue(-100)

        if layers.get("scene", True):
            self._draw_tiles(room)
        if layers.get("grid", False):
            self._draw_grid(min_x, min_y, max_x, max_y)
        if layers.get("visits", False) or not room.view_tiles:
            self._draw_visits(room)
        if layers.get("navigation", True):
            self._draw_navigation(room)

        guesses = visual_guess_entries(room_name, room, model.current_visible_regions)
        if layers.get("guesses", True):
            self._draw_guesses(guesses, model.current_guess_id)
        if layers.get("objects", True):
            self._draw_interactables(room)
            self._draw_warps(room)
        if layers.get("camera", True):
            self._draw_camera(model, room_name)
        self._draw_player(model, room_name)
        return guesses

    def _draw_tiles(self, room) -> None:
        region_scene_size = EXPLORATION_REGION_CELLS * CELL_PIXELS
        for (region_x, region_y), record in sorted(room.view_tiles.items()):
            try:
                path = Path(str(record["path"]))
                mtime = int(record.get("mtime_ns") or path.stat().st_mtime_ns)
                key = (str(path), mtime)
                pixmap = self._pixmap_cache.get(key)
                if pixmap is None:
                    # Discard obsolete revisions of the same tile path.
                    for old_key in [cached for cached in self._pixmap_cache if cached[0] == str(path)]:
                        self._pixmap_cache.pop(old_key, None)
                    pixmap = QPixmap(str(path))
                    self._pixmap_cache[key] = pixmap
                    if len(self._pixmap_cache) > 256:
                        self._pixmap_cache.popitem(last=False)
                else:
                    self._pixmap_cache.move_to_end(key)
                if pixmap.isNull():
                    continue
                item = QGraphicsPixmapItem(pixmap)
                item.setTransformationMode(Qt.TransformationMode.FastTransformation)
                item.setTransform(
                    QTransform.fromScale(
                        region_scene_size / pixmap.width(),
                        region_scene_size / pixmap.height(),
                    )
                )
                item.setPos(
                    region_x * region_scene_size,
                    region_y * region_scene_size,
                )
                item.setZValue(-90)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                self.addItem(item)
            # A remembered tile can be replaced or removed while the UI is
            # coalescing live telemetry updates.  Skip that one frame instead
            # of letting a harmless file-system race take down the map view.
            except (KeyError, OSError, TypeError, ValueError):
                continue

    def _draw_grid(self, min_x: int, min_y: int, max_x: int, max_y: int) -> None:
        pen = _pen(self._palette.get("grid", "#40506b"), 1)
        for x in range(min_x, max_x + 2):
            item = self.addLine(
                x * CELL_PIXELS,
                min_y * CELL_PIXELS,
                x * CELL_PIXELS,
                (max_y + 1) * CELL_PIXELS,
                pen,
            )
            item.setZValue(-10)
        for y in range(min_y, max_y + 2):
            item = self.addLine(
                min_x * CELL_PIXELS,
                y * CELL_PIXELS,
                (max_x + 1) * CELL_PIXELS,
                y * CELL_PIXELS,
                pen,
            )
            item.setZValue(-10)

    def _draw_visits(self, room) -> None:
        for (x, y), visits in room.visits.items():
            key = "cell_hot" if visits >= 20 else "cell_repeat" if visits >= 5 else "cell"
            color = QColor(self._palette.get(key, "#3284d8"))
            color.setAlpha(120)
            rect = _scene_cell_rect(x + 0.30, y + 0.30, 0.40, 0.40)
            item = self.addEllipse(rect, QPen(Qt.PenStyle.NoPen), QBrush(color))
            item.setZValue(-20)

    def _draw_navigation(self, room) -> None:
        path_pen = _pen(self._palette.get("path", "#33d69f"), 2.4)
        for source_x, source_y, target_x, target_y in room.open_edges:
            source = _scene_point_from_cell((source_x + 0.5, source_y + 0.5))
            target = _scene_point_from_cell((target_x + 0.5, target_y + 0.5))
            item = self.addLine(source.x(), source.y(), target.x(), target.y(), path_pen)
            item.setZValue(10)
        for (x, y, direction), failures in room.blocked_edges.items():
            rect = _scene_cell_rect(x, y)
            if direction == "up":
                line = (rect.left(), rect.top(), rect.right(), rect.top())
            elif direction == "down":
                line = (rect.left(), rect.bottom(), rect.right(), rect.bottom())
            elif direction == "left":
                line = (rect.left(), rect.top(), rect.left(), rect.bottom())
            else:
                line = (rect.right(), rect.top(), rect.right(), rect.bottom())
            item = self.addLine(*line, _pen(self._palette.get("wall", "#ff5c75"), min(5, 2 + failures / 2)))
            item.setZValue(20)
            self._metadata(
                item,
                {
                    "kind": "wall",
                    "room": self._room_name,
                    "cell": (x, y),
                    "direction": direction,
                    "failures": failures,
                    "label": f"Learned wall toward {direction}",
                    "tooltip": f"{failures} supported blockage observation(s)",
                },
            )

    def _draw_guesses(self, guesses: list[VisualGuessEntry], current_id: str | None) -> None:
        marker_offsets: dict[tuple[int, int], int] = {}
        for guess in guesses:
            key = {
                "possible_exit": "guess_exit",
                "possible_character": "guess_character",
                "possible_interactable": "guess_object",
            }.get(guess.hypothesis, "guess_object")
            color = self._palette.get(key, "#f7bd45")
            payload: dict[str, object] = {
                "kind": "guess",
                "room": self._room_name,
                "id": guess.stable_id,
                "marker": guess.marker,
                "label": guess.label,
                "confidence": guess.confidence,
                "evidence": guess.evidence,
                "status": guess.status,
                "anchor_cell": guess.anchor_cell,
                "feature_box_world": guess.feature_box_world,
                "tooltip": f"{guess.marker} · {guess.label}\n{guess.evidence}\n{guess.status}",
            }
            items: list[QGraphicsItem] = []
            if guess.feature_box_world is not None:
                rect = _scene_world_rect(guess.feature_box_world)
                if rect.width() < 4:
                    rect.adjust(-2, 0, 2, 0)
                if rect.height() < 4:
                    rect.adjust(0, -2, 0, 2)
                box = self.addRect(
                    rect,
                    _pen(color, 3 if guess.stable_id == current_id else 2, dashed=guess.hypothesis == "possible_exit"),
                    QBrush(Qt.BrushStyle.NoBrush),
                )
                box.setZValue(40)
                self._metadata(box, payload)
                items.append(box)
            anchor = _scene_point_from_world(guess.anchor_world)
            bucket = (round(anchor.x() / 30), round(anchor.y() / 30))
            offset = marker_offsets.get(bucket, 0)
            marker_offsets[bucket] = offset + 1
            marker_center = anchor + QPointF(12 + offset * 22, -12 - offset * 8)
            connector = self.addLine(
                anchor.x(), anchor.y(), marker_center.x(), marker_center.y(), _pen(color, 1, dashed=True)
            )
            connector.setZValue(39)
            radius = 10.0
            marker = self.addEllipse(
                QRectF(marker_center.x() - radius, marker_center.y() - radius, radius * 2, radius * 2),
                _pen("#08101f", 2),
                QBrush(QColor(color)),
            )
            marker.setZValue(42)
            self._metadata(marker, payload)
            label = QGraphicsSimpleTextItem(guess.marker)
            label.setBrush(QBrush(QColor("#07101f")))
            label.setPos(marker_center.x() - label.boundingRect().width() / 2, marker_center.y() - label.boundingRect().height() / 2)
            label.setZValue(43)
            label.setData(0, payload)
            label.setToolTip(str(payload["tooltip"]))
            self.addItem(label)
            items.extend((marker, label))
            self.guess_items[guess.stable_id] = items

    def _draw_interactables(self, room) -> None:
        color = self._palette.get("interactable", "#ffd45a")
        for (x, y), record in room.interactables.items():
            center = _scene_point_from_cell((x + 0.5, y + 0.5))
            item = self.addEllipse(
                QRectF(center.x() - 7, center.y() - 7, 14, 14),
                _pen("#4a3610", 2),
                QBrush(QColor(color)),
            )
            item.setZValue(50)
            self._metadata(
                item,
                {
                    "kind": "interactable",
                    "room": self._room_name,
                    "cell": (x, y),
                    "record": dict(record),
                    "label": str(record.get("name") or "Learned interaction"),
                    "tooltip": f"{record.get('classification', 'unknown')} · {record.get('last_outcome', 'unknown outcome')}",
                },
            )

    def _draw_warps(self, room) -> None:
        for number, ((x, y, target), record) in enumerate(sorted(room.warps.items()), start=1):
            badge, color_key, role_description = warp_role_badge(record.get("role"))
            color = self._palette.get(color_key, self._palette.get("warp", "#a486ff"))
            footprint = record.get("source_footprint")
            bounds = footprint.get("bounds") if isinstance(footprint, Mapping) else None
            try:
                if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                    left, top, right, bottom = (int(value) for value in bounds)
                else:
                    left = right = x
                    top = bottom = y
            except (TypeError, ValueError):
                left = right = x
                top = bottom = y
            rect = _scene_cell_rect(left, top, right - left + 1, bottom - top + 1)
            item = self.addRect(rect, _pen(color, 3), QBrush(Qt.BrushStyle.NoBrush))
            item.setZValue(55)
            payload = {
                "kind": "warp",
                "room": self._room_name,
                "target_room": target,
                "cell": (x, y),
                "role": str(record.get("role") or "unknown"),
                "confidence": record.get("role_confidence"),
                "record": dict(record),
                "label": f"{badge}{number} · {target}",
                "tooltip": f"{target}\n{role_description}",
            }
            self._metadata(item, payload)
            text = QGraphicsSimpleTextItem(f"{badge}{number}")
            text.setBrush(QColor(color))
            text.setPos(rect.topLeft() + QPointF(3, -text.boundingRect().height()))
            text.setZValue(56)
            text.setData(0, payload)
            text.setToolTip(str(payload["tooltip"]))
            self.addItem(text)

    def _draw_camera(self, model: WallMapModel, room_name: str) -> None:
        if model.current_camera is None or model.current_camera[0] != room_name:
            return
        _name, x, y, width, height = model.current_camera
        rect = _scene_world_rect((x, y, x + width, y + height))
        item = self.addRect(rect, _pen(self._palette.get("camera", "#56a0ff"), 3), QBrush(Qt.BrushStyle.NoBrush))
        item.setZValue(70)
        label = QGraphicsSimpleTextItem("VISIBLE NOW")
        label.setBrush(QColor(self._palette.get("camera", "#56a0ff")))
        label.setPos(rect.topLeft() + QPointF(4, 3))
        label.setZValue(71)
        self.addItem(label)

    def _draw_player(self, model: WallMapModel, room_name: str) -> None:
        if model.current_room != room_name:
            return
        if model.current_display_position is not None:
            center = _scene_point_from_world(model.current_display_position)
        elif model.current_cell is not None:
            center = _scene_point_from_cell((model.current_cell[0] + 0.5, model.current_cell[1] + 0.5))
        else:
            return
        radius = 6.0
        item = self.addEllipse(
            QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2),
            _pen("#0a386e", 2),
            QBrush(QColor(self._palette.get("player", "#45a1ff"))),
        )
        item.setZValue(80)
        self._metadata(
            item,
            {
                "kind": "player",
                "room": room_name,
                "position": model.current_display_position,
                "direction": model.current_direction,
                "label": "Kris",
                "tooltip": f"Current position · facing {model.current_direction or 'unknown'}",
            },
        )
        if model.current_direction in DIRECTION_VECTORS:
            dx, dy = DIRECTION_VECTORS[model.current_direction]
            direction = self.addLine(
                center.x(), center.y(), center.x() + dx * 13, center.y() + dy * 13, _pen("#ffffff", 2)
            )
            direction.setZValue(81)


class RoomMapView(QGraphicsView):
    """Zoomable/pannable view which remembers one camera per room."""

    itemSelected = Signal(object)
    guessesChanged = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.map_scene = RoomMapScene(self)
        self.setScene(self.map_scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        # Left click remains selection. Middle/right drag mirrors the legacy
        # map controls and avoids accidental lead selection while panning.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor("#02050a")))
        self._model: WallMapModel | None = None
        self._room_name = ""
        self._palette: Mapping[str, str] = {}
        self._layers: dict[str, bool] = {
            "scene": True,
            "navigation": True,
            "visits": False,
            "grid": False,
            "objects": True,
            "guesses": True,
            "camera": True,
        }
        self._view_states: dict[str, tuple[QTransform, int, int]] = {}
        self._pan_active = False
        self._pan_last = QPoint()

    @property
    def room_name(self) -> str:
        return self._room_name

    @property
    def layers(self) -> dict[str, bool]:
        return dict(self._layers)

    def set_layer(self, name: str, enabled: bool) -> None:
        if name not in self._layers:
            return
        self._layers[name] = enabled
        self.refresh(preserve_view=True)

    def set_model(self, model: WallMapModel) -> None:
        self._model = model

    def set_palette(self, palette: Mapping[str, str]) -> None:
        self._palette = palette
        self.refresh(preserve_view=True)

    def set_room(self, room_name: str, *, fit: bool = False) -> None:
        if room_name == self._room_name and not fit:
            self.refresh(preserve_view=True)
            return
        self._save_view_state()
        self._room_name = room_name
        self.refresh(preserve_view=not fit)

    def _save_view_state(self) -> None:
        if not self._room_name:
            return
        self._view_states[self._room_name] = (
            QTransform(self.transform()),
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
        )

    def refresh(self, *, preserve_view: bool = True) -> None:
        if self._model is None:
            return
        if preserve_view and self._room_name:
            self._save_view_state()
        guesses = self.map_scene.rebuild(
            self._model,
            self._room_name,
            layers=self._layers,
            palette=self._palette,
        )
        self.guessesChanged.emit(guesses)
        state = self._view_states.get(self._room_name) if preserve_view else None
        if state is None:
            self.fit_to_room()
        else:
            transform, horizontal, vertical = state
            self.setTransform(transform)
            self.horizontalScrollBar().setValue(horizontal)
            self.verticalScrollBar().setValue(vertical)

    def fit_to_room(self) -> None:
        rect = self.map_scene.itemsBoundingRect()
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-18, -18, 18, 18), Qt.AspectRatioMode.KeepAspectRatio)
        if self._room_name:
            self._save_view_state()

    def focus_guess(self, stable_id: str) -> None:
        self.map_scene.clearSelection()
        items = self.map_scene.guess_items.get(stable_id, [])
        if not items:
            return
        bounds = QRectF()
        for item in items:
            item.setSelected(True)
            bounds = bounds.united(item.sceneBoundingRect())
        self.centerOn(bounds.center())

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.angleDelta().y() == 0:
            return super().wheelEvent(event)
        current = self.transform().m11()
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        target = current * factor
        if 0.08 <= target <= 16:
            self.scale(factor, factor)
            self._save_view_state()
        event.accept()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() in {Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton}:
            self._pan_active = True
            self._pan_last = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)
        item = self.itemAt(event.position().toPoint())
        if item is None:
            self.itemSelected.emit(None)
            return
        payload = item.data(0)
        if payload:
            self.itemSelected.emit(payload)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._pan_active:
            current = event.position().toPoint()
            delta = current - self._pan_last
            self._pan_last = current
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            self._save_view_state()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._pan_active and event.button() in {
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        }:
            self._pan_active = False
            self.viewport().unsetCursor()
            self._save_view_state()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if not self._room_name or self._room_name not in self._view_states:
            self.fit_to_room()
