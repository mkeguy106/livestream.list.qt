"""Twitch IRC chat connection over WebSocket."""

import logging
import time
import uuid
from datetime import datetime, timezone

import aiohttp

from ...core.models import StreamPlatform
from ..models import ChatBadge, ChatEmote, ChatMessage, ChatUser, ModerationEvent
from .base import BaseChatConnection

logger = logging.getLogger(__name__)

TWITCH_IRC_WS_URL = "wss://irc-ws.chat.twitch.tv:443"

# IRC capabilities to request
IRC_CAPS = [
    "twitch.tv/membership",
    "twitch.tv/tags",
    "twitch.tv/commands",
]


def parse_irc_tags(tag_string: str) -> dict[str, str]:
    """Parse IRC tags string into a dictionary.

    Tags format: @key1=value1;key2=value2;...
    """
    tags: dict[str, str] = {}
    if not tag_string:
        return tags

    # Remove leading '@' if present
    if tag_string.startswith("@"):
        tag_string = tag_string[1:]

    for pair in tag_string.split(";"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            # Unescape IRC tag values
            value = (
                value.replace("\\:", ";")
                .replace("\\s", " ")
                .replace("\\\\", "\\")
                .replace("\\r", "\r")
                .replace("\\n", "\n")
            )
            tags[key] = value
        else:
            tags[pair] = ""

    return tags


def parse_irc_message(raw: str) -> dict:
    """Parse a raw IRC message into components.

    Returns dict with keys: tags, prefix, command, params, trailing
    """
    result: dict = {"tags": {}, "prefix": "", "command": "", "params": [], "trailing": ""}

    pos = 0

    # Parse tags
    if raw.startswith("@"):
        space_idx = raw.index(" ")
        result["tags"] = parse_irc_tags(raw[:space_idx])
        pos = space_idx + 1

    # Parse prefix
    if raw[pos] == ":":
        space_idx = raw.index(" ", pos)
        result["prefix"] = raw[pos + 1 : space_idx]
        pos = space_idx + 1

    # Parse command and params
    trailing_idx = raw.find(" :", pos)
    if trailing_idx >= 0:
        result["trailing"] = raw[trailing_idx + 2 :]
        remaining = raw[pos:trailing_idx]
    else:
        remaining = raw[pos:]

    parts = remaining.split(" ")
    result["command"] = parts[0]
    result["params"] = parts[1:] if len(parts) > 1 else []

    return result


def parse_emote_positions(emotes_tag: str) -> list[tuple[int, int, ChatEmote]]:
    """Parse Twitch emote positions from IRC tags.

    Format: emote_id:start-end,start-end/emote_id:start-end
    """
    positions: list[tuple[int, int, ChatEmote]] = []
    if not emotes_tag:
        return positions

    for emote_section in emotes_tag.split("/"):
        if ":" not in emote_section:
            continue
        emote_id, ranges = emote_section.split(":", 1)
        emote = ChatEmote(
            id=emote_id,
            name="",  # Will be filled from message text
            url_template=f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark/{{size}}",
            provider="twitch",
        )
        for range_str in ranges.split(","):
            if "-" in range_str:
                start_str, end_str = range_str.split("-", 1)
                try:
                    start = int(start_str)
                    end = int(end_str) + 1  # Twitch uses inclusive end
                    positions.append((start, end, emote))
                except ValueError:
                    pass

    return sorted(positions, key=lambda x: x[0])


def parse_badges(badges_tag: str) -> list[ChatBadge]:
    """Parse Twitch badges from IRC tags.

    Format: badge_name/version,badge_name/version
    The image_url is left empty here; the ChatManager resolves correct URLs
    from the Twitch badge API response.
    """
    badges: list[ChatBadge] = []
    if not badges_tag:
        return badges

    for badge_str in badges_tag.split(","):
        if "/" in badge_str:
            name, version = badge_str.split("/", 1)
            badges.append(
                ChatBadge(
                    id=f"{name}/{version}",
                    name=name,
                    image_url="",
                )
            )

    return badges


class TwitchChatConnection(BaseChatConnection):
    """Twitch IRC chat connection over WebSocket.

    Connects to Twitch's IRC WebSocket endpoint, authenticates with OAuth,
    and receives/sends chat messages.
    """

    def __init__(self, oauth_token: str = "", parent=None):
        super().__init__(parent)
        self._oauth_token = oauth_token
        self._nick = ""  # Set during auth
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._should_stop = False
        self._message_batch: list[ChatMessage] = []
        self._last_flush: float = 0

    async def connect_to_channel(self, channel_id: str, **kwargs) -> None:
        """Connect to a Twitch channel's chat via IRC WebSocket."""
        self._should_stop = False
        channel = channel_id.lower()

        try:
            self._session = aiohttp.ClientSession()
            self._ws = await self._session.ws_connect(TWITCH_IRC_WS_URL)

            # Request capabilities
            for cap in IRC_CAPS:
                await self._ws.send_str(f"CAP REQ :{cap}")

            # Authenticate
            if self._oauth_token:
                await self._ws.send_str(f"PASS oauth:{self._oauth_token}")
                # Extract nick from token (we'll get it from GLOBALUSERSTATE)
                self._nick = "justinfan" + str(hash(self._oauth_token) % 100000)
            else:
                # Anonymous connection (read-only)
                self._nick = f"justinfan{int(time.time()) % 100000}"

            await self._ws.send_str(f"NICK {self._nick}")

            # Join channel
            await self._ws.send_str(f"JOIN #{channel}")

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
        """Send a message to the connected channel."""
        if not self._ws or self._ws.closed or not self._channel_id:
            return False

        if not self._oauth_token:
            self._emit_error("Cannot send messages without authentication")
            return False

        try:
            await self._ws.send_str(f"PRIVMSG #{self._channel_id} :{text}")
            return True
        except Exception as e:
            self._emit_error(f"Failed to send message: {e}")
            return False

    async def _read_loop(self) -> None:
        """Main read loop for incoming IRC messages."""
        async for msg in self._ws:
            if self._should_stop:
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                for line in msg.data.split("\r\n"):
                    if line:
                        await self._handle_message(line)

                # Flush batched messages if threshold reached
                now = time.monotonic()
                if len(self._message_batch) >= 10 or (
                    self._message_batch and now - self._last_flush >= 0.1
                ):
                    self._flush_batch()

            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

        # Flush remaining messages
        self._flush_batch()

    async def _handle_message(self, raw: str) -> None:
        """Handle a single IRC message."""
        # Handle PING
        if raw.startswith("PING"):
            if self._ws and not self._ws.closed:
                await self._ws.send_str(f"PONG {raw[5:]}")
            return

        parsed = parse_irc_message(raw)
        command = parsed["command"]

        if command == "PRIVMSG":
            self._handle_privmsg(parsed)
        elif command == "CLEARCHAT":
            self._handle_clearchat(parsed)
        elif command == "CLEARMSG":
            self._handle_clearmsg(parsed)
        elif command == "GLOBALUSERSTATE":
            # Update our nick from server response
            display_name = parsed["tags"].get("display-name", "")
            if display_name:
                self._nick = display_name.lower()
        elif command == "USERNOTICE":
            # Sub/raid/etc - could be handled for display
            pass

    def _handle_privmsg(self, parsed: dict) -> None:
        """Handle a PRIVMSG (chat message)."""
        tags = parsed["tags"]
        text = parsed["trailing"]

        # Check for /me action
        is_action = False
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            is_action = True
            text = text[8:-1]

        # Parse user info
        user_id = tags.get("user-id", "")
        display_name = tags.get("display-name", "")
        username = parsed["prefix"].split("!")[0] if "!" in parsed["prefix"] else ""
        color = tags.get("color", None)
        badges = parse_badges(tags.get("badges", ""))

        user = ChatUser(
            id=user_id,
            name=username,
            display_name=display_name or username,
            platform=StreamPlatform.TWITCH,
            color=color if color else None,
            badges=badges,
        )

        # Parse emote positions
        emote_positions = parse_emote_positions(tags.get("emotes", ""))

        # Fill emote names from message text
        for i, (start, end, emote) in enumerate(emote_positions):
            if start < len(text) and end <= len(text):
                emote.name = text[start:end]

        # Parse timestamp
        tmi_sent = tags.get("tmi-sent-ts", "")
        if tmi_sent:
            try:
                timestamp = datetime.fromtimestamp(int(tmi_sent) / 1000, tz=timezone.utc)
            except (ValueError, OSError):
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        message = ChatMessage(
            id=tags.get("id", str(uuid.uuid4())),
            user=user,
            text=text,
            timestamp=timestamp,
            platform=StreamPlatform.TWITCH,
            emote_positions=emote_positions,
            is_action=is_action,
            is_first_message=tags.get("first-msg", "0") == "1",
        )

        self._message_batch.append(message)

    def _handle_clearchat(self, parsed: dict) -> None:
        """Handle CLEARCHAT (ban/timeout or chat clear)."""
        tags = parsed["tags"]
        target_user = parsed["trailing"]  # Username being banned/timed out

        if target_user:
            duration = tags.get("ban-duration")
            event_type = "timeout" if duration else "ban"
            event = ModerationEvent(
                type=event_type,
                target_user_id=tags.get("target-user-id", ""),
                duration=int(duration) if duration else None,
            )
        else:
            # Full chat clear
            event = ModerationEvent(type="clear")

        self._emit_moderation(event)

    def _handle_clearmsg(self, parsed: dict) -> None:
        """Handle CLEARMSG (single message deletion)."""
        tags = parsed["tags"]
        event = ModerationEvent(
            type="delete",
            target_message_id=tags.get("target-msg-id", ""),
            target_user_id=tags.get("login", ""),
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
