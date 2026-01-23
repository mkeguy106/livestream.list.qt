"""Kick chat connection via Pusher WebSocket."""

import json
import logging
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from ...core.models import StreamPlatform
from ..models import ChatBadge, ChatMessage, ChatUser, ModerationEvent
from .base import BaseChatConnection

logger = logging.getLogger(__name__)

# Kick uses Pusher for WebSocket chat
PUSHER_WS_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
PUSHER_PARAMS = "?protocol=7&client=js&version=8.3.0&flash=false"

KICK_API_BASE = "https://kick.com/api/v2"


class KickChatConnection(BaseChatConnection):
    """Kick chat connection via Pusher WebSocket.

    Connects to Kick's Pusher-based chat system, subscribes to the
    channel's chatroom, and receives/sends messages.
    """

    def __init__(self, auth_token: str = "", parent=None):
        super().__init__(parent)
        self._auth_token = auth_token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._should_stop = False
        self._chatroom_id: int | None = None
        self._message_batch: list[ChatMessage] = []
        self._last_flush: float = 0

    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a Kick channel's chat.

        Args:
            channel_id: The channel slug (username).
            chatroom_id: Optional chatroom ID. If not provided, will be fetched.
        """
        self._should_stop = False
        self._chatroom_id = kwargs.get("chatroom_id")

        try:
            self._session = aiohttp.ClientSession()

            # Fetch chatroom_id if not provided
            if not self._chatroom_id:
                self._chatroom_id = await self._fetch_chatroom_id(channel_id)
                if not self._chatroom_id:
                    self._emit_error(f"Could not find chatroom for {channel_id}")
                    return

            # Connect to Pusher WebSocket
            ws_url = f"{PUSHER_WS_URL}{PUSHER_PARAMS}"
            self._ws = await self._session.ws_connect(ws_url)

            # Wait for connection established
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("event") == "pusher:connection_established":
                    logger.debug(f"Kick Pusher connected for {channel_id}")

            # Subscribe to chatroom channel
            subscribe_data = {
                "event": "pusher:subscribe",
                "data": {
                    "auth": "",
                    "channel": f"chatrooms.{self._chatroom_id}.v2",
                },
            }
            await self._ws.send_str(json.dumps(subscribe_data))

            self._set_connected(channel_id)
            self._last_flush = time.monotonic()

            # Message loop
            await self._read_loop()

        except Exception as e:
            if not self._should_stop:
                self._emit_error(f"Connection failed: {e}")
        finally:
            await self._cleanup()
            self._set_disconnected()

    async def disconnect(self) -> None:
        """Disconnect from the channel."""
        self._should_stop = True
        if self._ws and not self._ws.closed:
            await self._ws.close()

    async def send_message(self, text: str) -> bool:
        """Send a message to the connected Kick channel.

        Requires authentication token.
        """
        if not self._auth_token:
            self._emit_error("Cannot send messages without Kick authentication")
            return False

        if not self._chatroom_id:
            return False

        try:
            url = f"{KICK_API_BASE}/messages/send/{self._chatroom_id}"
            headers = {
                "Authorization": f"Bearer {self._auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            payload = {"content": text, "type": "message"}

            async with self._session.post(url, json=payload, headers=headers) as resp:
                return resp.status == 200
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            return False

    async def _fetch_chatroom_id(self, channel_id: str) -> int | None:
        """Fetch the chatroom ID for a channel from the Kick API."""
        try:
            url = f"{KICK_API_BASE}/channels/{channel_id}"
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            }
            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                chatroom = data.get("chatroom", {})
                return chatroom.get("id")
        except Exception as e:
            logger.debug(f"Failed to fetch chatroom_id for {channel_id}: {e}")
            return None

    async def _read_loop(self) -> None:
        """Main read loop for incoming Pusher messages."""
        async for msg in self._ws:
            if self._should_stop:
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    await self._handle_pusher_event(data)
                except json.JSONDecodeError:
                    pass

                # Flush batched messages
                now = time.monotonic()
                if len(self._message_batch) >= 10 or (
                    self._message_batch and now - self._last_flush >= 0.1
                ):
                    self._flush_batch()

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

        self._flush_batch()

    async def _handle_pusher_event(self, data: dict) -> None:
        """Handle a Pusher WebSocket event."""
        event = data.get("event", "")
        event_data_str = data.get("data", "")

        # Parse event data (Pusher wraps JSON as string)
        if isinstance(event_data_str, str) and event_data_str:
            try:
                event_data = json.loads(event_data_str)
            except json.JSONDecodeError:
                event_data = {}
        else:
            event_data = event_data_str if isinstance(event_data_str, dict) else {}

        if event == "App\\Events\\ChatMessageEvent":
            self._handle_chat_message(event_data)
        elif event == "App\\Events\\MessageDeletedEvent":
            self._handle_message_deleted(event_data)
        elif event == "App\\Events\\UserBannedEvent":
            self._handle_user_banned(event_data)
        elif event == "pusher:ping":
            # Respond to Pusher ping
            if self._ws and not self._ws.closed:
                await self._ws.send_str(json.dumps({"event": "pusher:pong", "data": ""}))

    def _handle_chat_message(self, data: dict) -> None:
        """Handle a chat message event."""
        sender = data.get("sender", {})
        user_id = str(sender.get("id", ""))
        username = sender.get("slug", sender.get("username", ""))
        display_name = sender.get("username", username)

        # Parse badges
        badges_data = data.get("sender", {}).get("identity", {}).get("badges", [])
        badges = []
        for badge_data in badges_data:
            badge_type = badge_data.get("type", "")
            badges.append(
                ChatBadge(
                    id=badge_type,
                    name=badge_type,
                    image_url=badge_data.get("image", {}).get("src", ""),
                )
            )

        # Parse color
        color = sender.get("identity", {}).get("color", None)

        user = ChatUser(
            id=user_id,
            name=username,
            display_name=display_name,
            platform=StreamPlatform.KICK,
            color=color,
            badges=badges,
        )

        # Parse timestamp
        created_at = data.get("created_at")
        if created_at:
            try:
                timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        message = ChatMessage(
            id=str(data.get("id", uuid.uuid4())),
            user=user,
            text=data.get("content", ""),
            timestamp=timestamp,
            platform=StreamPlatform.KICK,
        )

        self._message_batch.append(message)

    def _handle_message_deleted(self, data: dict) -> None:
        """Handle a message deletion event."""
        event = ModerationEvent(
            type="delete",
            target_message_id=str(data.get("message", {}).get("id", "")),
        )
        self._emit_moderation(event)

    def _handle_user_banned(self, data: dict) -> None:
        """Handle a user ban event."""
        user = data.get("user", {})
        banned_user = data.get("banned_user", user)
        duration = data.get("duration")

        event = ModerationEvent(
            type="timeout" if duration else "ban",
            target_user_id=str(banned_user.get("id", "")),
            duration=int(duration) if duration else None,
        )
        self._emit_moderation(event)

    def _flush_batch(self) -> None:
        """Emit batched messages and reset."""
        if self._message_batch:
            self._emit_messages(self._message_batch[:])
            self._message_batch.clear()
        self._last_flush = time.monotonic()

    async def _cleanup(self) -> None:
        """Clean up WebSocket and session."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
