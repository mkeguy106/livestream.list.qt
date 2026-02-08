"""Mixin class providing chat search functionality.

This mixin can be used by any widget that displays chat messages
and needs search capability. The host widget must provide:
- _search_widget: QWidget (the search bar container)
- _search_input: QLineEdit (the search input field)
- _search_count_label: QLabel (shows match count)
- _search_matches: list[int] (stores matching row indices)
- _search_current: int (current match index, -1 if none)
- _model: QAbstractItemModel (the message model)
- _list_view: QAbstractItemView (the list view widget)
"""

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView

from ...chat.models import ChatMessage
from .message_model import MessageRole

# Regex to extract key:value predicates from a search query
_PREDICATE_RE = re.compile(r"(from|has|is):(\S+)")


class ChatSearchMixin:
    """Mixin providing search functionality for chat message widgets.

    Usage:
        class MyChatWidget(QWidget, ChatSearchMixin):
            def __init__(self):
                # Set up required attributes:
                self._search_matches: list[int] = []
                self._search_current: int = -1
                # ... create _search_widget, _search_input, etc.

                # Connect signals:
                self._search_input.textChanged.connect(self._on_search_text_changed)
                self._search_input.returnPressed.connect(self._search_next)
    """

    # These attributes must be provided by the host widget
    _search_widget: "QWidget"  # noqa: F821
    _search_input: "QLineEdit"  # noqa: F821
    _search_count_label: "QLabel"  # noqa: F821
    _search_matches: list[int]
    _search_current: int
    _model: "QAbstractItemModel"  # noqa: F821
    _list_view: QAbstractItemView

    def _toggle_search(self) -> None:
        """Toggle the search bar visibility."""
        if self._search_widget.isVisible():
            self._close_search()
        else:
            self._search_widget.show()
            self._search_input.setFocus()
            self._search_input.selectAll()

    def _close_search(self) -> None:
        """Hide the search bar and clear state."""
        self._search_widget.hide()
        self._search_input.clear()
        self._search_matches.clear()
        self._search_current = -1
        self._search_count_label.setText("")

    @staticmethod
    def _parse_query(text: str) -> tuple[str, dict[str, str]]:
        """Parse a search query into plain text and predicates.

        Extracts key:value tokens like from:user, has:link, is:sub.
        Returns (remaining_plain_text, {key: value}).
        """
        predicates: dict[str, str] = {}
        for match in _PREDICATE_RE.finditer(text):
            predicates[match.group(1).lower()] = match.group(2).lower()
        plain = _PREDICATE_RE.sub("", text).strip()
        return plain, predicates

    @staticmethod
    def _matches_query(msg: ChatMessage, plain_text: str, predicates: dict[str, str]) -> bool:
        """Check if a message matches the parsed query (all predicates AND plain text)."""
        # Check predicates
        for key, value in predicates.items():
            if key == "from":
                if value not in msg.user.display_name.lower():
                    return False
            elif key == "has":
                if value == "link":
                    if "http://" not in msg.text and "https://" not in msg.text:
                        return False
                elif value == "emote":
                    if not msg.emote_positions:
                        return False
                else:
                    return False
            elif key == "is":
                if value == "sub":
                    if not any("subscriber" in b.name.lower() for b in msg.user.badges):
                        return False
                elif value == "mod":
                    if not any("moderator" in b.name.lower() for b in msg.user.badges):
                        return False
                elif value == "first":
                    if not msg.is_first_message:
                        return False
                elif value == "system":
                    if not msg.is_system:
                        return False
                elif value == "action":
                    if not msg.is_action:
                        return False
                elif value == "hype":
                    if not msg.is_hype_chat:
                        return False
                else:
                    return False
            else:
                return False

        # Check plain text (match against display name or message text)
        if plain_text:
            query = plain_text.lower()
            if query not in msg.user.display_name.lower() and query not in msg.text.lower():
                return False

        return True

    def _on_search_text_changed(self, text: str) -> None:
        """Update search matches when query changes."""
        self._search_matches.clear()
        self._search_current = -1

        if not text:
            self._search_count_label.setText("")
            return

        plain_text, predicates = self._parse_query(text)

        # If no predicates and no plain text, nothing to search
        if not plain_text and not predicates:
            self._search_count_label.setText("")
            return

        for row in range(self._model.rowCount()):
            index = self._model.index(row, 0)
            msg = index.data(MessageRole)
            if not msg or not isinstance(msg, ChatMessage):
                continue
            if self._matches_query(msg, plain_text, predicates):
                self._search_matches.append(row)

        if self._search_matches:
            # Start at the most recent match
            self._search_current = len(self._search_matches) - 1
            self._scroll_to_search_match()
        else:
            self._search_count_label.setText("No matches")

    def _search_next(self) -> None:
        """Navigate to the next search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current + 1) % len(self._search_matches)
        self._scroll_to_search_match()

    def _search_prev(self) -> None:
        """Navigate to the previous search match."""
        if not self._search_matches:
            return
        self._search_current = (self._search_current - 1) % len(self._search_matches)
        self._scroll_to_search_match()

    def _scroll_to_search_match(self) -> None:
        """Scroll to the current search match and update the count label."""
        if not self._search_matches or self._search_current < 0:
            return
        row = self._search_matches[self._search_current]
        index = self._model.index(row, 0)
        self._list_view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._list_view.setCurrentIndex(index)
        total = len(self._search_matches)
        current = self._search_current + 1
        self._search_count_label.setText(f"{current}/{total}")

    def _handle_search_key_press(self, key: int) -> bool:
        """Handle key press for search - returns True if handled.

        Call this from keyPressEvent:
            if self._handle_search_key_press(event.key()):
                return
        """
        if key == Qt.Key.Key_Escape and self._search_widget.isVisible():
            self._close_search()
            return True
        return False
