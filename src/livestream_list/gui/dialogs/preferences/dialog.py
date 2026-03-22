"""Preferences dialog - slim coordinator that hosts per-tab widgets."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ...app import Application

from .accounts_tab import AccountsTab
from .chat_tab import ChatTab
from .general_tab import GeneralTab
from .playback_tab import PlaybackTab

logger = logging.getLogger(__name__)


class PreferencesDialog(QDialog):
    """Preferences dialog with multiple tabs."""

    def __init__(self, parent: QWidget | None, app: Application, initial_tab: int = 0) -> None:
        super().__init__(parent)
        self.app = app
        self._loading = True  # Prevent cascading updates during init

        self.setWindowTitle("Preferences")
        self.setMinimumSize(500, 500)
        # Restore saved size or use default
        pref_w = getattr(self.app.settings, "_prefs_width", 550)
        pref_h = getattr(self.app.settings, "_prefs_height", 550)
        self.resize(pref_w, pref_h)

        layout = QVBoxLayout(self)

        # Tab widget
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # General tab
        self.general_tab = GeneralTab(self)
        tabs.addTab(self.general_tab, "General")

        # Playback tab
        self.playback_tab = PlaybackTab(self)
        tabs.addTab(self.playback_tab, "Playback")

        # Chat tab
        self.chat_tab = ChatTab(self)
        tabs.addTab(self.chat_tab, "Chat")

        # Appearance tab (inline — only ~12 lines)
        appearance_tab = self._create_appearance_tab()
        tabs.addTab(appearance_tab, "Appearance")

        # Accounts tab
        self.accounts_tab = AccountsTab(self)
        tabs.addTab(self.accounts_tab, "Accounts")

        if initial_tab:
            tabs.setCurrentIndex(initial_tab)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._loading = False  # Init complete, allow updates

    def _create_appearance_tab(self) -> QWidget:
        """Create the Appearance tab with the theme editor."""
        from ..theme_editor import ThemeEditorWidget

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        self._theme_editor = ThemeEditorWidget(self.app, parent=widget)
        layout.addWidget(self._theme_editor)

        return widget

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Save dialog size on close."""
        self.app.settings._prefs_width = self.width()  # type: ignore[attr-defined]
        self.app.settings._prefs_height = self.height()  # type: ignore[attr-defined]
        super().closeEvent(event)
