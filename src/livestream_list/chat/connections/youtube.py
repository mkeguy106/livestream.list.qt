"""YouTube chat connection using pytchat or direct polling."""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from ...core.models import StreamPlatform
from ..models import ChatMessage, ChatUser
from .base import BaseChatConnection

logger = logging.getLogger(__name__)


class YouTubeChatConnection(BaseChatConnection):
    """YouTube live chat connection.

    Uses pytchat library for receiving live chat messages.
    Falls back to a polling-based approach if pytchat is not available.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._should_stop = False
        self._pytchat = None  # pytchat.LiveChat instance
        self._message_batch: list[ChatMessage] = []
        self._last_flush: float = 0

    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a YouTube channel's live chat.

        Args:
            channel_id: The YouTube channel ID.
            video_id: The video ID for the live stream (required).
        """
        self._should_stop = False
        video_id = kwargs.get("video_id")

        if not video_id:
            self._emit_error("YouTube chat requires a video_id")
            return

        try:
            import pytchat
        except ImportError:
            self._emit_error("pytchat library not installed. Install with: pip install pytchat")
            return

        try:
            self._pytchat = pytchat.create(video_id=video_id)
            self._set_connected(channel_id)
            self._last_flush = time.monotonic()

            # Poll loop
            await self._poll_loop()

        except Exception as e:
            if not self._should_stop:
                self._emit_error(f"YouTube chat error: {e}")
        finally:
            self._cleanup_pytchat()
            self._set_disconnected()

    async def disconnect(self) -> None:
        """Disconnect from YouTube chat."""
        self._should_stop = True
        self._cleanup_pytchat()

    async def send_message(self, text: str) -> bool:
        """Send a message to YouTube chat.

        Note: Sending requires Google OAuth authentication which is
        not yet implemented. Returns False for now.
        """
        self._emit_error("YouTube chat sending is not yet supported")
        return False

    async def _poll_loop(self) -> None:
        """Poll pytchat for new messages."""
        while not self._should_stop and self._pytchat and self._pytchat.is_alive():
            try:
                chat_data = self._pytchat.get()
                for item in chat_data.items:
                    message = self._parse_pytchat_item(item)
                    if message:
                        self._message_batch.append(message)

                # Flush batched messages
                now = time.monotonic()
                if len(self._message_batch) >= 10 or (
                    self._message_batch and now - self._last_flush >= 0.1
                ):
                    self._flush_batch()

            except Exception as e:
                if not self._should_stop:
                    logger.debug(f"YouTube poll error: {e}")

            # Poll interval
            await asyncio.sleep(2.0)

        self._flush_batch()

    def _parse_pytchat_item(self, item) -> ChatMessage | None:
        """Parse a pytchat chat item into a ChatMessage."""
        try:
            # pytchat item attributes: author, message, datetime, etc.
            user = ChatUser(
                id=getattr(item, "author", {}).get("channelId", str(uuid.uuid4())),
                name=getattr(item, "author", {}).get("name", "Unknown"),
                display_name=getattr(item, "author", {}).get("name", "Unknown"),
                platform=StreamPlatform.YOUTUBE,
                color=None,
                badges=[],
            )

            # Handle different pytchat item formats
            if hasattr(item, "author"):
                # Newer pytchat format
                author = item.author
                if hasattr(author, "name"):
                    user.name = author.name
                    user.display_name = author.name
                if hasattr(author, "channelId"):
                    user.id = author.channelId
            elif hasattr(item, "authorName"):
                # Older pytchat format
                user.name = item.authorName or "Unknown"
                user.display_name = user.name
                user.id = getattr(item, "authorChannelId", str(uuid.uuid4()))

            # Parse timestamp
            timestamp = datetime.now(timezone.utc)
            if hasattr(item, "datetime"):
                ts = item.datetime
                if isinstance(ts, datetime):
                    timestamp = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            elif hasattr(item, "timestamp"):
                try:
                    timestamp = datetime.fromtimestamp(
                        int(item.timestamp) / 1000000, tz=timezone.utc
                    )
                except (ValueError, OSError, TypeError):
                    pass

            # Get message text
            text = ""
            if hasattr(item, "message"):
                text = item.message or ""
            elif hasattr(item, "messageText"):
                text = item.messageText or ""

            if not text:
                return None

            return ChatMessage(
                id=getattr(item, "id", str(uuid.uuid4())),
                user=user,
                text=text,
                timestamp=timestamp,
                platform=StreamPlatform.YOUTUBE,
            )
        except Exception as e:
            logger.debug(f"Failed to parse YouTube chat item: {e}")
            return None

    def _flush_batch(self) -> None:
        """Emit batched messages and reset."""
        if self._message_batch:
            self._emit_messages(self._message_batch[:])
            self._message_batch.clear()
        self._last_flush = time.monotonic()

    def _cleanup_pytchat(self) -> None:
        """Clean up pytchat instance."""
        if self._pytchat:
            try:
                self._pytchat.terminate()
            except Exception:
                pass
            self._pytchat = None
