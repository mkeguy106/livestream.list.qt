"""User context menu and info popup for chat."""

import logging
import webbrowser

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QWidget

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

    Provides user info, block/unblock, and open channel actions.
    """

    user_blocked = Signal(str)  # "platform:user_id"
    user_unblocked = Signal(str)  # "platform:user_id"

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

        self.addSeparator()

        # Block/Unblock
        is_blocked = user_key in self.settings.blocked_users
        if is_blocked:
            unblock = self.addAction("Unblock User")
            unblock.triggered.connect(lambda: self._unblock_user(user_key))
        else:
            block = self.addAction("Block User")
            block.triggered.connect(lambda: self._block_user(user_key))

        self.addSeparator()

        # Open channel
        open_channel = self.addAction("Open Channel")
        open_channel.triggered.connect(self._open_channel)

    def _block_user(self, user_key: str) -> None:
        """Block a user."""
        if user_key not in self.settings.blocked_users:
            self.settings.blocked_users.append(user_key)
            self.user_blocked.emit(user_key)
            logger.info(f"Blocked user: {user_key}")

    def _unblock_user(self, user_key: str) -> None:
        """Unblock a user."""
        if user_key in self.settings.blocked_users:
            self.settings.blocked_users.remove(user_key)
            self.user_unblocked.emit(user_key)
            logger.info(f"Unblocked user: {user_key}")

    def _open_channel(self) -> None:
        """Open the user's channel in the browser."""
        url_template = CHANNEL_URLS.get(self.user.platform)
        if url_template:
            url = url_template.format(name=self.user.name, id=self.user.id)
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.error(f"Failed to open channel URL: {e}")
