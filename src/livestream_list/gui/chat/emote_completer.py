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

from ...chat.emotes.cache import EmoteCache
from ...chat.models import ChatEmote
from ..theme import get_theme

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 15
MIN_TRIGGER_LENGTH = 1  # Minimum chars after ':' to start suggesting

# 3rd party emote providers that should show for all platforms
THIRD_PARTY_PROVIDERS = {"7tv", "bttv", "ffz"}


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
        self._image_store: EmoteCache | None = None
        self._trigger_pos: int = -1  # Position of the ':' trigger
        self._active = False
        self._platform: str = ""  # Current platform (twitch, kick, youtube)

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the completer dropdown as a child widget (not a window)."""
        # No window flags - this is a child widget, not a separate window
        self.setFixedWidth(250)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setMaximumHeight(300)
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

    def _connect_signals(self) -> None:
        """Connect to the input widget's signals."""
        self._input.textChanged.connect(self._on_text_changed)

    def set_emotes(self, emote_map: dict[str, ChatEmote]) -> None:
        """Set the available emotes for completion."""
        self._emote_map = emote_map

    def set_image_store(self, store: EmoteCache) -> None:
        """Set the shared image store."""
        self._image_store = store

    def set_platform(self, platform: str) -> None:
        """Set the current platform for filtering emotes.

        Args:
            platform: Platform name (twitch, kick, youtube)
        """
        self._platform = platform.lower()

    def _current_scale(self) -> float:
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            return 1.0

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

        # Collect matches, filtering by platform
        # Show emotes from current platform + 3rd party providers (7tv, bttv, ffz)
        matches: list[tuple[str, ChatEmote]] = []
        for name, emote in self._emote_map.items():
            if partial_lower not in name.lower():
                continue
            # Filter: show 3rd party emotes for all platforms,
            # or platform-specific emotes only for that platform
            if emote.provider in THIRD_PARTY_PROVIDERS or emote.provider == self._platform:
                matches.append((name, emote))

        if not matches:
            self._dismiss()
            return

        # Sort: prefix matches first, then by length (shorter first), then alphabetical
        matches.sort(
            key=lambda x: (
                not x[0].lower().startswith(partial_lower),
                len(x[0]),  # Shorter names first
                x[0].lower(),
            )
        )

        # Take top MAX_SUGGESTIONS after sorting
        matches = matches[:MAX_SUGGESTIONS]

        for name, emote in matches:
            # Format provider label
            provider_labels = {
                "twitch": "Twitch",
                "kick": "Kick",
                "7tv": "7TV",
                "bttv": "BTTV",
                "ffz": "FFZ",
            }
            provider_label = provider_labels.get(emote.provider, emote.provider.upper())

            # Display name with provider suffix
            item = QListWidgetItem(f"{name}  [{provider_label}]")
            # Store original name for completion
            item.setData(Qt.ItemDataRole.UserRole, name)

            # Try to set icon from image set
            if self._image_store and emote.image_set:
                image_set = emote.image_set.bind(self._image_store)
                emote.image_set = image_set
                image_ref = image_set.get_image_or_loaded(scale=self._current_scale())
                if image_ref:
                    pixmap = image_ref.pixmap_or_load()
                    if pixmap and not pixmap.isNull():
                        item.setIcon(QIcon(pixmap))

            self._list.addItem(item)

        # Select first item
        self._list.setCurrentRow(0)

        # Position and show
        self._position_popup()
        self.show()

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        """Handle emote selection from the list."""
        # Get original emote name (stored in UserRole, fallback to text)
        emote_name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        if self._trigger_pos >= 0:
            cursor_pos = self._input.cursorPosition()
            trigger_pos = self._trigger_pos  # Save before setText triggers textChanged
            # Replace from ':' to cursor with the emote name + space
            text = self._input.text()
            new_text = text[:trigger_pos] + emote_name + " " + text[cursor_pos:]
            self._input.setText(new_text)
            self._input.setCursorPosition(trigger_pos + len(emote_name) + 1)

        self._dismiss()

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
