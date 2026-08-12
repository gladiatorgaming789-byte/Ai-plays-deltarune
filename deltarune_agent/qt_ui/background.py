"""Layered, motion-aware application backgrounds."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl
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
BACKGROUND_MAX_FPS = 15
BACKGROUND_FRAME_INTERVAL = 1.0 / BACKGROUND_MAX_FPS
PARALLAX_INTERVAL_MS = 40
ANIMATED_OVERSCAN_PIXELS = 32


def bundled_background(name: str) -> Path | None:
    if not name:
        return None
    candidate = Path(__file__).resolve().parent / "assets" / "backgrounds" / name
    return candidate if candidate.is_file() else None


class BackgroundLayer(QWidget):
    """Paint a gradient and optional image/GIF/video beneath translucent UI.

    Animated backgrounds are deliberately presentation-rate limited. Video
    decoding may still happen at the source rate inside Qt, but expensive frame
    conversion, scaling, and full-window repaints are capped at 15 FPS. This is
    enough for a decorative layer while leaving CPU time for the controller and
    live map.
    """

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
        self._loaded_source: Path | None = None
        self._parallax = QPointF()
        self._pending_parallax = QPointF()
        self._parallax_timer = QTimer(self)
        self._parallax_timer.setSingleShot(True)
        self._parallax_timer.setInterval(PARALLAX_INTERVAL_MS)
        self._parallax_timer.timeout.connect(self._apply_pending_parallax)
        self._last_presented_frame = 0.0
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
        self._reload_source_if_needed()
        self._sync_media_state()
        self.update()

    def set_settings(self, settings: BackgroundSettings) -> None:
        self._settings = settings
        self._reload_source_if_needed()
        self._sync_media_state()
        if not settings.parallax or settings.reduce_motion:
            self._pending_parallax = QPointF()
            self._apply_pending_parallax()
        self.update()

    def set_motion_active(self, active: bool) -> None:
        self._active = active
        self._sync_media_state()

    def set_parallax_position(self, normalized_x: float, normalized_y: float) -> None:
        if not self._settings.parallax or self._settings.reduce_motion:
            target = QPointF()
        else:
            target = QPointF(
                max(-1.0, min(1.0, normalized_x)) * 12,
                max(-1.0, min(1.0, normalized_y)) * 8,
            )
        if target == self._pending_parallax and target == self._parallax:
            return
        self._pending_parallax = target
        if not self._parallax_timer.isActive():
            self._parallax_timer.start()

    def _apply_pending_parallax(self) -> None:
        if self._pending_parallax == self._parallax:
            return
        self._parallax = QPointF(self._pending_parallax)
        self.update()

    def _should_animate(self) -> bool:
        configured = self._settings.animation
        if not self._settings.path and self._theme is not None:
            configured = configured or self._theme.default_animation
        return configured and not self._settings.reduce_motion and self._active

    def _sync_media_state(self) -> None:
        animate = self._should_animate()
        if self._movie is not None:
            if animate:
                self._movie.start()
            elif self._movie.state() != QMovie.MovieState.NotRunning:
                self._movie.setPaused(True)
        if self._player is not None:
            if animate:
                self._player.play()
            elif self._pixmap.isNull():
                # A disabled video still needs one frame for its static preview.
                self._player.play()
            else:
                self._player.pause()

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

    def _reload_source_if_needed(self) -> None:
        source = self.source_path
        if source == self._loaded_source:
            return
        self._load_source(source)

    def _load_source(self, path: Path | None = None) -> None:
        self._stop_media()
        self._pixmap = QPixmap()
        self._loaded_source = path if path is not None else self.source_path
        self._last_presented_frame = 0.0
        self.last_warning = ""
        path = self._loaded_source
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
            # Decode each GIF frame once and reuse it on later loops. Decorative
            # backgrounds are a good memory-for-CPU tradeoff.
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            self._movie = movie
            movie.frameChanged.connect(self._movie_frame_changed)
            movie.jumpToFrame(0)
            self._pixmap = self._prepare_animated_pixmap(movie.currentPixmap())
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
                # Start long enough to obtain the first frame. The callback
                # pauses immediately when animation is disabled.
                self._player.play()
            except (RuntimeError, TypeError) as exc:
                self.last_warning = f"Video background unavailable: {exc}"

    def _frame_is_due(self) -> bool:
        now = monotonic()
        if self._last_presented_frame and (
            now - self._last_presented_frame < BACKGROUND_FRAME_INTERVAL
        ):
            return False
        self._last_presented_frame = now
        return True

    def _prepare_animated_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return pixmap
        maximum_width = self.width() + ANIMATED_OVERSCAN_PIXELS * 2
        maximum_height = self.height() + ANIMATED_OVERSCAN_PIXELS * 2
        if pixmap.width() <= maximum_width and pixmap.height() <= maximum_height:
            return pixmap
        return pixmap.scaled(
            maximum_width,
            maximum_height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.FastTransformation,
        )

    def _movie_frame_changed(self, _frame: int) -> None:
        if self._movie is None:
            return
        if not self._pixmap.isNull() and not self._frame_is_due():
            return
        self._pixmap = self._prepare_animated_pixmap(self._movie.currentPixmap())
        self.update()

    def _video_frame_changed(self, frame) -> None:
        if not frame.isValid():
            return
        animate = self._should_animate()
        if not self._pixmap.isNull() and animate and not self._frame_is_due():
            return
        if not self._pixmap.isNull() and not animate:
            if self._player is not None:
                self._player.pause()
            return
        image: QImage = frame.toImage()
        if not image.isNull():
            self._pixmap = self._prepare_animated_pixmap(QPixmap.fromImage(image))
            self._last_presented_frame = monotonic()
            self.update()
        if self._player is not None and not animate:
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
        # Static images keep high-quality scaling. Animated frames are already
        # reduced to the window's working size, so fast drawing avoids another
        # expensive full-window interpolation pass on every frame.
        animated = self._movie is not None or self._player is not None
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not animated)
        if self._theme is not None:
            gradient = QLinearGradient(0, 0, self.width(), self.height())
            gradient.setColorAt(0, QColor(self._theme.gradient[0]))
            gradient.setColorAt(1, QColor(self._theme.gradient[1]))
            painter.fillRect(self.rect(), gradient)
        else:
            painter.fillRect(self.rect(), QColor("#090e18"))
        if not self._pixmap.isNull():
            painter.drawPixmap(
                self._target_rect(self._pixmap),
                self._pixmap,
                QRectF(self._pixmap.rect()),
            )
        tint = QColor(self._settings.tint)
        tint.setAlphaF(min(0.95, max(0.0, self._settings.dim)))
        painter.fillRect(self.rect(), tint)
        painter.end()
        super().paintEvent(event)
