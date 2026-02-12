"""User card popup showing user info, badges, and actions."""

import json
import logging
import re
import webbrowser

import aiohttp
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
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

# Pronoun ID → display text mapping (from pronouns.alejo.io)
PRONOUN_MAP: dict[str, str] = {
    "hehim": "He/Him",
    "sheher": "She/Her",
    "theythem": "They/Them",
    "hethem": "He/They",
    "shethem": "She/They",
    "aeaer": "Ae/Aer",
    "itits": "It/Its",
    "other": "Other",
    "perper": "Per/Per",
    "vever": "Ve/Ver",
    "xexem": "Xe/Xem",
    "ziehir": "Zie/Hir",
    "heshe": "He/She",
    "anyall": "Any/All",
}

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

    GQL_URL = "https://gql.twitch.tv/gql"
    GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

    @staticmethod
    async def fetch_twitch_user_info(login: str, channel_login: str = "") -> dict | None:
        """Fetch Twitch user card info via GraphQL.

        Uses its own aiohttp session and static GQL credentials so it can
        run from any thread without depending on the main API client.
        If channel_login is provided, also fetches follow age for that channel.
        """
        headers = {
            "Client-ID": UserCardFetchWorker.GQL_CLIENT_ID,
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: Fetch user info (+ channel numeric ID if needed)
                if channel_login and channel_login.lower() != login.lower():
                    query = """
                    query GetUserCard($login: String!, $channelLogin: String!) {
                        user(login: $login) {
                            id
                            login
                            displayName
                            createdAt
                            description
                            profileImageURL(width: 70)
                            followers { totalCount }
                        }
                        channel: user(login: $channelLogin) { id }
                    }
                    """
                    variables = {"login": login, "channelLogin": channel_login}
                else:
                    query = """
                    query GetUserCard($login: String!) {
                        user(login: $login) {
                            id
                            login
                            displayName
                            createdAt
                            description
                            profileImageURL(width: 70)
                            followers { totalCount }
                        }
                    }
                    """
                    variables = {"login": login}

                async with session.post(
                    UserCardFetchWorker.GQL_URL,
                    headers=headers,
                    json={"query": query, "variables": variables},
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

                user_data = data.get("data", {}).get("user")
                if not user_data:
                    return None

                result = {
                    "created_at": user_data.get("createdAt", ""),
                    "profile_image_url": user_data.get("profileImageURL", ""),
                    "display_name": user_data.get("displayName", ""),
                    "description": user_data.get("description") or "",
                    "follower_count": (user_data.get("followers", {}).get("totalCount", 0)),
                    "followed_at": "",
                }

                # Step 2: Fetch follow relationship if we got the channel ID
                channel_data = data.get("data", {}).get("channel")
                if channel_data and channel_data.get("id"):
                    channel_id = channel_data["id"]
                    follow_query = """
                    query GetFollowAge($login: String!, $targetId: ID!) {
                        user(login: $login) {
                            follow(targetID: $targetId) { followedAt }
                        }
                    }
                    """
                    async with session.post(
                        UserCardFetchWorker.GQL_URL,
                        headers=headers,
                        json={
                            "query": follow_query,
                            "variables": {
                                "login": login,
                                "targetId": channel_id,
                            },
                        },
                    ) as resp2:
                        if resp2.status == 200:
                            fdata = await resp2.json()
                            follow = fdata.get("data", {}).get("user", {}).get("follow")
                            if follow:
                                result["followed_at"] = follow.get("followedAt", "")

                return result
        except Exception as e:
            logger.debug(f"Failed to fetch user card info: {e}")
            return None

    @staticmethod
    async def fetch_avatar(url: str) -> bytes:
        """Download profile image bytes from URL."""
        if not url:
            return b""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception as e:
            logger.debug(f"Failed to fetch avatar: {e}")
        return b""

    @staticmethod
    async def fetch_pronouns(login: str) -> str:
        """Fetch user pronouns from pronouns.alejo.io API."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://pronouns.alejo.io/api/users/{login}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        pronoun_id = data[0].get("pronoun_id", "")
                        return PRONOUN_MAP.get(pronoun_id, pronoun_id)
        except Exception as e:
            logger.debug(f"Failed to fetch pronouns for {login}: {e}")
        return ""

    @staticmethod
    async def fetch_kick_user_info(slug: str) -> dict | None:
        """Fetch Kick user info from the public channel API.

        Returns dict with: bio, followers_count, verified, profile_pic_url, country.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    f"https://kick.com/api/v2/channels/{slug}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            user_data = data.get("user", {})
            return {
                "bio": user_data.get("bio") or "",
                "followers_count": data.get("followers_count", 0),
                "verified": data.get("verified", False),
                "profile_pic_url": user_data.get("profile_pic") or "",
                "country": user_data.get("country") or "",
            }
        except Exception as e:
            logger.debug(f"Failed to fetch Kick user info for {slug}: {e}")
            return None

    @staticmethod
    async def fetch_youtube_user_info(channel_id: str) -> dict | None:
        """Fetch YouTube channel info by scraping the /about page.

        Returns dict with: description, subscriber_count_text, joined_date_text,
        country, avatar_url. Uses the same ytInitialData technique as SocialsFetchWorker.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/about"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}/about"
        else:
            url = f"https://www.youtube.com/@{channel_id}/about"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()

            match = re.search(
                r"var ytInitialData\s*=\s*({.+?});</script>", html, re.DOTALL
            )
            if not match:
                return None
            data = json.loads(match.group(1))

            result: dict[str, str] = {
                "description": "",
                "subscriber_count_text": "",
                "joined_date_text": "",
                "country": "",
                "avatar_url": "",
            }

            # Avatar from channelMetadataRenderer
            metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
            thumbs = metadata.get("avatar", {}).get("thumbnails", [])
            if thumbs:
                result["avatar_url"] = thumbs[-1].get("url", "")

            # About info from aboutChannelViewModel
            about = None
            for endpoint in data.get("onResponseReceivedEndpoints", []):
                panel = (
                    endpoint.get("showEngagementPanelEndpoint", {})
                    .get("engagementPanel", {})
                    .get("engagementPanelSectionListRenderer", {})
                    .get("content", {})
                    .get("sectionListRenderer", {})
                    .get("contents", [])
                )
                for section in panel:
                    about = (
                        section.get("itemSectionRenderer", {})
                        .get("contents", [{}])[0]
                        .get("aboutChannelRenderer", {})
                        .get("metadata", {})
                        .get("aboutChannelViewModel", {})
                    )
                    if about:
                        break
                if about:
                    break

            # Fallback: try tabs path (older layout)
            if not about:
                tabs = (
                    data.get("contents", {})
                    .get("twoColumnBrowseResultsRenderer", {})
                    .get("tabs", [])
                )
                for tab in tabs:
                    tab_content = (
                        tab.get("tabRenderer", {})
                        .get("content", {})
                        .get("sectionListRenderer", {})
                        .get("contents", [])
                    )
                    for section in tab_content:
                        about = (
                            section.get("itemSectionRenderer", {})
                            .get("contents", [{}])[0]
                            .get("channelAboutFullMetadataRenderer", {})
                        )
                        if about:
                            break
                    if about:
                        break

            if about:
                result["description"] = about.get("description", "")
                result["subscriber_count_text"] = about.get(
                    "subscriberCountText", ""
                )
                joined = about.get("joinedDateText", {})
                if isinstance(joined, dict):
                    result["joined_date_text"] = joined.get("content", "")
                elif isinstance(joined, str):
                    result["joined_date_text"] = joined
                result["country"] = about.get("country", "")

            return result
        except Exception as e:
            logger.debug(f"Failed to fetch YouTube user info for {channel_id}: {e}")
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
        self._text_labels: list[QLabel] = []

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

        # Top row: profile image + name/badges
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        # Profile image (loaded async, placeholder initially)
        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(50, 50)
        self._avatar_label.setStyleSheet(
            "background: transparent; border: none; border-radius: 25px;"
        )
        self._avatar_label.hide()
        top_layout.addWidget(self._avatar_label)

        # Right side: name + platform + login
        name_col = QVBoxLayout()
        name_col.setSpacing(2)

        # Header: display name + platform
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)

        user_color = user.color or theme.text_primary
        name_label = QLabel(user.display_name)
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(13)
        name_label.setFont(name_font)
        name_label.setStyleSheet(f"color: {user_color}; background: transparent; border: none;")
        name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(name_label)
        header_layout.addWidget(name_label)

        # Nickname indicator
        nickname = settings.user_nicknames.get(user_key, "")
        if nickname:
            nick_label = QLabel(f'("{nickname}")')
            nick_label.setStyleSheet(
                f"color: {theme.text_muted}; font-style: italic;"
                " background: transparent; border: none;"
            )
            nick_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._text_labels.append(nick_label)
            header_layout.addWidget(nick_label)

        header_layout.addStretch()

        platform_names = {
            StreamPlatform.TWITCH: "Twitch",
            StreamPlatform.YOUTUBE: "YouTube",
            StreamPlatform.KICK: "Kick",
        }
        self._platform_label = QLabel(platform_names.get(user.platform, ""))
        self._platform_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px; background: transparent; border: none;"
        )
        self._platform_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._platform_label)
        header_layout.addWidget(self._platform_label)
        name_col.addLayout(header_layout)

        # @username (login name if different from display name)
        if user.name.lower() != user.display_name.lower():
            login_label = QLabel(f"@{user.name}")
            login_label.setStyleSheet(
                f"color: {theme.text_muted}; font-size: 11px;"
                " background: transparent; border: none;"
            )
            login_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._text_labels.append(login_label)
            name_col.addWidget(login_label)

        top_layout.addLayout(name_col)
        layout.addLayout(top_layout)

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
                                    18,
                                    18,
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation,
                                )
                            )
                badges_layout.addWidget(badge_label)
            badges_layout.addStretch()
            layout.addLayout(badges_layout)

        # Pronouns (loaded async, initially hidden)
        self._pronouns_label = QLabel()
        self._pronouns_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px; background: transparent; border: none;"
        )
        self._pronouns_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._pronouns_label)
        self._pronouns_label.hide()
        layout.addWidget(self._pronouns_label)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {theme.border_light}; background: transparent;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # Info section
        info_style = (
            f"color: {theme.text_muted}; font-size: 11px; background: transparent; border: none;"
        )

        # Bio/description (Twitch only, loaded async)
        self._bio_label = QLabel()
        self._bio_label.setStyleSheet(
            f"color: {theme.text_muted}; font-size: 11px; font-style: italic;"
            " background: transparent; border: none;"
        )
        self._bio_label.setWordWrap(True)
        self._bio_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._bio_label)
        self._bio_label.hide()
        layout.addWidget(self._bio_label)

        # Account created / joined date (Twitch, YouTube, Kick — loaded async)
        if user.platform in (StreamPlatform.TWITCH, StreamPlatform.YOUTUBE, StreamPlatform.KICK):
            loading_text = (
                "Joined: Loading..."
                if user.platform == StreamPlatform.YOUTUBE
                else "Account created: Loading..."
            )
            self._created_label = QLabel(loading_text)
            self._created_label.setStyleSheet(info_style)
            self._created_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._text_labels.append(self._created_label)
            layout.addWidget(self._created_label)
        else:
            self._created_label = None

        # Followers / subscribers (Twitch and YouTube, loaded async)
        self._followers_label = QLabel()
        self._followers_label.setStyleSheet(info_style)
        self._followers_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._followers_label)
        self._followers_label.hide()
        layout.addWidget(self._followers_label)

        # Country (YouTube only, loaded async)
        self._country_label = QLabel()
        self._country_label.setStyleSheet(info_style)
        self._country_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._country_label)
        self._country_label.hide()
        layout.addWidget(self._country_label)

        # Follow age (Twitch only, loaded async)
        self._follow_age_label = QLabel()
        self._follow_age_label.setStyleSheet(info_style)
        self._follow_age_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(self._follow_age_label)
        self._follow_age_label.hide()
        layout.addWidget(self._follow_age_label)

        # Session message count
        count_label = QLabel(f"Session messages: {message_count}")
        count_label.setStyleSheet(info_style)
        count_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_labels.append(count_label)
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
            note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._text_labels.append(note_label)
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

    def update_pronouns(self, text: str) -> None:
        """Update the pronouns label after async fetch."""
        if text:
            self._pronouns_label.setText(f"Pronouns: {text}")
            self._pronouns_label.show()
            self.adjustSize()

    def update_created_at(self, created_at_str: str) -> None:
        """Update the account creation date label after async fetch.

        Handles both ISO format (Twitch: "2015-06-21T15:40:00.000Z") and
        pre-formatted strings (YouTube: "Joined Mar 5, 2021").
        """
        if self._created_label and created_at_str:
            # YouTube returns pre-formatted "Joined Mar 5, 2021"
            if created_at_str.startswith("Joined"):
                self._created_label.setText(created_at_str)
            else:
                # Parse ISO format from Twitch
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    formatted = dt.astimezone().strftime("%B %d, %Y")
                    self._created_label.setText(f"Account created: {formatted}")
                except Exception:
                    self._created_label.setText(f"Account created: {created_at_str}")
        elif self._created_label:
            self._created_label.setText("Account created: Unknown")

    def update_bio(self, description: str) -> None:
        """Update the bio/description label."""
        if description:
            # Truncate long bios
            text = description if len(description) <= 120 else description[:117] + "..."
            self._bio_label.setText(f'"{text}"')
            self._bio_label.show()
            self.adjustSize()

    def update_followers(self, count: int) -> None:
        """Update the follower count label."""
        if count > 0:
            if count >= 1_000_000:
                text = f"{count / 1_000_000:.1f}M"
            elif count >= 1_000:
                text = f"{count / 1_000:.1f}K"
            else:
                text = str(count)
            self._followers_label.setText(f"Followers: {text}")
            self._followers_label.show()
            self.adjustSize()

    def update_subscribers(self, text: str) -> None:
        """Update the followers label with YouTube subscriber count text."""
        if text:
            self._followers_label.setText(f"Subscribers: {text}")
            self._followers_label.show()
            self.adjustSize()

    def update_country(self, text: str) -> None:
        """Update the country label (YouTube)."""
        if text:
            self._country_label.setText(f"Country: {text}")
            self._country_label.show()
            self.adjustSize()

    def update_verified(self, verified: bool) -> None:
        """Append a checkmark to the platform label if verified."""
        if verified and not self._platform_label.text().endswith(" \u2713"):
            self._platform_label.setText(self._platform_label.text() + " \u2713")

    def update_follow_age(self, followed_at_str: str) -> None:
        """Update the follow age label."""
        if followed_at_str:
            try:
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(followed_at_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                delta = now - dt
                days = delta.days
                if days >= 365:
                    years = days // 365
                    months = (days % 365) // 30
                    text = f"{years}y {months}mo" if months else f"{years}y"
                elif days >= 30:
                    months = days // 30
                    text = f"{months}mo"
                else:
                    text = f"{days}d"
                formatted_date = dt.astimezone().strftime("%b %d, %Y")
                self._follow_age_label.setText(f"Following: {text} (since {formatted_date})")
            except Exception:
                self._follow_age_label.setText("Following: Yes")
            self._follow_age_label.show()
            self.adjustSize()

    def update_avatar(self, image_data: bytes) -> None:
        """Update the profile image from raw bytes."""
        if not image_data:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(image_data) and not pixmap.isNull():
            scaled = pixmap.scaled(
                QSize(50, 50),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._avatar_label.setPixmap(scaled)
            self._avatar_label.show()
            self.adjustSize()

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

    def _get_selected_text(self) -> str:
        """Get selected text from any label, if any."""
        for label in self._text_labels:
            if label.hasSelectedText():
                return label.selectedText()
        return ""

    def _get_all_text(self) -> str:
        """Collect all visible text from the card."""
        lines = []
        for label in self._text_labels:
            if label.isVisible() and label.text():
                lines.append(label.text())
        return "\n".join(lines)

    def _copy_text(self) -> None:
        """Copy selected text, or all card text if nothing selected."""
        text = self._get_selected_text() or self._get_all_text()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Handle Ctrl+C to copy card text."""
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_text()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Show right-click context menu with copy options."""
        menu = QMenu(self)
        selected = self._get_selected_text()
        if selected:
            copy_sel = menu.addAction("Copy")
            copy_sel.triggered.connect(lambda: QApplication.clipboard().setText(selected))
        copy_all = menu.addAction("Copy All")
        copy_all.triggered.connect(self._copy_text)
        menu.exec(event.globalPos())
