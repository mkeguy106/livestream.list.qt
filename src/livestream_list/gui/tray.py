"""System tray icon for Livestream List using Qt's QSystemTrayIcon."""

import logging
from collections.abc import Callable

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .theme import get_theme

logger = logging.getLogger(__name__)


def create_app_icon(size: int = 22) -> QIcon:
    """Create the application icon at the specified size.

    Colors are derived from the active theme so the icon matches the app
    (and the system tray it sits in) for every theme, including monochrome
    ones and light trays.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))  # Transparent background

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Scale factor based on size (22 is the base size)
    scale = size / 22.0

    theme = get_theme()
    monitor_color = QColor(theme.text_primary)
    screen_color = QColor(theme.window_bg)
    play_color = QColor(theme.accent)
    live_color = QColor(theme.status_live)

    # Monitor body
    painter.setPen(monitor_color)
    painter.setBrush(monitor_color)
    painter.drawRoundedRect(
        int(2 * scale),
        int(2 * scale),
        int(18 * scale),
        int(14 * scale),
        int(2 * scale),
        int(2 * scale),
    )

    # Screen (theme background inside)
    painter.setBrush(screen_color)
    painter.drawRect(int(4 * scale), int(4 * scale), int(14 * scale), int(10 * scale))

    # Play button triangle
    painter.setPen(play_color)
    painter.setBrush(play_color)
    triangle = QPolygon(
        [
            QPoint(int(8 * scale), int(6 * scale)),
            QPoint(int(8 * scale), int(12 * scale)),
            QPoint(int(14 * scale), int(9 * scale)),
        ]
    )
    painter.drawPolygon(triangle)

    # Live indicator dot
    painter.setPen(live_color)
    painter.setBrush(live_color)
    painter.drawEllipse(int(15 * scale), int(3 * scale), int(4 * scale), int(4 * scale))

    # Monitor stand
    painter.setPen(monitor_color)
    painter.setBrush(monitor_color)
    painter.drawRect(int(9 * scale), int(16 * scale), int(4 * scale), int(2 * scale))
    painter.drawRect(int(7 * scale), int(18 * scale), int(8 * scale), int(2 * scale))

    painter.end()

    return QIcon(pixmap)


def is_tray_available() -> bool:
    """Check if system tray is available."""
    return QSystemTrayIcon.isSystemTrayAvailable()


class TrayIcon(QSystemTrayIcon):
    """System tray icon with context menu."""

    def __init__(
        self,
        parent: QWidget | None,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        get_notifications_enabled: Callable[[], bool],
        set_notifications_enabled: Callable[[bool], None],
        on_show_notification_log: Callable[[], None] | None = None,
    ):
        super().__init__(parent)

        self._on_open = on_open
        self._on_quit = on_quit
        self._get_notifications_enabled = get_notifications_enabled
        self._set_notifications_enabled = set_notifications_enabled
        self._on_show_notification_log = on_show_notification_log

        # Create icon
        self._create_icon()

        # Set tooltip
        self.setToolTip("Livestream List (Qt)")

        # Create context menu
        self._create_menu()

        # Connect signals
        self.activated.connect(self._on_activated)

    def _create_icon(self) -> None:
        """Create the tray icon."""
        self.setIcon(create_app_icon(22))

    def refresh_icon(self) -> None:
        """Redraw the icon with the current theme colors."""
        self._create_icon()

    def _create_menu(self) -> None:
        """Create the context menu."""
        menu = QMenu()

        # Open action
        open_action = menu.addAction("Open Livestream List (Qt)")
        open_action.triggered.connect(self._on_open)

        menu.addSeparator()

        # Notifications toggle
        self._notifications_action = menu.addAction("Notifications")
        self._notifications_action.setCheckable(True)
        self._notifications_action.setChecked(self._get_notifications_enabled())
        self._notifications_action.triggered.connect(self._on_notifications_toggled)

        # Recent Notifications log
        if self._on_show_notification_log:
            log_action = menu.addAction("Recent Notifications...")
            log_action.triggered.connect(self._on_show_notification_log)

        menu.addSeparator()

        # Quit action
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self._on_quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation."""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_open()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            # Toggle notifications on middle-click
            current = self._get_notifications_enabled()
            self._set_notifications_enabled(not current)
            self._notifications_action.setChecked(not current)

    def _on_notifications_toggled(self, checked: bool) -> None:
        """Handle notifications toggle."""
        self._set_notifications_enabled(checked)

    def update_notifications_state(self) -> None:
        """Update the notifications checkbox state."""
        self._notifications_action.setChecked(self._get_notifications_enabled())

    def destroy(self) -> None:
        """Clean up the tray icon."""
        self.hide()
