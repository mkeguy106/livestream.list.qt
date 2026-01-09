"""System tray icon for Livestream List using Qt's QSystemTrayIcon."""

import logging
from typing import Callable, Optional

from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor

logger = logging.getLogger(__name__)


def is_tray_available() -> bool:
    """Check if system tray is available."""
    return QSystemTrayIcon.isSystemTrayAvailable()


class TrayIcon(QSystemTrayIcon):
    """System tray icon with context menu."""

    def __init__(
        self,
        parent,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        get_notifications_enabled: Callable[[], bool],
        set_notifications_enabled: Callable[[bool], None],
    ):
        super().__init__(parent)

        self._on_open = on_open
        self._on_quit = on_quit
        self._get_notifications_enabled = get_notifications_enabled
        self._set_notifications_enabled = set_notifications_enabled

        # Create icon
        self._create_icon()

        # Set tooltip
        self.setToolTip("Livestream List (Qt)")

        # Create context menu
        self._create_menu()

        # Connect signals
        self.activated.connect(self._on_activated)

    def _create_icon(self):
        """Create the tray icon."""
        # Create a simple 22x22 icon
        size = 22
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))  # Transparent background

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw a simple monitor-like shape
        # Monitor frame
        monitor_color = QColor(100, 150, 200)  # Light blue
        painter.setPen(monitor_color)
        painter.setBrush(monitor_color)
        painter.drawRoundedRect(2, 2, 18, 14, 2, 2)

        # Screen (darker inside)
        screen_color = QColor(40, 60, 80)
        painter.setBrush(screen_color)
        painter.drawRect(4, 4, 14, 10)

        # Play button triangle
        play_color = QColor(255, 255, 255)
        painter.setPen(play_color)
        painter.setBrush(play_color)
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        triangle = QPolygon([
            QPoint(8, 6),
            QPoint(8, 12),
            QPoint(14, 9),
        ])
        painter.drawPolygon(triangle)

        # Live indicator dot (red)
        live_color = QColor(255, 50, 50)
        painter.setPen(live_color)
        painter.setBrush(live_color)
        painter.drawEllipse(15, 3, 4, 4)

        # Monitor stand
        painter.setPen(monitor_color)
        painter.setBrush(monitor_color)
        painter.drawRect(9, 16, 4, 2)
        painter.drawRect(7, 18, 8, 2)

        painter.end()

        self.setIcon(QIcon(pixmap))

    def _create_menu(self):
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

        menu.addSeparator()

        # Quit action
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self._on_quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left click - show window
            self._on_open()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # Double click - show window
            self._on_open()

    def _on_notifications_toggled(self, checked: bool):
        """Handle notifications toggle."""
        self._set_notifications_enabled(checked)

    def update_notifications_state(self):
        """Update the notifications checkbox state."""
        self._notifications_action.setChecked(self._get_notifications_enabled())

    def destroy(self):
        """Clean up the tray icon."""
        self.hide()
