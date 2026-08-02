"""Layered, motion-aware application backgrounds."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QLinearGradient, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .themes import BackgroundSettings, Theme, supported_background

try:  # Video support can be absent in reduced Qt builds.
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
except (ImportError, OSError):  # pragma: no cover - platform packaging dependent
    QMediaPlayer = None  # type: ignore[assignment]
    QVideoSink = None  # type: ignore[assignment]


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv"}


def bundled_background(name: str) -> Path | None:
    if not name:
        return None
    candidate = Path(__file__).resolve().parent / "assets" / "backgrounds" / name
    return candidate if candidate.is_file() else None


class BackgroundLayer(QWidget):
    """Paint a gradient and optional image/GIF/video beneath translucent UI."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAutoFillBackground(False)
        self._theme: Theme | None = None
        self._settings = BackgroundSettings()
        self._pixmap = QPixmap()
        self._movie: QMovie | None = None
        self._player = None
        self._video_sink = None
        self._parallax = QPointF()
        self._active = True
        self.last_warning = ""

    @property
    def source_path(self) -> Path | None:
        path = self._settings.path
        if path:
            candidate = Path(path).expanduser()
            if candidate.is_file():
                return candidate
        if self._theme is not None:
            return bundled_background(self._theme.default_background)
        return None

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._load_source()
        self.update()

    def set_settings(self, settings: BackgroundSettings) -> None:
        self._settings = settings
        self._load_source()
        self.update()

    def set_motion_active(self, active: bool) -> None:
        self._active = active
        animate = self._should_animate()
        if self._movie is not None:
            if animate:
                self._movie.start()
            else:
                self._movie.setPaused(True)
        if self._player is not None:
            if animate:
                self._player.play()
            else:
                self._player.pause()

    def set_parallax_position(self, normalized_x: float, normalized_y: float) -> None:
        if not self._settings.parallax or self._settings.reduce_motion:
            self._parallax = QPointF()
        else:
            self._parallax = QPointF(
                max(-1.0, min(1.0, normalized_x)) * 12,
                max(-1.0, min(1.0, normalized_y)) * 8,
            )
        self.update()

    def _should_animate(self) -> bool:
        configured = self._settings.animation
        if not self._settings.path and self._theme is not None:
            configured = configured or self._theme.default_animation
        return configured and not self._settings.reduce_motion and self._active

    def _stop_media(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        if self._player is not None:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._video_sink is not None:
            self._video_sink.deleteLater()
            self._video_sink = None

    def _load_source(self) -> None:
        self._stop_media()
        self._pixmap = QPixmap()
        self.last_warning = ""
        path = self.source_path
        if path is None:
            return
        if not supported_background(path):
            self.last_warning = f"Unsupported background: {path.suffix}"
            return
        suffix = path.suffix.casefold()
        if suffix in IMAGE_SUFFIXES:
            self._pixmap = QPixmap(str(path))
            if self._pixmap.isNull():
                self.last_warning = f"Could not decode {path.name}"
            return
        if suffix == ".gif":
            movie = QMovie(str(path))
            if not movie.isValid():
                self.last_warning = f"Could not decode {path.name}"
                return
            self._movie = movie
            movie.frameChanged.connect(self._movie_frame_changed)
            movie.jumpToFrame(0)
            self._pixmap = movie.currentPixmap()
            if self._should_animate():
                movie.start()
            return
        if suffix in VIDEO_SUFFIXES:
            if QMediaPlayer is None or QVideoSink is None:
                self.last_warning = "This Qt installation has no video backend."
                return
            try:
                self._video_sink = QVideoSink(self)
                self._video_sink.videoFrameChanged.connect(self._video_frame_changed)
                self._player = QMediaPlayer(self)
                self._player.setVideoSink(self._video_sink)
                self._player.setSource(QUrl.fromLocalFile(str(path.resolve())))
                self._player.mediaStatusChanged.connect(self._media_status_changed)
                if self._should_animate():
                    self._player.play()
                else:
                    # Decode the first frame, then pause in the frame callback.
                    self._player.play()
            except (RuntimeError, TypeError) as exc:
                self.last_warning = f"Video background unavailable: {exc}"

    def _movie_frame_changed(self, _frame: int) -> None:
        if self._movie is not None:
            self._pixmap = self._movie.currentPixmap()
            self.update()

    def _video_frame_changed(self, frame) -> None:
        if not frame.isValid():
            return
        image: QImage = frame.toImage()
        if not image.isNull():
            self._pixmap = QPixmap.fromImage(image)
            self.update()
        if self._player is not None and not self._should_animate():
            self._player.pause()

    def _media_status_changed(self, status) -> None:
        if self._player is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._should_animate():
            self._player.setPosition(0)
            self._player.play()

    def _target_rect(self, pixmap: QPixmap) -> QRectF:
        area = QRectF(self.rect())
        if pixmap.isNull():
            return area
        source_ratio = pixmap.width() / max(1, pixmap.height())
        area_ratio = area.width() / max(1, area.height())
        mode = self._settings.mode
        if mode == "stretch":
            target = area
        elif (mode == "cover" and source_ratio > area_ratio) or (
            mode == "contain" and source_ratio < area_ratio
        ):
            height = area.height()
            width = height * source_ratio
            target = QRectF((area.width() - width) / 2, 0, width, height)
        else:
            width = area.width()
            height = width / max(0.001, source_ratio)
            target = QRectF(0, (area.height() - height) / 2, width, height)
        target.translate(self._parallax)
        if mode == "cover":
            target.adjust(-14, -10, 14, 10)
        return target

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._theme is not None:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, QColor(self._theme.gradient[0]))
            gradient.setColorAt(1, QColor(self._theme.gradient[1]))
            painter.fillRect(self.rect(), gradient)
        else:
            painter.fillRect(self.rect(), QColor("#090e18"))
        if not self._pixmap.isNull():
            painter.drawPixmap(self._target_rect(self._pixmap), self._pixmap, QRectF(self._pixmap.rect()))
        tint = QColor(self._settings.tint)
        tint.setAlphaF(min(0.95, max(0.0, self._settings.dim)))
        painter.fillRect(self.rect(), tint)
        painter.end()
        super().paintEvent(event)
