"""Emote picker popup with searchable grid."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QGridLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...chat.emotes.cache import EmoteCache
from ...chat.models import ChatEmote
from ..theme import get_theme

logger = logging.getLogger(__name__)

EMOTE_BUTTON_SIZE = 36
GRID_COLUMNS = 8


class EmotePickerWidget(QWidget):
    """Searchable emote picker popup.

    Shows emotes in a grid organized by provider tabs.
    Clicking an emote inserts its code at the cursor position.
    """

    emote_selected = Signal(str)  # emote name/code

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._emotes: dict[str, list[ChatEmote]] = {}  # provider -> emotes
        self._image_store: EmoteCache | None = None
        self._all_buttons: list[tuple[QPushButton, ChatEmote]] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the picker UI."""
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFixedSize(320, 350)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search emotes...")
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        # Tab widget for providers
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Apply theme styling
        self.apply_theme()

    def apply_theme(self) -> None:
        """Apply theme colors to the picker."""
        theme = get_theme()
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.chat_input_bg};
                border: 1px solid {theme.border_light};
                border-radius: 4px;
                padding: 4px 8px;
                color: {theme.text_primary};
                font-size: 12px;
            }}
        """)
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background-color: {theme.popup_bg};
            }}
            QTabBar::tab {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_muted};
                padding: 4px 8px;
                font-size: 11px;
                border: none;
            }}
            QTabBar::tab:selected {{
                color: {theme.selection_text};
                border-bottom: 2px solid {theme.accent};
            }}
        """)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {theme.popup_bg};
                border: 1px solid {theme.border_light};
                border-radius: 6px;
            }}
        """)

    def set_emotes(self, emotes_by_provider: dict[str, list[ChatEmote]]) -> None:
        """Set the available emotes, organized by provider."""
        self._emotes = emotes_by_provider
        self._rebuild_tabs()

    def set_image_store(self, store: EmoteCache) -> None:
        """Set the shared image store."""
        self._image_store = store

    def _current_scale(self) -> float:
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            return 1.0

    def show_picker(self, pos) -> None:
        """Show the picker at the given position."""
        self.move(pos)
        self._search.clear()
        self._search.setFocus()
        self.show()

    def _rebuild_tabs(self) -> None:
        """Rebuild the tab widget with current emotes."""
        self._tabs.clear()
        self._all_buttons.clear()

        # Provider display names
        provider_names = {
            "twitch": "Twitch",
            "7tv": "7TV",
            "bttv": "BTTV",
            "ffz": "FFZ",
        }

        for provider, emotes in self._emotes.items():
            if not emotes:
                continue

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

            container = QWidget()
            grid = QGridLayout(container)
            grid.setSpacing(2)
            grid.setContentsMargins(2, 2, 2, 2)

            for i, emote in enumerate(emotes):
                btn = self._create_emote_button(emote)
                row = i // GRID_COLUMNS
                col = i % GRID_COLUMNS
                grid.addWidget(btn, row, col)
                self._all_buttons.append((btn, emote))

            scroll.setWidget(container)
            tab_name = provider_names.get(provider, provider)
            self._tabs.addTab(scroll, tab_name)

    def _create_emote_button(self, emote: ChatEmote) -> QPushButton:
        """Create a button for an emote."""
        theme = get_theme()
        btn = QPushButton()
        btn.setFixedSize(EMOTE_BUTTON_SIZE, EMOTE_BUTTON_SIZE)
        btn.setToolTip(emote.name)

        # Try to set icon from image set
        if self._image_store and emote.image_set:
            image_set = emote.image_set.bind(self._image_store)
            emote.image_set = image_set
            image_ref = image_set.get_image_or_loaded(scale=self._current_scale())
            if image_ref:
                pixmap = image_ref.pixmap_or_load()
                if pixmap and not pixmap.isNull():
                    btn.setIcon(QIcon(pixmap))
                    btn.setIconSize(pixmap.size())
                else:
                    btn.setText(emote.name[:3])
            else:
                btn.setText(emote.name[:3])
        else:
            btn.setText(emote.name[:3])

        btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 8px;
                color: {theme.text_muted};
                background-color: {theme.chat_input_bg};
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                border-color: {theme.accent};
                background-color: {theme.popup_hover};
            }}
        """)

        btn.clicked.connect(lambda checked=False, name=emote.name: self._on_emote_clicked(name))
        return btn

    def _on_emote_clicked(self, name: str) -> None:
        """Handle emote button click."""
        self.emote_selected.emit(name)
        self.hide()

    def _on_search_changed(self, text: str) -> None:
        """Filter emotes by search text."""
        search = text.lower()
        for btn, emote in self._all_buttons:
            visible = not search or search in emote.name.lower()
            btn.setVisible(visible)
