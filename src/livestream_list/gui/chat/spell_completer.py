"""Spellcheck completer for chat input - correction popup on click."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...chat.spellcheck.checker import SpellChecker
from ..theme import get_theme

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 5
ADD_TO_DICT_TEXT = "+ Add to dictionary"


class SpellCompleter(QWidget):
    """Spellcheck correction popup.

    Third completer in the chain (lowest priority). Shows correction
    suggestions on left-click of a misspelled word.
    """

    def __init__(
        self,
        input_widget: QLineEdit,
        spell_checker: SpellChecker,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._input = input_widget
        self._checker = spell_checker
        self._active = False
        self._word_start: int = -1
        self._word_end: int = -1
        self._original_word: str = ""

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the completer dropdown as a child widget."""
        self.setFixedWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setMaximumHeight(200)
        self._apply_list_style()
        self._list.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self._list)

    def _apply_list_style(self) -> None:
        """Apply theme-aware stylesheet to the list widget."""
        theme = get_theme()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background-color: {theme.popup_bg};
                border: 1px solid {theme.popup_border};
                border-radius: 4px;
                color: {theme.text_primary};
                font-size: 12px;
                padding: 2px;
            }}
            QListWidget::item {{
                padding: 4px 8px;
                border-radius: 2px;
            }}
            QListWidget::item:selected {{
                background-color: {theme.popup_selected};
            }}
            QListWidget::item:hover {{
                background-color: {theme.popup_hover};
            }}
        """)

    def apply_theme(self) -> None:
        """Apply theme colors to the completer."""
        self._apply_list_style()

    def handle_key_press(self, key: int) -> bool:
        """Handle key presses from the input widget.

        When popup is visible: Return selects, Escape dismisses, Up/Down navigate.
        """
        if not (self._active and self.isVisible()):
            return False

        if key == Qt.Key.Key_Return:
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

    def show_suggestions_at_word(self, start: int, end: int) -> None:
        """Show correction popup for a misspelled word at the given range."""
        text = self._input.text()
        if start < 0 or end > len(text):
            return

        word = text[start:end]
        suggestions = self._checker.get_suggestions(word, max_count=MAX_SUGGESTIONS)

        self._list.clear()
        self._word_start = start
        self._word_end = end
        self._original_word = word

        if not suggestions and not word:
            return

        for suggestion in suggestions:
            display = self._match_case(word, suggestion)
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, display)
            self._list.addItem(item)

        # "Add to dictionary" option
        add_item = QListWidgetItem(ADD_TO_DICT_TEXT)
        add_item.setData(Qt.ItemDataRole.UserRole, ADD_TO_DICT_TEXT)
        add_item.setForeground(Qt.GlobalColor.darkGray)
        self._list.addItem(add_item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        self._active = True
        self._position_popup()
        self.show()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle selection from the suggestion list."""
        value = item.data(Qt.ItemDataRole.UserRole)

        if value == ADD_TO_DICT_TEXT:
            # Add the original word to user dictionary
            self._checker.dictionary.add_user_word(self._original_word)
            self._dismiss()
            # Trigger recheck by faking a text change
            self._input.textChanged.emit(self._input.text())
            return

        # Replace the misspelled word with the correction
        if self._word_start >= 0 and value:
            text = self._input.text()
            new_text = text[: self._word_start] + value + text[self._word_end :]
            new_cursor = self._word_start + len(value)
            self._input.setText(new_text)
            self._input.setCursorPosition(new_cursor)

        self._dismiss()
        self._input.setFocus()

    def _position_popup(self) -> None:
        """Position the popup above the input widget."""
        height = min(self._list.count() * 28 + 8, 200)
        self.setFixedHeight(height)

        if self.parent():
            input_pos = self._input.mapTo(self.parent(), self._input.rect().topLeft())
            self.move(input_pos.x(), input_pos.y() - height - 4)
            self.raise_()

    def _dismiss(self) -> None:
        """Hide the completer."""
        self._active = False
        self._word_start = -1
        self._word_end = -1
        self._original_word = ""
        self.hide()

    @staticmethod
    def _match_case(original: str, replacement: str) -> str:
        """Match the casing of the original word to the replacement."""
        if original.isupper():
            return replacement.upper()
        if original and original[0].isupper():
            return replacement.capitalize()
        return replacement
