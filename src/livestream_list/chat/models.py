"""Data models for the built-in chat system."""

from dataclasses import dataclass, field
from datetime import datetime

from ..core.models import StreamPlatform


@dataclass
class ChatEmote:
    """Represents a chat emote from any provider."""

    id: str
    name: str  # Text code (e.g., "KEKW")
    url_template: str  # URL with {size} placeholder
    provider: str  # "twitch", "7tv", "bttv", "ffz"
    zero_width: bool = False  # 7TV overlay emotes


@dataclass
class ChatBadge:
    """Represents a chat badge (sub, mod, etc.)."""

    id: str
    name: str
    image_url: str


@dataclass
class ChatUser:
    """Represents a chat message author."""

    id: str
    name: str
    display_name: str
    platform: StreamPlatform
    color: str | None = None
    badges: list[ChatBadge] = field(default_factory=list)


@dataclass
class ChatMessage:
    """Represents a single chat message."""

    id: str
    user: ChatUser
    text: str
    timestamp: datetime
    platform: StreamPlatform
    emote_positions: list[tuple[int, int, ChatEmote]] = field(default_factory=list)
    is_action: bool = False  # /me messages
    is_moderated: bool = False  # Strikethrough + 50% opacity (NOT deleted)
    is_first_message: bool = False
    is_system: bool = False  # USERNOTICE (sub, raid, etc.)
    system_text: str = ""  # System message text (e.g., "UserX subscribed!")
    is_hype_chat: bool = False  # Paid pinned message
    hype_chat_amount: str = ""
    hype_chat_currency: str = ""
    hype_chat_level: str = ""


@dataclass
class ModerationEvent:
    """Represents a moderation action (ban, timeout, delete)."""

    type: str  # "ban", "timeout", "delete"
    target_user_id: str | None = None
    target_message_id: str | None = None
    duration: int | None = None  # Timeout duration in seconds
