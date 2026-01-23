"""Inline emote autocomplete triggered by ':'."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from ...chat.models import ChatEmote

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 15
MIN_TRIGGER_LENGTH = 2  # Minimum chars after ':' to start suggesting


class EmoteCompleter(QWidget):
    """Inline emote autocomplete dropdown.

    Triggered when user types ':' followed by characters in the chat input.
    Shows matching emote names with preview thumbnails.
    """

    emote_completed = Signal(str, int, int)  # emote_name, start_pos, end_pos

    def __init__(self, input_widget: QLineEdit, parent: QWidget | None = None):
        super().__init__(parent)
        self._input = input_widget
        self._emote_map: dict[str, ChatEmote] = {}  # name -> emote
        self._emote_cache: dict[str, QPixmap] = {}
        self._trigger_pos: int = -1  # Position of the ':' trigger
        self._active = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the completer dropdown."""
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(250)

        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setMaximumHeight(300)
        self._list.setStyleSheet("""
            QListWidget {
                background-color: #1a1a2e;
                border: 1px solid #444;
                border-radius: 4px;
                color: #eee;
                font-size: 12px;
                padding: 2px;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-radius: 2px;
            }
            QListWidget::item:selected {
                background-color: #6441a5;
            }
            QListWidget::item:hover {
                background-color: #1f2b4d;
            }
        """)
        self._list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self._list)

    def _connect_signals(self) -> None:
        """Connect to the input widget's signals."""
        self._input.textChanged.connect(self._on_text_changed)

    def set_emotes(self, emote_map: dict[str, ChatEmote]) -> None:
        """Set the available emotes for completion."""
        self._emote_map = emote_map

    def set_emote_cache(self, cache: dict[str, QPixmap]) -> None:
        """Set the shared emote pixmap cache."""
        self._emote_cache = cache

    def handle_key_press(self, key: int) -> bool:
        """Handle key presses from the input widget.

        Returns True if the key was consumed by the completer.
        """
        if not self._active or not self.isVisible():
            return False

        if key == Qt.Key.Key_Tab or key == Qt.Key.Key_Return:
            current = self._list.currentItem()
            if current:
                self._on_item_activated(current)
                return True
        elif key == Qt.Key.Key_Escape:
            self._dismiss()
            return True
        elif key == Qt.Key.Key_Down:
            row = self._list.currentRow()
            if row < self._list.count() - 1:
                self._list.setCurrentRow(row + 1)
            return True
        elif key == Qt.Key.Key_Up:
            row = self._list.currentRow()
            if row > 0:
                self._list.setCurrentRow(row - 1)
            return True

        return False

    def _on_text_changed(self, text: str) -> None:
        """Check for ':' trigger and update suggestions."""
        cursor_pos = self._input.cursorPosition()

        # Find the last ':' before cursor
        trigger_pos = text.rfind(":", 0, cursor_pos)
        if trigger_pos < 0:
            self._dismiss()
            return

        # Check that ':' is at start or preceded by space
        if trigger_pos > 0 and text[trigger_pos - 1] != " ":
            self._dismiss()
            return

        # Get the partial emote name after ':'
        partial = text[trigger_pos + 1 : cursor_pos]

        # Don't trigger on empty or whitespace
        if not partial or " " in partial or len(partial) < MIN_TRIGGER_LENGTH:
            self._dismiss()
            return

        self._trigger_pos = trigger_pos
        self._active = True
        self._update_suggestions(partial)

    def _update_suggestions(self, partial: str) -> None:
        """Update the suggestion list based on partial text."""
        self._list.clear()
        partial_lower = partial.lower()

        matches: list[tuple[str, ChatEmote]] = []
        for name, emote in self._emote_map.items():
            if partial_lower in name.lower():
                matches.append((name, emote))
                if len(matches) >= MAX_SUGGESTIONS:
                    break

        if not matches:
            self._dismiss()
            return

        # Sort: prefix matches first, then alphabetical
        matches.sort(
            key=lambda x: (
                not x[0].lower().startswith(partial_lower),
                x[0].lower(),
            )
        )

        for name, emote in matches:
            item = QListWidgetItem(name)

            # Try to set icon from cache
            cache_key = f"emote:{emote.provider}:{emote.id}"
            pixmap = self._emote_cache.get(cache_key)
            if pixmap and not pixmap.isNull():
                item.setIcon(QIcon(pixmap))

            # Show provider as tooltip
            item.setToolTip(f"[{emote.provider.upper()}] {name}")
            self._list.addItem(item)

        # Select first item
        self._list.setCurrentRow(0)

        # Position and show
        self._position_popup()
        self.show()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle emote selection from the list."""
        emote_name = item.text()
        if self._trigger_pos >= 0:
            cursor_pos = self._input.cursorPosition()
            # Replace from ':' to cursor with the emote name + space
            text = self._input.text()
            new_text = text[: self._trigger_pos] + emote_name + " " + text[cursor_pos:]
            self._input.setText(new_text)
            self._input.setCursorPosition(self._trigger_pos + len(emote_name) + 1)

        self._dismiss()

    def _position_popup(self) -> None:
        """Position the popup above the input widget."""
        from PySide6.QtCore import QPoint

        global_pos = self._input.mapToGlobal(QPoint(0, 0))
        # Show above the input
        height = min(self._list.count() * 28 + 8, 300)
        self.setFixedHeight(height)
        self.move(global_pos.x(), global_pos.y() - height - 4)

    def _dismiss(self) -> None:
        """Hide the completer."""
        self._active = False
        self._trigger_pos = -1
        self.hide()
