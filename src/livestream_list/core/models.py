"""Core data models for Livestream List."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class StreamPlatform(str, Enum):
    """Supported streaming platforms."""

    TWITCH = "twitch"
    YOUTUBE = "youtube"
    KICK = "kick"


class StreamQuality(str, Enum):
    """Stream quality options."""

    SOURCE = "best"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    P360 = "360p"
    AUDIO_ONLY = "audio_only"


@dataclass
class Channel:
    """Represents a monitored channel."""

    channel_id: str
    platform: StreamPlatform
    display_name: Optional[str] = None
    imported_by: Optional[str] = None
    dont_notify: bool = False
    favorite: bool = False
    added_at: datetime = field(default_factory=datetime.now)

    @property
    def unique_key(self) -> str:
        """Get a unique identifier for this channel across all platforms."""
        return f"{self.platform.value}:{self.channel_id}"

    def __hash__(self) -> int:
        return hash(self.unique_key)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Channel):
            return False
        return self.unique_key == other.unique_key


@dataclass
class Livestream:
    """Represents an active or offline livestream."""

    channel: Channel
    live: bool = False
    title: Optional[str] = None
    game: Optional[str] = None
    viewers: int = 0
    start_time: Optional[datetime] = None
    last_live_time: Optional[datetime] = None
    thumbnail_url: Optional[str] = None
    is_partner: bool = False
    language: Optional[str] = None
    is_mature: bool = False
    error_message: Optional[str] = None
    video_id: Optional[str] = None  # YouTube video ID for live chat

    @property
    def display_name(self) -> str:
        """Get the display name for this stream."""
        return self.channel.display_name or self.channel.channel_id

    @property
    def uptime(self) -> timedelta:
        """Get the current uptime if live."""
        if self.live and self.start_time:
            now = datetime.now(timezone.utc) if self.start_time.tzinfo else datetime.now()
            return now - self.start_time
        return timedelta()

    @property
    def uptime_str(self) -> str:
        """Get a formatted uptime string."""
        if not self.live or not self.start_time:
            return ""
        total_seconds = int(self.uptime.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    @property
    def live_duration_str(self) -> str:
        """Get a formatted live duration string in human-readable format."""
        if not self.live or not self.start_time:
            return ""
        total_seconds = int(self.uptime.total_seconds())
        total_minutes = total_seconds // 60

        if total_minutes < 5:
            return "Under 5 minutes"

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes = remainder // 60

        parts = []
        if days > 0:
            parts.append(f"{days} day" if days == 1 else f"{days} days")
        if hours > 0 or days > 0:
            parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
        parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")

        return " ".join(parts)

    @property
    def viewers_str(self) -> str:
        """Get a formatted viewer count string."""
        if self.viewers >= 1_000_000:
            return f"{self.viewers / 1_000_000:.1f}M"
        if self.viewers >= 1_000:
            return f"{self.viewers / 1_000:.1f}K"
        return str(self.viewers)

    @property
    def last_seen_str(self) -> str:
        """Get a formatted 'last seen' string for offline streams."""
        if self.live or not self.last_live_time:
            return ""

        now = datetime.now(timezone.utc) if self.last_live_time.tzinfo else datetime.now()
        delta = now - self.last_live_time

        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return "just now"

        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"

        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"

        days = hours // 24
        if days < 30:
            return f"{days}d ago"

        months = days // 30
        if months < 12:
            return f"{months}mo ago"

        years = days // 365
        return f"{years}y ago"

    @property
    def stream_url(self) -> str:
        """Get the stream URL for this livestream."""
        if self.channel.platform == StreamPlatform.TWITCH:
            return f"https://twitch.tv/{self.channel.channel_id}"
        elif self.channel.platform == StreamPlatform.YOUTUBE:
            return f"https://youtube.com/channel/{self.channel.channel_id}/live"
        elif self.channel.platform == StreamPlatform.KICK:
            return f"https://kick.com/{self.channel.channel_id}"
        return ""

    @property
    def chat_url(self) -> str:
        """Get the chat URL for this livestream."""
        if self.channel.platform == StreamPlatform.TWITCH:
            return f"https://twitch.tv/popout/{self.channel.channel_id}/chat"
        elif self.channel.platform == StreamPlatform.YOUTUBE:
            if self.video_id:
                return f"https://youtube.com/live_chat?v={self.video_id}"
            return ""  # No chat URL without video ID
        elif self.channel.platform == StreamPlatform.KICK:
            return f"https://kick.com/{self.channel.channel_id}/chatroom"
        return ""

    def set_offline(self) -> None:
        """Mark the stream as offline."""
        self.live = False
        self.viewers = 0
        self.start_time = None

    def update_from(self, other: "Livestream") -> bool:
        """
        Update this livestream with data from another instance.
        Returns True if the stream went live (was offline, now online).
        """
        went_live = not self.live and other.live

        self.live = other.live
        self.title = other.title
        self.game = other.game
        self.viewers = other.viewers
        self.start_time = other.start_time
        self.thumbnail_url = other.thumbnail_url
        self.is_partner = other.is_partner
        self.language = other.language
        self.is_mature = other.is_mature
        self.error_message = other.error_message
        self.video_id = other.video_id

        if other.live:
            self.last_live_time = datetime.now(timezone.utc)
        elif other.last_live_time:
            # Preserve last_live_time from API for offline channels
            self.last_live_time = other.last_live_time

        return went_live

    def __hash__(self) -> int:
        return hash(self.channel)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Livestream):
            return False
        return self.channel == other.channel
