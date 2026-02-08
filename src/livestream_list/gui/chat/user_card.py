"""User card popup showing user info, badges, and actions."""

import logging
import webbrowser

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...chat.emotes.cache import EmoteCache
from ...chat.models import ChatUser
from ...core.models import StreamPlatform
from ...core.settings import BuiltinChatSettings
from ..theme import get_theme

logger = logging.getLogger(__name__)

CHANNEL_URLS = {
    StreamPlatform.TWITCH: "https://twitch.tv/{name}",
    StreamPlatform.YOUTUBE: "https://youtube.com/channel/{id}",
    StreamPlatform.KICK: "https://kick.com/{name}",
}


class UserCardFetchWorker:
    """Fetches user card info asynchronously using a Twitch API client.

    This is a lightweight helper — the actual QThread is managed by the caller
    (ChatWidget already has an event loop pattern for async work).
    """

    @staticmethod
    async def fetch_twitch_user_info(api_client, login: str) -> dict | None:
        """Fetch Twitch user card info via GraphQL."""
        query = """
        query GetUserCard($login: String!) {
            user(login: $login) {
                id
                login
                displayName
                createdAt
                profileImageURL(width: 70)
            }
        }
        """
        try:
            async with api_client.session.post(
                api_client.GQL_URL,
                headers=api_client._get_gql_headers(),
                json={
                    "query": query,
                    "variables": {"login": login},
                },
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                user_data = data.get("data", {}).get("user")
                if not user_data:
                    return None
                return {
                    "created_at": user_data.get("createdAt", ""),
                    "profile_image_url": user_data.get("profileImageURL", ""),
                    "display_name": user_data.get("displayName", ""),
                }
        except Exception as e:
            logger.debug(f"Failed to fetch user card info: {e}")
            return None


class UserCardPopup(QFrame):
    """Popup showing user info when clicking a username in chat."""

    history_requested = Signal()
    channel_requested = Signal()

    def __init__(
        self,
        user: ChatUser,
        message_count: int,
        settings: BuiltinChatSettings,
        image_store: EmoteCache | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._user = user
        self._settings = settings
        self._image_store = image_store
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(280)

        theme = get_theme()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.popup_bg};
                border: 1px solid {theme.border_light};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        user_key = f"{user.platform.value}:{user.id}"

        # Header: display name + platform
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)

        user_color = user.color or theme.text_primary
        name_label = QLabel(user.display_name)
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(13)
        name_label.setFont(name_font)
        name_label.setStyleSheet(
            f"color: {user_color}; background: transparent; border: none;"
        )
        header_layout.addWidget(name_label)

        # Nickname indicator
        nickname = settings.user_nicknames.get(user_key, "")
        if nickname:
            nick_label = QLabel(f'("{nickname}")')
            nick_label.setStyleSheet(
                f"color: {theme.text_muted}; font-style: italic;"
                " background: transparent; border: none;"
            )
            header_layout.addWidget(nick_label)

        header_layout.addStretch()

        platform_names = {
            StreamPlatform.TWITCH: "Twitch",
            StreamPlatform.YOUTUBE: "YouTube",
            StreamPlatform.KICK: "Kick",
        }
        platform_label = QLabel(platform_names.get(user.platform, ""))
        platform_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px;"
            " background: transparent; border: none;"
        )
        header_layout.addWidget(platform_label)
        layout.addLayout(header_layout)

        # @username (login name if different from display name)
        if user.name.lower() != user.display_name.lower():
            login_label = QLabel(f"@{user.name}")
            login_label.setStyleSheet(
                f"color: {theme.text_muted}; font-size: 11px;"
                " background: transparent; border: none;"
            )
            layout.addWidget(login_label)

        # Badges row
        if user.badges:
            badges_layout = QHBoxLayout()
            badges_layout.setSpacing(3)
            for badge in user.badges:
                badge_label = QLabel()
                badge_label.setFixedSize(18, 18)
                badge_label.setStyleSheet("background: transparent; border: none;")
                badge_label.setToolTip(badge.title or badge.name)
                if image_store and badge.image_set:
                    image_set = badge.image_set.bind(image_store)
                    badge.image_set = image_set
                    try:
                        scale = float(self.devicePixelRatioF())
                    except Exception:
                        scale = 1.0
                    image_ref = image_set.get_image_or_loaded(scale=scale)
                    if image_ref:
                        pixmap = image_ref.pixmap_or_load()
                        if pixmap and not pixmap.isNull():
                            badge_label.setPixmap(
                                pixmap.scaled(
                                    18, 18,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            )
                badges_layout.addWidget(badge_label)
            badges_layout.addStretch()
            layout.addLayout(badges_layout)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.border_light}; background: transparent;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Info section
        info_style = (
            f"color: {theme.text_muted}; font-size: 11px;"
            " background: transparent; border: none;"
        )

        # Account created (Twitch only, loaded async)
        if user.platform == StreamPlatform.TWITCH:
            self._created_label = QLabel("Account created: Loading...")
            self._created_label.setStyleSheet(info_style)
            layout.addWidget(self._created_label)
        else:
            self._created_label = None

        # Session message count
        count_label = QLabel(f"Session messages: {message_count}")
        count_label.setStyleSheet(info_style)
        layout.addWidget(count_label)

        # Note
        note = settings.user_notes.get(user_key, "")
        if note:
            note_label = QLabel(f'Note: "{note}"')
            note_label.setStyleSheet(
                f"color: {theme.text_muted}; font-size: 11px; font-style: italic;"
                " background: transparent; border: none;"
            )
            note_label.setWordWrap(True)
            layout.addWidget(note_label)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        btn_style = f"""
            QPushButton {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_primary};
                border: 1px solid {theme.border_light};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {theme.popup_hover};
                border-color: {theme.accent};
            }}
        """

        history_btn = QPushButton("Chat History")
        history_btn.setStyleSheet(btn_style)
        history_btn.clicked.connect(self._on_history)
        btn_layout.addWidget(history_btn)

        channel_btn = QPushButton("Open Channel")
        channel_btn.setStyleSheet(btn_style)
        channel_btn.clicked.connect(self._on_open_channel)
        btn_layout.addWidget(channel_btn)

        layout.addLayout(btn_layout)

    def update_created_at(self, created_at_str: str) -> None:
        """Update the account creation date label after async fetch."""
        if self._created_label and created_at_str:
            # Parse ISO format: "2015-06-21T15:40:00.000Z"
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                formatted = dt.astimezone().strftime("%B %d, %Y")
                self._created_label.setText(f"Account created: {formatted}")
            except Exception:
                self._created_label.setText(f"Account created: {created_at_str}")
        elif self._created_label:
            self._created_label.setText("Account created: Unknown")

    def show_at(self, pos: QPoint) -> None:
        """Show the popup near the given global position."""
        self.adjustSize()
        # Ensure popup stays on screen
        screen = self.screen()
        if screen:
            screen_rect = screen.availableGeometry()
            x = min(pos.x(), screen_rect.right() - self.width())
            y = pos.y() - self.height() - 5
            if y < screen_rect.top():
                y = pos.y() + 20
            self.move(x, y)
        else:
            self.move(pos)
        self.show()

    def _on_history(self) -> None:
        """Request chat history dialog."""
        self.history_requested.emit()
        self.hide()

    def _on_open_channel(self) -> None:
        """Open the user's channel in browser."""
        url_template = CHANNEL_URLS.get(self._user.platform)
        if url_template:
            url = url_template.format(name=self._user.name, id=self._user.id)
            try:
                webbrowser.open(url)
            except Exception as e:
                logger.error(f"Failed to open channel URL: {e}")
        self.hide()
