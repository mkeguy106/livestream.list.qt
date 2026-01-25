"""Inline @mention autocomplete triggered by '@'."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 15
MIN_TRIGGER_LENGTH = 1  # Minimum chars after '@' to start suggesting


class MentionCompleter(QWidget):
    """Inline @mention autocomplete dropdown.

    Triggered when user types '@' followed by characters in the chat input.
    Shows matching usernames from recent chat messages.
    """

    mention_completed = Signal(str, int, int)  # username, start_pos, end_pos

    def __init__(self, input_widget: QLineEdit, parent: QWidget | None = None):
        super().__init__(parent)
        self._input = input_widget
        self._usernames: dict[str, str] = {}  # display_name_lower -> display_name
        self._trigger_pos: int = -1  # Position of the '@' trigger
        self._active = False

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the completer dropdown as a child widget (not a window)."""
        # No window flags - this is a child widget, not a separate window
        self.setFixedWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._list.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self._list)

    def _connect_signals(self) -> None:
        """Connect to the input widget's signals."""
        self._input.textChanged.connect(self._on_text_changed)

    def add_username(self, display_name: str) -> None:
        """Add a username to the completion list."""
        if display_name:
            self._usernames[display_name.lower()] = display_name

    def clear_usernames(self) -> None:
        """Clear all tracked usernames."""
        self._usernames.clear()

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
        """Check for '@' trigger and update suggestions."""
        cursor_pos = self._input.cursorPosition()

        # Find the last '@' before cursor
        trigger_pos = text.rfind("@", 0, cursor_pos)
        if trigger_pos < 0:
            self._dismiss()
            return

        # Check that '@' is at start or preceded by space
        if trigger_pos > 0 and text[trigger_pos - 1] != " ":
            self._dismiss()
            return

        # Get the partial username after '@'
        partial = text[trigger_pos + 1 : cursor_pos]

        # Don't trigger on empty or whitespace
        if " " in partial or len(partial) < MIN_TRIGGER_LENGTH:
            self._dismiss()
            return

        self._trigger_pos = trigger_pos
        self._active = True
        self._update_suggestions(partial)

    def _update_suggestions(self, partial: str) -> None:
        """Update the suggestion list based on partial text."""
        self._list.clear()
        partial_lower = partial.lower()

        matches: list[str] = []
        for name_lower, name in self._usernames.items():
            if partial_lower in name_lower:
                matches.append(name)
                if len(matches) >= MAX_SUGGESTIONS:
                    break

        if not matches:
            self._dismiss()
            return

        # Sort: prefix matches first, then alphabetical
        matches.sort(
            key=lambda x: (
                not x.lower().startswith(partial_lower),
                x.lower(),
            )
        )

        for name in matches:
            item = QListWidgetItem(name)
            self._list.addItem(item)

        # Select first item
        self._list.setCurrentRow(0)

        # Position and show
        self._position_popup()
        self.show()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle username selection from the list."""
        username = item.text()
        if self._trigger_pos >= 0:
            cursor_pos = self._input.cursorPosition()
            trigger_pos = self._trigger_pos  # Save before setText triggers textChanged
            # Replace from '@' to cursor with @username + space
            text = self._input.text()
            new_text = text[:trigger_pos] + "@" + username + " " + text[cursor_pos:]
            self._input.setText(new_text)
            self._input.setCursorPosition(trigger_pos + len(username) + 2)

        self._dismiss()
        # Restore focus to input after mouse selection
        self._input.setFocus()

    def _position_popup(self) -> None:
        """Position the popup above the input widget."""
        # Calculate height based on items
        height = min(self._list.count() * 28 + 8, 300)
        self.setFixedHeight(height)

        # Position above the input, within the parent widget
        if self.parent():
            input_pos = self._input.mapTo(self.parent(), self._input.rect().topLeft())
            self.move(input_pos.x(), input_pos.y() - height - 4)
            self.raise_()  # Bring to front

    def _dismiss(self) -> None:
        """Hide the completer."""
        self._active = False
        self._trigger_pos = -1
        self.hide()
