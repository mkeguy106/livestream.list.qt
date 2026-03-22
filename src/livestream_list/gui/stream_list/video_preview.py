"""Live video preview on hover using QMediaPlayer (native Qt, Wayland-safe)."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QEnterEvent,
    QFont,
    QPainter,
    QPaintEvent,
    QScreen,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QVBoxLayout, QWidget

from ...core.platform import SUBPROCESS_NO_WINDOW, host_command

if TYPE_CHECKING:
    from ...core.models import Livestream
    from ...core.settings import Settings
    from ...core.streamlink import StreamlinkLauncher

logger = logging.getLogger(__name__)

PREVIEW_WIDTH = 320
PREVIEW_HEIGHT = 180
HOVER_DELAY_MS = 400
GRACE_PERIOD_MS = 300
URL_CACHE_TTL_S = 60
MOUSE_FOLLOW_INTERVAL_MS = 50
CURSOR_OFFSET_X = 16
CURSOR_OFFSET_Y = 16


class PreviewUrlResolver(QThread):
    """Resolves a stream URL to a direct HLS/DASH URL in a background thread."""

    resolved = Signal(str, str)  # channel_key, direct_url
    failed = Signal(str)  # channel_key

    def __init__(
        self,
        channel_key: str,
        stream_url: str,
        twitch_token: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._channel_key = channel_key
        self._stream_url = stream_url
        self._twitch_token = twitch_token

    def run(self) -> None:
        """Try streamlink --stream-url first, then yt-dlp -g."""
        url = self._try_streamlink() or self._try_ytdlp()
        if url:
            self.resolved.emit(self._channel_key, url)
        else:
            self.failed.emit(self._channel_key)

    def _try_streamlink(self) -> str | None:
        try:
            args = ["streamlink", "--stream-url"]
            if self._twitch_token:
                args.extend(["--twitch-api-header", f"Authorization=OAuth {self._twitch_token}"])
            args.extend([self._stream_url, "worst,360p,480p,best"])
            cmd = host_command(args)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, **SUBPROCESS_NO_WINDOW
            )
            if result.returncode == 0 and result.stdout.strip():
                return str(result.stdout.strip().splitlines()[0])
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("streamlink --stream-url failed: %s", e)
        return None

    def _try_ytdlp(self) -> str | None:
        try:
            cmd = host_command(
                ["yt-dlp", "-g", "-f", "worstvideo*", "--no-warnings", self._stream_url]
            )
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, **SUBPROCESS_NO_WINDOW
            )
            if result.returncode == 0 and result.stdout.strip():
                return str(result.stdout.strip().splitlines()[0])
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("yt-dlp -g failed: %s", e)
        return None


class VideoPreviewPopup(QWidget):
    """Frameless popup that shows a live video preview following the cursor."""

    popup_entered = Signal()
    popup_left = Signal()

    def __init__(self, main_window: QWidget) -> None:
        super().__init__(None)
        self._main_window = main_window
        # ToolTip type stays above parent on KDE Wayland when transient parent is set
        self.setWindowFlags(Qt.WindowType.ToolTip)
        self.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT + 28)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # Loading state
        self._loading_label = QLabel("Loading preview...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(
            "QLabel { background-color: #1a1a2e; color: #aaa; font-size: 13px; }"
        )
        self._stack.addWidget(self._loading_label)

        # Video widget (native Qt — works on Wayland)
        self._video_widget = QVideoWidget()
        self._stack.addWidget(self._video_widget)

        # Media player
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setMuted(True)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)

        # Channel name overlay (painted on top)
        self._channel_name = ""

        # Mouse-follow timer
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(MOUSE_FOLLOW_INTERVAL_MS)
        self._follow_timer.timeout.connect(self._follow_cursor)

        self._stack.setCurrentIndex(0)

    def show_for_stream(self, livestream: Livestream, muted: bool) -> None:
        """Position at cursor and show the popup for a given stream."""
        self._channel_name = livestream.display_name
        self._stack.setCurrentIndex(0)
        self._loading_label.setText(f"Loading {livestream.display_name}...")

        self._audio.setMuted(muted)

        # Position at cursor before showing
        self._position_at_cursor()
        # Set transient parent so Wayland compositor keeps popup above main window
        self.winId()  # Force native handle creation
        main_handle = self._main_window.windowHandle()
        if main_handle and self.windowHandle():
            self.windowHandle().setTransientParent(main_handle)
        self.show()
        self._follow_timer.start()

    def start_playback(self, url: str) -> None:
        """Switch from loading to video and start playback."""
        self._stack.setCurrentIndex(1)
        self._player.setSource(QUrl(url))
        self._player.play()
        self.update()  # Trigger repaint for channel name overlay

    def hide_preview(self) -> None:
        """Stop playback and hide."""
        self._follow_timer.stop()
        self._player.stop()
        self._player.setSource(QUrl())
        self.hide()

    def cleanup(self) -> None:
        """Release resources."""
        self._follow_timer.stop()
        self._player.stop()

    def _position_at_cursor(self) -> None:
        """Position the popup near the cursor, clamped to screen bounds."""
        cursor_pos = QCursor.pos()
        screen = self._get_screen_at(cursor_pos)
        if screen:
            screen_geo = screen.availableGeometry()
        else:
            screen_geo = QRect(0, 0, 3840, 2160)

        x = cursor_pos.x() + CURSOR_OFFSET_X
        y = cursor_pos.y() + CURSOR_OFFSET_Y

        # Flip left if would go off-screen right
        if x + self.width() > screen_geo.right():
            x = cursor_pos.x() - self.width() - CURSOR_OFFSET_X

        # Flip up if would go off-screen bottom
        if y + self.height() > screen_geo.bottom():
            y = cursor_pos.y() - self.height() - CURSOR_OFFSET_Y

        # Clamp
        x = max(screen_geo.left(), min(x, screen_geo.right() - self.width()))
        y = max(screen_geo.top(), min(y, screen_geo.bottom() - self.height()))

        self.move(x, y)

    def _follow_cursor(self) -> None:
        """Reposition the popup to follow the cursor."""
        if self.isVisible():
            self._position_at_cursor()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        """Draw channel name overlay at the bottom."""
        super().paintEvent(event)
        if not self._channel_name or self._stack.currentIndex() == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent bar at bottom
        bar_height = 28
        bar_rect = QRect(0, self.height() - bar_height, self.width(), bar_height)
        painter.fillRect(bar_rect, QColor(0, 0, 0, 160))

        # Channel name text
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        text_rect = bar_rect.adjusted(8, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, self._channel_name)
        painter.end()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        """Notify controller that mouse entered the popup."""
        super().enterEvent(event)
        self.popup_entered.emit()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        """Notify controller that mouse left the popup."""
        super().leaveEvent(event)
        self.popup_left.emit()

    @staticmethod
    def _get_screen_at(point: QPoint) -> QScreen | None:
        """Get the screen containing the given point."""
        app = QApplication.instance()
        if app:
            for screen in app.screens():  # type: ignore[attr-defined]
                if screen.availableGeometry().contains(point):
                    return screen  # type: ignore[no-any-return]
        return None


class VideoPreviewController(QWidget):
    """Orchestrates hover detection, URL resolution, and preview popup lifecycle."""

    def __init__(
        self,
        settings: Settings,
        launcher: StreamlinkLauncher,
        main_window: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.hide()  # Controller widget is invisible

        self._settings = settings
        self._launcher = launcher
        self._main_window = main_window

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(self._on_hover_timer)

        self._grace_timer = QTimer(self)
        self._grace_timer.setSingleShot(True)
        self._grace_timer.setInterval(GRACE_PERIOD_MS)
        self._grace_timer.timeout.connect(self._hide_preview)

        self._popup: VideoPreviewPopup | None = None
        self._resolver: PreviewUrlResolver | None = None
        self._current_key: str | None = None
        self._pending_livestream: Livestream | None = None
        self._url_cache: dict[str, tuple[str, float]] = {}

    @property
    def is_available(self) -> bool:
        """Whether video preview is available."""
        return True  # QMediaPlayer is always available with PySide6

    def on_hover_enter(self, livestream: Livestream) -> None:
        """Called when mouse enters a live channel row."""
        if not self._settings.streamlink.preview_on_hover:
            return
        if not livestream.live:
            return

        key = livestream.channel.unique_key
        if self._launcher.is_playing(key):
            return

        # Already showing this channel
        if self._current_key == key and self._popup and self._popup.isVisible():
            self._grace_timer.stop()
            return

        # Different channel — hide current and start new hover
        if self._current_key and self._current_key != key:
            self._hide_preview()

        self._grace_timer.stop()
        self._pending_livestream = livestream
        self._hover_timer.start()

    def on_hover_leave(self) -> None:
        """Called when mouse leaves a channel row."""
        self._hover_timer.stop()
        self._pending_livestream = None
        if self._current_key:
            self._grace_timer.start()

    def on_popup_enter(self) -> None:
        """Called when mouse enters the preview popup."""
        self._grace_timer.stop()

    def on_popup_leave(self) -> None:
        """Called when mouse leaves the preview popup."""
        self._grace_timer.start()

    def cleanup(self) -> None:
        """Release all resources."""
        self._hover_timer.stop()
        self._grace_timer.stop()
        if self._resolver and self._resolver.isRunning():
            self._resolver.quit()
            self._resolver.wait(2000)
        if self._popup:
            self._popup.hide_preview()
            self._popup.cleanup()
            self._popup.deleteLater()
            self._popup = None

    def _ensure_popup(self) -> VideoPreviewPopup | None:
        """Lazily create the popup."""
        if self._popup is None:
            try:
                self._popup = VideoPreviewPopup(self._main_window)
                self._popup.popup_entered.connect(self.on_popup_enter)
                self._popup.popup_left.connect(self.on_popup_leave)
            except Exception:
                logger.exception("Failed to create video preview popup")
                self._popup = None
        return self._popup

    def _on_hover_timer(self) -> None:
        """Hover delay elapsed — show the preview."""
        livestream = self._pending_livestream
        if not livestream:
            return

        key = livestream.channel.unique_key
        self._current_key = key

        popup = self._ensure_popup()
        if not popup:
            return
        muted = not self._settings.streamlink.preview_audio
        popup.show_for_stream(livestream, muted)

        # Check URL cache
        cached = self._url_cache.get(key)
        if cached and (time.monotonic() - cached[1]) < URL_CACHE_TTL_S:
            popup.start_playback(cached[0])
            return

        # Resolve URL in background
        self._start_resolver(key, livestream)

    def _start_resolver(self, key: str, livestream: Livestream) -> None:
        """Start a background URL resolver thread."""
        # Cancel any running resolver
        if self._resolver and self._resolver.isRunning():
            self._resolver.quit()
            self._resolver.wait(1000)

        # Pass Twitch auth token for Turbo/subscriber ad-free viewing
        twitch_token: str | None = None
        if self._settings.streamlink.twitch_turbo and livestream.channel.platform.value == "twitch":
            twitch_token = self._settings.twitch.browser_auth_token or None

        self._resolver = PreviewUrlResolver(
            key, livestream.stream_url, twitch_token=twitch_token, parent=self
        )
        self._resolver.resolved.connect(self._on_url_resolved)
        self._resolver.failed.connect(self._on_url_failed)
        self._resolver.start()

    def _on_url_resolved(self, key: str, url: str) -> None:
        """URL resolved — start playback if still relevant."""
        self._url_cache[key] = (url, time.monotonic())

        if key != self._current_key or not self._popup or not self._popup.isVisible():
            return

        self._popup.start_playback(url)

    def _on_url_failed(self, key: str) -> None:
        """URL resolution failed — hide the preview."""
        logger.warning("Failed to resolve preview URL for %s", key)
        if key == self._current_key:
            self._hide_preview()

    def _hide_preview(self) -> None:
        """Hide the preview popup and reset state."""
        self._hover_timer.stop()
        self._grace_timer.stop()
        self._current_key = None
        if self._popup and self._popup.isVisible():
            self._popup.hide_preview()
