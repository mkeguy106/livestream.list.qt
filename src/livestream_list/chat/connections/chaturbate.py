"""Chaturbate chat connection via WebSocket."""

import asyncio
import json
import logging
import random
import string
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from ...core.models import StreamPlatform
from ..models import ChatBadge, ChatMessage, ChatUser, ModerationEvent
from .base import BaseChatConnection

logger = logging.getLogger(__name__)

# Chaturbate public API
CB_API_BASE = "https://chaturbate.com"

# Gender badge colors
GENDER_COLORS = {
    "m": "#1C6EBD",  # Male - blue
    "f": "#DC5500",  # Female - orange
    "s": "#A10069",  # Trans - magenta
    "c": "#CC6633",  # Couple - brown
}


class ChaturbateChatConnection(BaseChatConnection):
    """Chaturbate chat connection via WebSocket.

    Connects to Chaturbate's SockJS-based WebSocket chat system using
    credentials from the chatvideocontext API.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._chat_username: str = ""
        self._chat_password: str = ""

    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a Chaturbate channel's chat."""
        self._should_stop = False

        try:
            self._session = aiohttp.ClientSession()

            # Fetch chat credentials from the chatvideocontext API
            ctx = await self._fetch_chat_context(channel_id)
            if not ctx:
                self._emit_error(f"Could not get chat context for {channel_id}")
                return

            wschat_host = ctx.get("wschat_host", "")
            self._chat_username = ctx.get("chat_username", "")
            self._chat_password = ctx.get("chat_password", "")

            if not wschat_host:
                self._emit_error(f"No WebSocket host for {channel_id}")
                return

            # Build SockJS WebSocket URL
            server_id = "".join(random.choices(string.digits, k=3))
            session_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
            ws_url = f"wss://{wschat_host}/{server_id}/{session_id}/websocket"

            self._ws = await self._session.ws_connect(ws_url)

            # Wait for SockJS open frame ('o')
            msg = await self._ws.receive()
            if msg.type != aiohttp.WSMsgType.TEXT or not msg.data.startswith("o"):
                self._emit_error("Unexpected SockJS frame")
                return

            # Authenticate
            connect_msg = json.dumps(
                {
                    "method": "connect",
                    "data": {
                        "user": self._chat_username,
                        "password": self._chat_password,
                        "room": channel_id,
                        "room_password": "",
                    },
                }
            )
            await self._ws.send_str(json.dumps([connect_msg]))

            self._set_connected(channel_id)
            self._reset_backoff()
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

    async def send_message(self, text: str, reply_to_msg_id: str = "") -> bool:
        """Send a message to the connected channel.

        Chaturbate chat sending requires an authenticated session (logged-in user).
        Anonymous/guest connections cannot send messages.
        """
        if not self._ws or self._ws.closed:
            self._emit_error("Not connected")
            return False

        if not self._chat_username or self._chat_username.startswith("guest-"):
            self._emit_error("Login required to send messages")
            return False

        try:
            send_msg = json.dumps(
                {
                    "method": "privmsg",
                    "data": {
                        "message": text,
                        "color": "",
                        "font": "default",
                    },
                }
            )
            await self._ws.send_str(json.dumps([send_msg]))
            return True
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            return False

    async def _fetch_chat_context(self, channel_id: str) -> dict | None:
        """Fetch chat context (WS host, credentials) from the chatvideocontext API.

        Includes session cookies from QWebEngine for authenticated access.
        Without cookies, Chaturbate omits the WebSocket host from the response.
        """
        try:
            url = f"{CB_API_BASE}/api/chatvideocontext/{channel_id}/"
            headers = {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "X-Requested-With": "XMLHttpRequest",
            }

            # Add session cookies for authenticated WebSocket access
            cookie_str = self._get_cookie_string()
            if cookie_str:
                headers["Cookie"] = cookie_str

            async with self._session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"Chaturbate chat context HTTP {resp.status} "
                        f"for {channel_id}"
                    )
                    return None
                data = await resp.json()
                if data and isinstance(data, dict):
                    has_ws = bool(data.get("wschat_host"))
                    if not has_ws:
                        logger.debug(
                            f"Chat context for {channel_id}: no wschat_host "
                            f"(room_status={data.get('room_status')})"
                        )
                return data
        except Exception as e:
            logger.debug(f"Failed to fetch chat context for {channel_id}: {e}")
            return None

    @staticmethod
    def _get_cookie_string() -> str:
        """Get Chaturbate session cookies from QWebEngine profile."""
        try:
            from ...gui.chat.chaturbate_web_chat import get_chaturbate_cookie_string

            return get_chaturbate_cookie_string()
        except ImportError:
            return ""

    async def _read_loop(self) -> None:
        """Main read loop for incoming SockJS messages."""
        async for msg in self._ws:
            if self._should_stop:
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.data
                if data.startswith("a"):
                    # SockJS array frame — contains JSON-encoded message strings
                    try:
                        payloads = json.loads(data[1:])
                        for payload_str in payloads:
                            payload = json.loads(payload_str)
                            self._handle_event(payload)
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif data == "h":
                    # SockJS heartbeat — no action needed
                    pass

                if self._should_flush_batch():
                    self._flush_batch()

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

        self._flush_batch()

    def _handle_event(self, data: dict) -> None:
        """Handle a parsed chat event."""
        method = data.get("method", "")
        args = data.get("args", data)

        if method == "onAuthResponse":
            success = args.get("success", False)
            if not success:
                self._emit_error("Chat authentication failed")
        elif method == "roomMsg" or method == "onRoomMsg":
            self._handle_room_message(args)
        elif method == "onNotify":
            self._handle_notify(args)
        elif method == "onRoomCountUpdate":
            pass  # Could update viewer count
        elif method == "onKick":
            self._handle_kick(args)

    def _handle_room_message(self, data: dict) -> None:
        """Handle a chat room message."""
        username = data.get("from_user", {}).get("username", "")
        if not username:
            username = data.get("user", "")
        if not username:
            return

        msg_text = data.get("msg", "")
        if not msg_text:
            return

        from_user = data.get("from_user", {})
        color = data.get("color", from_user.get("chat_color", ""))
        gender = from_user.get("gender", "")
        is_mod = from_user.get("is_mod", False)
        in_fanclub = from_user.get("in_fanclub", False)
        has_tokens = from_user.get("has_tokens", False)
        tipped_recently = from_user.get("tipped_recently", False)

        # Build badges
        badges: list[ChatBadge] = []
        if is_mod:
            badges.append(ChatBadge(id="moderator", name="Moderator", image_url=""))
        if in_fanclub:
            badges.append(ChatBadge(id="fanclub", name="Fan Club", image_url=""))
        if tipped_recently:
            badges.append(ChatBadge(id="tipper", name="Recent Tipper", image_url=""))

        # Use gender color if no explicit color
        if not color and gender in GENDER_COLORS:
            color = GENDER_COLORS[gender]

        user = ChatUser(
            id=username,
            name=username,
            display_name=username,
            platform=StreamPlatform.CHATURBATE,
            color=color or None,
            badges=badges,
        )

        message = ChatMessage(
            id=str(uuid.uuid4()),
            user=user,
            text=msg_text,
            timestamp=datetime.now(timezone.utc),
            platform=StreamPlatform.CHATURBATE,
        )
        self._message_batch.append(message)

    def _handle_notify(self, data: dict) -> None:
        """Handle notification events (tips, room subjects, etc.)."""
        msg = data.get("msg", "")
        if not msg:
            return

        # Create a system message for tips and other notifications
        user = ChatUser(
            id="system",
            name="system",
            display_name="System",
            platform=StreamPlatform.CHATURBATE,
        )

        message = ChatMessage(
            id=str(uuid.uuid4()),
            user=user,
            text=msg,
            timestamp=datetime.now(timezone.utc),
            platform=StreamPlatform.CHATURBATE,
            is_system=True,
            system_text=msg,
        )
        self._message_batch.append(message)

    def _handle_kick(self, data: dict) -> None:
        """Handle a user kick event."""
        username = data.get("user", "")
        if username:
            event = ModerationEvent(
                type="ban",
                target_user_id=username,
            )
            self._emit_moderation(event)

    async def _cleanup(self) -> None:
        """Clean up WebSocket and session."""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
