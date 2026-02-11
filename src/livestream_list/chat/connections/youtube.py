"""YouTube chat connection using pytchat with InnerTube message sending."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from ...core.models import StreamPlatform
from ...core.settings import YouTubeSettings
from ..emotes.image import ImageSet, ImageSpec
from ..models import ChatBadge, ChatEmote, ChatMessage, ChatRoomState, ChatUser
from .base import BaseChatConnection
from .youtube_processor import LivestreamListProcessor

logger = logging.getLogger(__name__)

# Required cookies for InnerTube authentication
REQUIRED_COOKIE_KEYS = {"SID", "HSID", "SSID", "APISID", "SAPISID"}

# Max age for extracted send params before proactive re-extraction (2 hours)
PARAMS_MAX_AGE = 7200

# SuperChat tier thresholds (USD equivalent)
_SUPERCHAT_TIERS = [
    (100, "RED"),
    (50, "MAGENTA"),
    (20, "ORANGE"),
    (10, "YELLOW"),
    (5, "GREEN"),
    (2, "CYAN"),
    (0, "BLUE"),
]


def _get_superchat_tier(amount: float) -> str:
    """Map a SuperChat amount to a tier level."""
    for threshold, tier in _SUPERCHAT_TIERS:
        if amount >= threshold:
            return tier
    return "BLUE"


def _generate_sapisidhash(sapisid: str, origin: str = "https://www.youtube.com") -> str:
    """Generate SAPISIDHASH authorization header value."""
    timestamp = int(time.time())
    hash_input = f"{timestamp} {sapisid} {origin}"
    hash_value = hashlib.sha1(hash_input.encode()).hexdigest()
    return f"SAPISIDHASH {timestamp}_{hash_value}"


def parse_cookie_string(cookie_str: str) -> dict[str, str]:
    """Parse a cookie string into a dict of name -> value.

    Handles both 'name=value; name2=value2' format and
    Netscape/curl cookie jar format.
    """
    cookies: dict[str, str] = {}
    if not cookie_str.strip():
        return cookies

    # Try simple "name=value; name2=value2" format first
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if name:
                cookies[name] = value

    return cookies


def validate_cookies(cookie_str: str) -> bool:
    """Check if a cookie string contains the required keys for YouTube auth."""
    parsed = parse_cookie_string(cookie_str)
    return REQUIRED_COOKIE_KEYS.issubset(parsed.keys())


class YouTubeChatConnection(BaseChatConnection):
    """YouTube live chat connection.

    Uses pytchat library for receiving live chat messages.
    Supports InnerTube API for sending messages when cookies are configured.
    """

    def __init__(self, youtube_settings: YouTubeSettings | None = None, parent=None):
        super().__init__(parent)
        self._should_stop = False
        self._pytchat = None  # pytchat.LiveChat instance
        self._processor: LivestreamListProcessor | None = None
        self._message_batch: list[ChatMessage] = []
        self._last_flush: float = 0
        self._youtube_settings = youtube_settings

        # InnerTube sending state
        self._innertube_api_key: str = ""
        self._send_params: str = ""
        self._datasync_id: str = ""
        self._client_version: str = "2.20240101.00.00"
        self._video_id: str = ""
        self._cookies: dict[str, str] = {}
        self._chat_restriction: str = ""  # e.g. "Subscribers-only mode"
        self._params_extracted_at: float = 0.0
        self._extract_lock: asyncio.Lock = asyncio.Lock()

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

        self._video_id = video_id

        try:
            import pytchat
        except ImportError:
            self._emit_error("pytchat library not installed. Install with: pip install pytchat")
            return

        # Parse cookies for sending support
        yt_settings = self._youtube_settings
        if yt_settings and yt_settings.cookies:
            self._cookies = parse_cookie_string(yt_settings.cookies)
            if REQUIRED_COOKIE_KEYS.issubset(self._cookies.keys()):
                await self._extract_send_params(video_id)

        try:
            self._processor = LivestreamListProcessor()
            self._pytchat = pytchat.create(
                video_id=video_id, processor=self._processor, interruptable=False
            )
            self._set_connected(channel_id)
            self._reset_backoff()  # Reset backoff on successful connection
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

    async def send_message(self, text: str, reply_to_msg_id: str = "") -> bool:
        """Send a message to YouTube chat via InnerTube API with retry logic."""
        if not self._cookies:
            self._emit_error("YouTube chat sending not available (cookies not configured)")
            return False
        if self._chat_restriction and not self._send_params:
            if self._chat_restriction == "cookies_expired":
                self._emit_error("YouTube login expired — update cookies to send")
            else:
                self._emit_error(
                    f"Cannot send: {self._chat_restriction}. "
                    "Use browser popout chat for restricted chats."
                )
            return False

        sapisid = self._cookies.get("SAPISID", "")
        if not sapisid:
            self._emit_error("Missing SAPISID cookie for YouTube auth")
            return False

        # Proactive re-extract if params are stale
        if self._params_extracted_at and self._send_params:
            age = time.monotonic() - self._params_extracted_at
            if age > PARAMS_MAX_AGE:
                logger.info(f"YouTube send params are {age:.0f}s old, re-extracting")
                await self._re_extract_send_params()

        if not self._send_params or not self._innertube_api_key:
            self._emit_error("YouTube login expired — update cookies to send")
            return False

        result = await self._do_send_yt(text)
        if result is True:
            return True

        if result == "auth_expired":
            logger.info("YouTube send auth expired, re-extracting params and retrying")
            if await self._re_extract_send_params():
                retry = await self._do_send_yt(text)
                if retry is True:
                    return True
            self._emit_error("YouTube auth expired. Try refreshing your cookies.")
            return False

        if result == "server_error":
            logger.info("YouTube server error, retrying once after 2s")
            await asyncio.sleep(2)
            retry = await self._do_send_yt(text)
            if retry is True:
                return True
            self._emit_error("YouTube server error. Try again later.")
            return False

        if result == "rate_limited":
            self._emit_error("YouTube rate limit reached. Wait a moment before sending again.")
            return False

        # Generic failure
        self._emit_error("Failed to send YouTube message")
        return False

    async def _do_send_yt(self, text: str) -> bool | str:
        """Perform the actual YouTube send HTTP request.

        Returns:
            True on success, or a string indicating the failure type:
            "auth_expired", "rate_limited", "server_error", or False.
        """
        try:
            import aiohttp

            sapisid = self._cookies.get("SAPISID", "")
            auth_header = _generate_sapisidhash(sapisid)
            cookie_header = "; ".join(f"{k}={v}" for k, v in self._cookies.items())

            headers = {
                "Authorization": auth_header,
                "Cookie": cookie_header,
                "Content-Type": "application/json",
                "Origin": "https://www.youtube.com",
                "X-Origin": "https://www.youtube.com",
                "X-Youtube-Client-Name": "1",
                "X-Youtube-Client-Version": self._client_version,
            }

            body = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": self._client_version,
                    },
                },
                "params": self._send_params,
                "richMessage": {
                    "textSegments": [{"text": text}],
                },
                "clientMessageId": f"msg-{uuid.uuid4().hex[:16]}",
            }

            if self._datasync_id:
                body["context"]["user"] = {"datasyncId": self._datasync_id}

            url = (
                f"https://www.youtube.com/youtubei/v1/live_chat/send_message"
                f"?key={self._innertube_api_key}"
            )

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        return True
                    elif resp.status in (401, 403):
                        logger.warning(f"YouTube send auth failed ({resp.status})")
                        return "auth_expired"
                    elif resp.status == 429:
                        logger.warning("YouTube send rate limited")
                        return "rate_limited"
                    elif resp.status >= 500:
                        logger.warning(f"YouTube server error ({resp.status})")
                        return "server_error"
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"YouTube send_message failed ({resp.status}): {error_text[:200]}"
                        )
                        return False

        except Exception as e:
            logger.error(f"YouTube send_message error: {e}")
            return False

    async def _re_extract_send_params(self) -> bool:
        """Re-extract InnerTube send params under lock.

        Returns True if params were successfully re-extracted.
        """
        async with self._extract_lock:
            # Skip if another coroutine just re-extracted
            if self._params_extracted_at and (time.monotonic() - self._params_extracted_at < 60):
                return bool(self._send_params and self._innertube_api_key)

            old_api_key = self._innertube_api_key
            old_params = self._send_params
            old_datasync = self._datasync_id

            self._innertube_api_key = ""
            self._send_params = ""
            self._datasync_id = ""

            await self._extract_send_params(self._video_id)

            if self._send_params and self._innertube_api_key:
                return True

            # Restore old params if re-extraction failed entirely
            logger.warning("YouTube param re-extraction failed, restoring old params")
            self._innertube_api_key = old_api_key
            self._send_params = old_params
            self._datasync_id = old_datasync
            return False

    async def _extract_send_params(self, video_id: str) -> None:
        """Extract InnerTube API key and send params from the live chat page."""
        try:
            import aiohttp

            cookie_header = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            sapisid = self._cookies.get("SAPISID", "")
            headers = {
                "Cookie": cookie_header,
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/",
            }
            if sapisid:
                headers["Authorization"] = _generate_sapisidhash(sapisid)

            url = f"https://www.youtube.com/live_chat?is_popout=1&v={video_id}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to fetch live_chat page: {resp.status}")
                        return

                    html = await resp.text()

            # Check if YouTube recognizes our cookies as authenticated
            logged_in_match = re.search(r'"LOGGED_IN"\s*:\s*(true|false)', html)
            is_logged_in = logged_in_match and logged_in_match.group(1) == "true"
            if not is_logged_in:
                logger.warning(
                    "YouTube does not recognize cookies as authenticated "
                    "(LOGGED_IN=false). Cookies may be expired — "
                    "re-export from browser in Preferences > Accounts."
                )
                self._chat_restriction = "cookies_expired"
                self._emit_error("cookies_expired")
                return

            # Extract INNERTUBE_API_KEY
            api_key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
            if api_key_match:
                self._innertube_api_key = api_key_match.group(1)

            # Extract client version
            version_match = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
            if version_match:
                self._client_version = version_match.group(1)

            # Diagnostic logging — check which key markers exist in HTML
            has_send = "sendLiveChatMessageEndpoint" in html
            has_restricted = "liveChatRestrictedParticipationRenderer" in html
            has_input = "liveChatMessageInputRenderer" in html
            logger.debug(
                "YouTube live_chat HTML markers: "
                f"sendEndpoint={'yes' if has_send else 'no'}, "
                f"restrictedRenderer={'yes' if has_restricted else 'no'}, "
                f"inputRenderer={'yes' if has_input else 'no'}"
            )

            # Extract sendLiveChatMessageEndpoint params
            # Use re.DOTALL with bounded window to handle nested JSON objects
            params_match = re.search(
                r'"sendLiveChatMessageEndpoint"\s*:\s*\{.{0,1000}?"params"\s*:\s*"([^"]+)"',
                html,
                re.DOTALL,
            )
            if params_match:
                self._send_params = params_match.group(1)

            # Extract datasyncId
            datasync_match = re.search(r'"datasyncId"\s*:\s*"([^"]+)"', html)
            if datasync_match:
                self._datasync_id = datasync_match.group(1)

            # Extract logged-in user's display name for local echo
            author_match = re.search(
                r'"liveChatMessageInputRenderer"\s*:\s*\{.{0,500}?"authorName"\s*:\s*\{.{0,200}?"simpleText"\s*:\s*"([^"]+)"',
                html,
                re.DOTALL,
            )
            if author_match:
                self._nick = author_match.group(1)
                logger.debug(f"YouTube user: {self._nick}")

            # Check for chat restrictions (e.g. subscribers-only mode)
            restriction_match = re.search(
                r'"liveChatRestrictedParticipationRenderer"\s*:\s*\{.{0,500}?"text"\s*:\s*"([^"]+)"',
                html,
                re.DOTALL,
            )
            if restriction_match:
                self._chat_restriction = restriction_match.group(1)
                logger.info(f"YouTube chat restriction detected: {self._chat_restriction}")
                # Emit initial room state for detected restrictions
                restriction_lower = self._chat_restriction.lower()
                if "subscriber" in restriction_lower or "member" in restriction_lower:
                    self._emit_room_state(ChatRoomState(subs_only=True))

            # Check for slow mode in initial page data
            slow_match = re.search(
                r'"slowModeRenderer"\s*:\s*\{.{0,500}?"slowModeDurationSeconds"\s*:\s*"?(\d+)',
                html,
                re.DOTALL,
            )
            if slow_match:
                slow_seconds = int(slow_match.group(1))
                self._emit_room_state(ChatRoomState(slow=slow_seconds))
                logger.info(f"YouTube slow mode detected: {slow_seconds}s")

            if self._innertube_api_key and self._send_params:
                self._params_extracted_at = time.monotonic()
                logger.info("YouTube InnerTube send params extracted successfully")
            else:
                reason = ""
                if self._chat_restriction:
                    reason = f" (restriction: {self._chat_restriction})"
                logger.warning(
                    "Could not extract all InnerTube params "
                    f"(api_key={'yes' if self._innertube_api_key else 'no'}, "
                    f"params={'yes' if self._send_params else 'no'}){reason}"
                )

        except Exception as e:
            logger.warning(f"Failed to extract YouTube send params: {e}")

    async def _poll_loop(self) -> None:
        """Poll pytchat for new messages."""
        loop = asyncio.get_event_loop()
        while not self._should_stop and self._pytchat and self._pytchat.is_alive():
            try:
                # Run blocking HTTP call in executor to not block the event loop
                chat_data = await loop.run_in_executor(None, self._pytchat.get)

                # get() returns [] when stream is no longer alive
                if not chat_data or not hasattr(chat_data, "items"):
                    await asyncio.sleep(2.0)
                    continue

                for item in chat_data.items:
                    message = self._parse_pytchat_item(item)
                    if message:
                        self._message_batch.append(message)

                # Process moderation events and mode changes from custom processor
                if self._processor:
                    self._process_extra_events()

                # Flush batched messages
                now = time.monotonic()
                if len(self._message_batch) >= 10 or (
                    self._message_batch and now - self._last_flush >= 0.1
                ):
                    self._flush_batch()

            except Exception as e:
                if not self._should_stop:
                    logger.debug(f"YouTube poll error: {e}")

            # Brief yield before next poll (pytchat handles its own timing)
            await asyncio.sleep(0.5)

        self._flush_batch()

    def _process_extra_events(self) -> None:
        """Process moderation events, room state changes, and system messages."""
        if not self._processor:
            return

        # Emit moderation events (deletions, bans)
        for event in self._processor.pop_moderation_events():
            self._emit_moderation(event)

        # Emit room state changes (slow mode, members-only)
        for state in self._processor.pop_room_state_changes():
            self._emit_room_state(state)

        # Add mode change system messages to the batch
        for msg_id, text in self._processor.pop_system_messages():
            sys_msg = ChatMessage(
                id=msg_id or str(uuid.uuid4()),
                user=ChatUser(
                    id="youtube-system",
                    name="YouTube",
                    display_name="YouTube",
                    platform=StreamPlatform.YOUTUBE,
                ),
                text="",
                timestamp=datetime.now(timezone.utc),
                platform=StreamPlatform.YOUTUBE,
                is_system=True,
                system_text=text,
            )
            self._message_batch.append(sys_msg)

    def _parse_badges(self, item) -> list[ChatBadge]:
        """Parse badge information from a pytchat item's author."""
        badges: list[ChatBadge] = []
        author = item.author if hasattr(item, "author") else None

        # pytchat exposes these as direct attributes on the item
        is_owner = getattr(item, "isChatOwner", False) or (
            getattr(author, "isChatOwner", False) if author else False
        )
        is_moderator = getattr(item, "isChatModerator", False) or (
            getattr(author, "isChatModerator", False) if author else False
        )
        is_sponsor = getattr(item, "isChatSponsor", False) or (
            getattr(author, "isChatSponsor", False) if author else False
        )
        is_verified = getattr(item, "isVerified", False) or (
            getattr(author, "isVerified", False) if author else False
        )
        badge_url = getattr(item, "badgeUrl", "") or (
            getattr(author, "badgeUrl", "") if author else ""
        )

        if is_owner:
            badges.append(ChatBadge(id="owner", name="Owner", image_url=""))
        if is_moderator:
            badges.append(ChatBadge(id="moderator", name="Moderator", image_url=""))
        if is_sponsor:
            badges.append(ChatBadge(id="member", name="Member", image_url=badge_url or ""))
        if is_verified:
            badges.append(ChatBadge(id="verified", name="Verified", image_url=""))

        return badges

    def _parse_message_ex(self, item) -> tuple[str, list[tuple[int, int, ChatEmote]]]:
        """Parse messageEx for text with inline emote/emoji positions.

        pytchat's messageEx is a list where each element is either:
        - A string (text segment)
        - A dict with 'id', 'txt', 'url' (emoji/emote)

        Returns:
            Tuple of (full text, list of (start, end, ChatEmote))
        """
        message_ex = getattr(item, "messageEx", None)
        if not message_ex or not isinstance(message_ex, list):
            # Fall back to plain message
            text = getattr(item, "message", "") or ""
            return text, []

        full_text = ""
        emote_positions: list[tuple[int, int, ChatEmote]] = []

        for segment in message_ex:
            if isinstance(segment, str):
                full_text += segment
            elif isinstance(segment, dict):
                emote_id = segment.get("id", "")
                emote_txt = segment.get("txt", "")
                emote_url = segment.get("url", "")

                if emote_txt:
                    start = len(full_text)
                    full_text += emote_txt
                    end = len(full_text)

                    if emote_url:
                        specs: dict[int, ImageSpec] = {}
                        for scale in (1, 2, 3):
                            key = f"emote:youtube:{emote_id or emote_txt}@{scale}x"
                            specs[scale] = ImageSpec(
                                scale=scale,
                                key=key,
                                url=emote_url,
                            )
                        emote = ChatEmote(
                            id=emote_id or emote_txt,
                            name=emote_txt,
                            url_template=emote_url,
                            provider="youtube",
                            image_set=ImageSet(specs),
                        )
                        emote_positions.append((start, end, emote))

        return full_text, emote_positions

    def _parse_pytchat_item(self, item) -> ChatMessage | None:
        """Parse a pytchat chat item into a ChatMessage."""
        try:
            # Parse badges
            badges = self._parse_badges(item)

            # Parse user info from author object
            author = getattr(item, "author", None)
            if author:
                author_name = getattr(author, "name", "Unknown") or "Unknown"
                author_id = getattr(author, "channelId", "") or str(uuid.uuid4())
            else:
                author_name = getattr(item, "authorName", "Unknown") or "Unknown"
                author_id = getattr(item, "authorChannelId", "") or str(uuid.uuid4())

            # Set username color based on role (matching YouTube web chat)
            user_color = None
            if any(b.id == "owner" for b in badges):
                user_color = "#ffd600"  # Gold for channel owner
            elif any(b.id == "moderator" for b in badges):
                user_color = "#5e84f1"  # Blue for moderators
            elif any(b.id == "member" for b in badges):
                user_color = "#2ba640"  # Green for members

            user = ChatUser(
                id=author_id,
                name=author_name,
                display_name=author_name,
                platform=StreamPlatform.YOUTUBE,
                color=user_color,
                badges=badges,
            )

            # Parse timestamp (pytchat timestamp is in milliseconds)
            timestamp = datetime.now(timezone.utc)
            if hasattr(item, "timestamp") and item.timestamp:
                try:
                    timestamp = datetime.fromtimestamp(int(item.timestamp) / 1000, tz=timezone.utc)
                except (ValueError, OSError, TypeError):
                    pass

            # Parse message text and emotes using messageEx
            text, emote_positions = self._parse_message_ex(item)

            # Check message type for SuperChat/membership events
            item_type = getattr(item, "type", "textMessage")
            if item_type != "textMessage":
                logger.info(f"YouTube special event: {item_type} from {author_name}")
            is_hype_chat = False
            hype_chat_amount = ""
            hype_chat_currency = ""
            hype_chat_level = ""
            is_system = False
            system_text = ""

            if item_type in ("superChat", "superSticker"):
                is_hype_chat = True
                amount_value = float(getattr(item, "amountValue", 0) or 0)
                hype_chat_amount = getattr(item, "amountString", "") or ""
                hype_chat_currency = getattr(item, "currency", "") or ""
                hype_chat_level = _get_superchat_tier(amount_value)

                # SuperChat/SuperSticker with no text gets a placeholder
                if not text:
                    if item_type == "superSticker":
                        text = "[SuperSticker]"
                    else:
                        text = hype_chat_amount or "[SuperChat]"

            elif item_type == "newSponsor":
                is_system = True
                system_text = f"{user.display_name} just became a member!"
                if not text:
                    text = system_text

            if not text:
                return None

            return ChatMessage(
                id=getattr(item, "id", str(uuid.uuid4())),
                user=user,
                text=text,
                timestamp=timestamp,
                platform=StreamPlatform.YOUTUBE,
                emote_positions=emote_positions,
                is_hype_chat=is_hype_chat,
                hype_chat_amount=hype_chat_amount,
                hype_chat_currency=hype_chat_currency,
                hype_chat_level=hype_chat_level,
                is_system=is_system,
                system_text=system_text,
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
        self._processor = None
