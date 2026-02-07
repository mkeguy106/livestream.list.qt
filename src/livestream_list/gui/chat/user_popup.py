"""User context menu and info popup for chat."""

import logging
import webbrowser

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from ...chat.models import ChatUser
from ...core.models import StreamPlatform
from ...core.settings import BuiltinChatSettings

logger = logging.getLogger(__name__)

# Platform channel URL templates
CHANNEL_URLS = {
    StreamPlatform.TWITCH: "https://twitch.tv/{name}",
    StreamPlatform.YOUTUBE: "https://youtube.com/channel/{id}",
    StreamPlatform.KICK: "https://kick.com/{name}",
}


class UserContextMenu(QMenu):
    """Right-click context menu for chat usernames.

    Provides user info, block/unblock, nickname, notes, and open channel actions.
    """

    user_blocked = Signal(str)  # "platform:user_id"
    user_unblocked = Signal(str)  # "platform:user_id"
    nickname_changed = Signal(str, str)  # user_key, nickname (empty = cleared)
    note_changed = Signal(str, str)  # user_key, note (empty = cleared)

    def __init__(
        self,
        user: ChatUser,
        settings: BuiltinChatSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.user = user
        self.settings = settings
        self._build_menu()

    def _build_menu(self) -> None:
        """Build the context menu actions."""
        user = self.user
        user_key = f"{user.platform.value}:{user.id}"

        # Header: display name + platform
        header = self.addAction(f"{user.display_name} ({user.platform.value})")
        header.setEnabled(False)
        header_font = header.font()
        header_font.setBold(True)
        header.setFont(header_font)

        # Badges info
        if user.badges:
            badge_names = ", ".join(b.name for b in user.badges)
            badge_action = self.addAction(f"Badges: {badge_names}")
            badge_action.setEnabled(False)

        # Show existing note in header area (italic, disabled)
        existing_note = self.settings.user_notes.get(user_key, "")
        if existing_note:
            note_display = self.addAction(f'Note: "{existing_note}"')
            note_display.setEnabled(False)
            note_font = QFont(note_display.font())
            note_font.setItalic(True)
            note_display.setFont(note_font)

        self.addSeparator()

        # Block/Unblock
        is_blocked = user_key in self.settings.blocked_users
        if is_blocked:
            unblock = self.addAction("Unblock User")
            unblock.triggered.connect(lambda: self._unblock_user(user_key))
        else:
            block = self.addAction("Block User")
            block.triggered.connect(lambda: self._block_user(user_key))

        # Nickname actions
        existing_nick = self.settings.user_nicknames.get(user_key, "")
        if existing_nick:
            edit_nick = self.addAction(f"Edit Nickname ({existing_nick})")
            edit_nick.triggered.connect(lambda: self._set_nickname(user_key))
            clear_nick = self.addAction("Clear Nickname")
            clear_nick.triggered.connect(lambda: self._clear_nickname(user_key))
        else:
            set_nick = self.addAction("Set Nickname")
            set_nick.triggered.connect(lambda: self._set_nickname(user_key))

        # Note actions
        if existing_note:
            edit_note = self.addAction("Edit Note")
            edit_note.triggered.connect(lambda: self._set_note(user_key))
            remove_note = self.addAction("Remove Note")
            remove_note.triggered.connect(lambda: self._remove_note(user_key))
        else:
            add_note = self.addAction("Add Note")
            add_note.triggered.connect(lambda: self._set_note(user_key))

        self.addSeparator()

        # Open channel
        open_channel = self.addAction("Open Channel")
        open_channel.triggered.connect(self._open_channel)

    def _block_user(self, user_key: str) -> None:
        """Block a user."""
        if user_key not in self.settings.blocked_users:
            self.settings.blocked_users.append(user_key)
            self.settings.blocked_user_names[user_key] = self.user.display_name
            self.user_blocked.emit(user_key)
            logger.info(f"Blocked user: {user_key}")

    def _unblock_user(self, user_key: str) -> None:
        """Unblock a user."""
        if user_key in self.settings.blocked_users:
            self.settings.blocked_users.remove(user_key)
            self.settings.blocked_user_names.pop(user_key, None)
            self.user_unblocked.emit(user_key)
            logger.info(f"Unblocked user: {user_key}")

    def _set_nickname(self, user_key: str) -> None:
        """Set or edit a nickname for the user."""
        current = self.settings.user_nicknames.get(user_key, "")
        text, ok = QInputDialog.getText(
            self, "Set Nickname", f"Nickname for {self.user.display_name}:", text=current
        )
        if ok and text.strip():
            self.settings.user_nicknames[user_key] = text.strip()
            self.settings.user_nickname_display_names[user_key] = self.user.display_name
            self.nickname_changed.emit(user_key, text.strip())

    def _clear_nickname(self, user_key: str) -> None:
        """Remove the nickname for the user."""
        self.settings.user_nicknames.pop(user_key, None)
        self.settings.user_nickname_display_names.pop(user_key, None)
        self.nickname_changed.emit(user_key, "")

    def _set_note(self, user_key: str) -> None:
        """Set or edit a note for the user."""
        current = self.settings.user_notes.get(user_key, "")
        text, ok = QInputDialog.getText(
            self, "User Note", f"Note for {self.user.display_name}:", text=current
        )
        if ok and text.strip():
            self.settings.user_notes[user_key] = text.strip()
            self.settings.user_note_display_names[user_key] = self.user.display_name
            self.note_changed.emit(user_key, text.strip())

    def _remove_note(self, user_key: str) -> None:
        """Remove the note for the user."""
        self.settings.user_notes.pop(user_key, None)
        self.settings.user_note_display_names.pop(user_key, None)
        self.note_changed.emit(user_key, "")

    def _open_channel(self) -> None:
        """Open the user's channel in the browser."""
        url_template = CHANNEL_URLS.get(self.user.platform)
        if url_template:
            url = url_template.format(name=self.user.name, id=self.user.id)
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.error(f"Failed to open channel URL: {e}")
