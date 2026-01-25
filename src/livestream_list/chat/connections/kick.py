"""Kick chat connection via Pusher WebSocket."""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from ...core.models import StreamPlatform
from ...core.settings import KickSettings
from ..models import ChatBadge, ChatEmote, ChatMessage, ChatUser, ModerationEvent
from .base import BaseChatConnection

logger = logging.getLogger(__name__)

# Kick uses Pusher for WebSocket chat
PUSHER_WS_URL = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
PUSHER_PARAMS = "?protocol=7&client=js&version=8.3.0&flash=false"

KICK_API_BASE = "https://kick.com/api/v2"
KICK_OAUTH_BASE = "https://id.kick.com"
KICK_EMOTE_URL = "https://files.kick.com/emotes/{id}/fullsize"
KICK_BADGE_BASE = "https://www.kickdatabase.com/kickBadges"

# Static badge URLs for system badges (not provided by Kick API)
KICK_SYSTEM_BADGES: dict[str, str] = {
    "broadcaster": f"{KICK_BADGE_BASE}/broadcaster.svg",
    "moderator": f"{KICK_BADGE_BASE}/moderator.svg",
    "vip": f"{KICK_BADGE_BASE}/vip.svg",
    "og": f"{KICK_BADGE_BASE}/og.svg",
    "founder": f"{KICK_BADGE_BASE}/founder.svg",
    "staff": f"{KICK_BADGE_BASE}/staff.svg",
    "verified": f"{KICK_BADGE_BASE}/verified.svg",
    "sub_gifter": f"{KICK_BADGE_BASE}/subGifter.svg",
}

# Matches [emote:ID:name] in Kick message content
KICK_EMOTE_RE = re.compile(r"\[emote:(\d+):([^\]]+)\]")


class KickChatConnection(BaseChatConnection):
    """Kick chat connection via Pusher WebSocket.

    Connects to Kick's Pusher-based chat system, subscribes to the
    channel's chatroom, and receives/sends messages.
    """

    def __init__(self, kick_settings: KickSettings | None = None, parent=None):
        super().__init__(parent)
        self._kick_settings = kick_settings
        self._auth_token = kick_settings.access_token if kick_settings else ""
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._should_stop = False
        self._chatroom_id: int | None = None
        self._broadcaster_user_id: int | None = None
        self._badge_url_map: dict[str, str] = {}  # badge_type -> image_url
        self._message_batch: list[ChatMessage] = []
        self._last_flush: float = 0
        self._refresh_lock = asyncio.Lock()

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
            self._reset_backoff()  # Reset backoff on successful connection
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

        Uses the official Kick public API with OAuth bearer token.
        Automatically refreshes the token on 401 and retries once.
        Uses a lock to prevent concurrent token refresh races.
        """
        if not self._auth_token:
            self._emit_error("Cannot send messages without Kick authentication")
            return False

        if not self._broadcaster_user_id:
            self._emit_error("Unknown broadcaster ID")
            return False

        result = await self._do_send(text)
        if result is None:
            # 401 - try refreshing token under lock to prevent concurrent refreshes
            async with self._refresh_lock:
                if await self._refresh_auth_token():
                    result = await self._do_send(text)
                    if result is None:
                        self._emit_error("Send failed after token refresh (401)")
                        return False
                    return result
                else:
                    self._emit_error("Authentication expired - please re-login to Kick")
                    return False
        return result

    async def _do_send(self, text: str) -> bool | None:
        """Attempt to send a message. Returns None on 401 (needs refresh)."""
        try:
            url = "https://api.kick.com/public/v1/chat"
            headers = {
                "Authorization": f"Bearer {self._auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            payload = {
                "broadcaster_user_id": int(self._broadcaster_user_id),
                "content": text,
                "type": "user",
            }
            logger.debug(f"Kick send_message payload: {payload}")

            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    is_sent = data.get("data", {}).get("is_sent", False)
                    if not is_sent:
                        logger.warning(f"Kick chat send returned is_sent=False: {data}")
                    return is_sent
                if resp.status == 401:
                    body = await resp.text()
                    logger.warning(f"Kick chat send got 401: {body}")
                    return None  # Signal to refresh and retry
                body = await resp.text()
                logger.error(f"Kick chat send failed ({resp.status}): {body}")
                self._emit_error(f"Send failed ({resp.status})")
                return False
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            return False

    async def _refresh_auth_token(self) -> bool:
        """Refresh the OAuth access token using the refresh token."""
        if not self._kick_settings or not self._kick_settings.refresh_token:
            logger.warning("No refresh token available for Kick")
            return False

        from ..auth.kick_auth import DEFAULT_KICK_CLIENT_ID, DEFAULT_KICK_CLIENT_SECRET

        client_id = self._kick_settings.client_id or DEFAULT_KICK_CLIENT_ID
        client_secret = self._kick_settings.client_secret or DEFAULT_KICK_CLIENT_SECRET

        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": self._kick_settings.refresh_token,
        }

        try:
            async with self._session.post(
                f"{KICK_OAUTH_BASE}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Kick token refresh failed ({resp.status}): {body}")
                    return False

                token_data = await resp.json()
                new_token = token_data.get("access_token", "")
                if not new_token:
                    return False

                # Update local token and persist to settings
                self._auth_token = new_token
                self._kick_settings.access_token = new_token
                new_refresh = token_data.get("refresh_token")
                if new_refresh:
                    self._kick_settings.refresh_token = new_refresh
                logger.info("Kick token refreshed successfully")
                return True
        except Exception as e:
            logger.error(f"Kick token refresh error: {e}")
            return False

    async def _fetch_chatroom_id(self, channel_id: str) -> int | None:
        """Fetch the chatroom ID and badge URLs for a channel from the Kick API."""
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

                # Store broadcaster user ID for sending messages
                self._broadcaster_user_id = data.get("user_id")
                logger.debug(
                    f"Kick channel {channel_id}: broadcaster_user_id={self._broadcaster_user_id}"
                )

                # Extract subscriber badge URLs
                for badge in data.get("subscriber_badges", []):
                    months = badge.get("months", 0)
                    badge_img = badge.get("badge_image", {})
                    src = badge_img.get("src", "")
                    if src:
                        self._badge_url_map[f"subscriber/{months}"] = src
                        # Also map generic "subscriber" to the first badge
                        if "subscriber" not in self._badge_url_map:
                            self._badge_url_map["subscriber"] = src

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
            # Try image from event data, then fetched channel map, then static map
            image_url = (
                badge_data.get("image", {}).get("src", "")
                or self._badge_url_map.get(badge_type, "")
                or KICK_SYSTEM_BADGES.get(badge_type, "")
            )
            badge_name = badge_data.get("text", badge_type.replace("_", " ").title())
            badges.append(
                ChatBadge(
                    id=badge_type,
                    name=badge_name,
                    image_url=image_url,
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

        # Parse emotes from content: [emote:ID:name] → rendered emote
        raw_content = data.get("content", "")
        emote_positions: list[tuple[int, int, ChatEmote]] = []
        text_parts: list[str] = []
        last_end = 0

        for match in KICK_EMOTE_RE.finditer(raw_content):
            emote_id = match.group(1)
            emote_name = match.group(2)
            # Add text before this emote
            text_parts.append(raw_content[last_end : match.start()])
            start = len("".join(text_parts))
            text_parts.append(emote_name)
            end = start + len(emote_name)
            emote = ChatEmote(
                id=emote_id,
                name=emote_name,
                url_template=KICK_EMOTE_URL.format(id=emote_id),
                provider="kick",
            )
            emote_positions.append((start, end, emote))
            last_end = match.end()

        text_parts.append(raw_content[last_end:])
        text = "".join(text_parts)

        message = ChatMessage(
            id=str(data.get("id", uuid.uuid4())),
            user=user,
            text=text,
            timestamp=timestamp,
            platform=StreamPlatform.KICK,
            emote_positions=emote_positions,
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
