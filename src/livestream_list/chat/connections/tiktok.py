"""TikTok chat connection via TikTokLive library."""

import logging
import uuid
from datetime import datetime, timezone

from ..models import ChatMessage, ChatUser
from .base import BaseChatConnection

logger = logging.getLogger(__name__)

try:
    from TikTokLive import TikTokLiveClient
    from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, GiftEvent

    HAS_TIKTOK_LIVE = True
except ImportError:
    HAS_TIKTOK_LIVE = False
    logger.debug("TikTokLive library not installed — TikTok chat unavailable")


class TikTokChatConnection(BaseChatConnection):
    """TikTok chat connection via TikTokLive WebCast protocol.

    Uses the TikTokLive library to connect to TikTok's WebCast WebSocket
    and receive chat messages and gift events.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = None

    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a TikTok channel's live chat."""
        if not HAS_TIKTOK_LIVE:
            self._emit_error(
                "TikTokLive library not installed. Install with: pip install TikTokLive"
            )
            return

        self._should_stop = False
        unique_id = f"@{channel_id}" if not channel_id.startswith("@") else channel_id

        attempt = 0
        while not self._should_stop:
            try:
                self._client = TikTokLiveClient(unique_id=unique_id)

                @self._client.on(ConnectEvent)
                async def on_connect(event: ConnectEvent):
                    self._set_connected(channel_id)
                    self._reset_backoff()
                    logger.info(f"TikTok: Connected to {channel_id}")

                @self._client.on(DisconnectEvent)
                async def on_disconnect(event: DisconnectEvent):
                    logger.info(f"TikTok: Disconnected from {channel_id}")

                @self._client.on(CommentEvent)
                async def on_comment(event: CommentEvent):
                    msg = ChatMessage(
                        id=str(uuid.uuid4()),
                        platform="tiktok",
                        channel_id=channel_id,
                        user=ChatUser(
                            id=str(event.user.user_id) if event.user.user_id else "",
                            display_name=event.user.nickname or event.user.unique_id or "",
                            color="",
                        ),
                        text=event.comment or "",
                        timestamp=datetime.now(timezone.utc),
                    )
                    self._message_batch.append(msg)
                    if self._should_flush_batch():
                        self._flush_batch()

                @self._client.on(GiftEvent)
                async def on_gift(event: GiftEvent):
                    gift_name = event.gift.name if event.gift else "Gift"
                    user_name = ""
                    if event.user:
                        user_name = event.user.nickname or event.user.unique_id or ""
                    text = f"{user_name} sent {gift_name}"
                    if hasattr(event, "repeat_count") and event.repeat_count:
                        text += f" x{event.repeat_count}"

                    msg = ChatMessage(
                        id=str(uuid.uuid4()),
                        platform="tiktok",
                        channel_id=channel_id,
                        user=ChatUser(
                            id="",
                            display_name="TikTok",
                            color="#69C9D0",
                        ),
                        text=text,
                        timestamp=datetime.now(timezone.utc),
                        is_system=True,
                    )
                    self._message_batch.append(msg)
                    if self._should_flush_batch():
                        self._flush_batch()

                await self._client.connect()

            except Exception as e:
                if self._should_stop:
                    break
                self._set_disconnected()
                error_msg = str(e)
                logger.warning(f"TikTok: Connection error for {channel_id}: {error_msg}")

                # Check for non-retryable errors
                if "not found" in error_msg.lower() or "user_not_found" in error_msg.lower():
                    self._emit_error(f"TikTok user not found: {channel_id}")
                    break

                attempt += 1
                if self._max_reconnect_attempts and attempt >= self._max_reconnect_attempts:
                    self._emit_error(f"Max reconnect attempts reached for {channel_id}")
                    break

                await self._sleep_with_backoff()

            finally:
                self._flush_batch()

        self._set_disconnected()

    async def disconnect(self) -> None:
        """Disconnect from the current channel."""
        self._should_stop = True
        self._should_reconnect = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def send_message(self, text: str, reply_to_msg_id: str = "") -> bool:
        """TikTok chat sending is not yet supported."""
        self._emit_error("TikTok chat sending is not yet supported")
        return False
