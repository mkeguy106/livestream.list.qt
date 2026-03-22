"""YouTube chat connection using pytchat with InnerTube message sending."""

import asyncio
import hashlib
import logging
import re
import time
import unicodedata
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


# Regex to match :emoji_shortcode: patterns (lowercase letters, digits, underscores)
_EMOJI_SHORTCODE_RE = re.compile(r":([a-z][a-z0-9_]*[a-z0-9]):")

# Shortcodes where YouTube name diverges from the official Unicode character name
_EMOJI_FALLBACK: dict[str, str] = {
    "thumbsup": "\U0001f44d",
    "thumbs_up": "\U0001f44d",
    "thumbsdown": "\U0001f44e",
    "thumbs_down": "\U0001f44e",
    "heart": "\u2764\ufe0f",
    "red_heart": "\u2764\ufe0f",
    "broken_heart": "\U0001f494",
    "100": "\U0001f4af",
    "pray": "\U0001f64f",
    "clap": "\U0001f44f",
    "wave": "\U0001f44b",
    "raised_hands": "\U0001f64c",
    "muscle": "\U0001f4aa",
    "eyes": "\U0001f440",
    "joy": "\U0001f602",
    "rofl": "\U0001f923",
    "sob": "\U0001f62d",
    "heart_eyes": "\U0001f60d",
    "kissing_heart": "\U0001f618",
    "thinking": "\U0001f914",
    "flushed": "\U0001f633",
    "scream": "\U0001f631",
    "poop": "\U0001f4a9",
    "skull": "\U0001f480",
    "ghost": "\U0001f47b",
    "tada": "\U0001f389",
    "sparkles": "\u2728",
    "star": "\u2b50",
    "sunny": "\u2600\ufe0f",
    "cloud": "\u2601\ufe0f",
    "snowflake": "\u2744\ufe0f",
    "zap": "\u26a1",
    "ocean": "\U0001f30a",
    "smiley": "\U0001f603",
    "smile": "\U0001f604",
    "grin": "\U0001f601",
    "laughing": "\U0001f606",
    "wink": "\U0001f609",
    "blush": "\U0001f60a",
    "yum": "\U0001f60b",
    "sunglasses": "\U0001f60e",
    "sweat_smile": "\U0001f605",
    "unamused": "\U0001f612",
    "pensive": "\U0001f614",
    "confused": "\U0001f615",
    "worried": "\U0001f61f",
    "angry": "\U0001f620",
    "rage": "\U0001f621",
    "cry": "\U0001f622",
    "triumph": "\U0001f624",
    "sleeping": "\U0001f634",
    "mask": "\U0001f637",
    "see_no_evil": "\U0001f648",
    "hear_no_evil": "\U0001f649",
    "speak_no_evil": "\U0001f64a",
    "fire": "\U0001f525",
    "boom": "\U0001f4a5",
    "sweat_drops": "\U0001f4a6",
    "dash": "\U0001f4a8",
    "warning": "\u26a0\ufe0f",
    "no_entry": "\u26d4",
    "x": "\u274c",
    "o": "\u2b55",
    "heavy_check_mark": "\u2714\ufe0f",
    "skull_and_crossbones": "\u2620\ufe0f",
    "rocket": "\U0001f680",
    "rainbow": "\U0001f308",
    "money_mouth_face": "\U0001f911",
    "partying_face": "\U0001f973",
    "stuck_out_tongue": "\U0001f61b",
    "stuck_out_tongue_winking_eye": "\U0001f61c",
    "nerd_face": "\U0001f913",
    "face_with_monocle": "\U0001f9d0",
    "pleading_face": "\U0001f97a",
    "smiling_face_with_tear": "\U0001f972",
    "saluting_face": "\U0001fae1",
    "melting_face": "\U0001fae0",
    "hot_face": "\U0001f975",
    "cold_face": "\U0001f976",
    "shushing_face": "\U0001f92b",
    "face_with_hand_over_mouth": "\U0001f92d",
    "lying_face": "\U0001f925",
    "clown_face": "\U0001f921",
    "smiling_imp": "\U0001f608",
    "imp": "\U0001f47f",
    "handshake": "\U0001f91d",
    "crossed_fingers": "\U0001f91e",
    "ok_hand": "\U0001f44c",
    "pinching_hand": "\U0001f90f",
    "v": "\u270c\ufe0f",
    "point_up": "\u261d\ufe0f",
    "point_down": "\U0001f447",
    "point_left": "\U0001f448",
    "point_right": "\U0001f449",
    "middle_finger": "\U0001f595",
    "raised_hand": "\u270b",
    "crown": "\U0001f451",
    "gem": "\U0001f48e",
    "ring": "\U0001f48d",
    "moneybag": "\U0001f4b0",
    "dollar": "\U0001f4b5",
    "pizza": "\U0001f355",
    "beer": "\U0001f37a",
    "beers": "\U0001f37b",
    "champagne": "\U0001f37e",
    "trophy": "\U0001f3c6",
    "medal": "\U0001f3c5",
    "soccer": "\u26bd",
    "basketball": "\U0001f3c0",
    "football": "\U0001f3c8",
    "baseball": "\u26be",
    "video_game": "\U0001f3ae",
    "joystick": "\U0001f579\ufe0f",
    "musical_note": "\U0001f3b5",
    "notes": "\U0001f3b6",
    "microphone": "\U0001f3a4",
    "headphones": "\U0001f3a7",
    "tv": "\U0001f4fa",
    "camera": "\U0001f4f7",
    "movie_camera": "\U0001f3a5",
    "skull_crossbones": "\u2620\ufe0f",
    "gg": "\U0001f1ec\U0001f1ec",
}


def _shortcode_to_unicode(name: str) -> str | None:
    """Convert an emoji shortcode name (without colons) to a Unicode character."""
    if name in _EMOJI_FALLBACK:
        return _EMOJI_FALLBACK[name]
    # Try unicodedata lookup: two_hearts → "TWO HEARTS"
    unicode_name = name.upper().replace("_", " ")
    try:
        return unicodedata.lookup(unicode_name)
    except KeyError:
        pass
    # Try with common suffixes that Unicode names use
    for suffix in (" FACE", " SIGN", " MARK"):
        try:
            return unicodedata.lookup(unicode_name + suffix)
        except KeyError:
            pass
    return None


def _replace_emoji_shortcodes(text: str) -> str:
    """Replace :emoji_name: patterns in text with Unicode characters."""

    def _replace(m: re.Match) -> str:
        char = _shortcode_to_unicode(m.group(1))
        return char if char else m.group(0)

    return _EMOJI_SHORTCODE_RE.sub(_replace, text)


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


def _build_nick_variants(display_name: str, login_name: str = "") -> list[str]:
    """Build a list of lowercase name variants for YouTube mention matching.

    YouTube @mentions use the channel handle (e.g. ``@angeloftheodd``), which
    may differ from the display name (e.g. "Angel Of The Odd").  We generate
    normalised forms so that mentions are detected regardless of spacing or
    casing.

    Args:
        display_name: The user's YouTube display name (from page HTML).
        login_name: Optional explicit YouTube handle/username from settings.
    """
    variants: set[str] = set()

    # Explicit handle from settings is the most reliable source
    if login_name:
        handle = login_name.strip().lstrip("@")
        if handle:
            variants.add(handle.lower())
            # Without dots/underscores/hyphens
            normalised = re.sub(r"[._-]", "", handle).lower()
            if normalised != handle.lower():
                variants.add(normalised)

    if display_name:
        dn = display_name.strip().lstrip("@")
        variants.add(dn.lower())
        # Without spaces (covers "Angel Of The Odd" -> "angeloftheodd")
        no_spaces = dn.replace(" ", "")
        if no_spaces.lower() != dn.lower():
            variants.add(no_spaces.lower())
        # Without spaces AND special chars (covers punctuation differences)
        alpha_only = re.sub(r"[^a-z0-9]", "", dn.lower())
        if alpha_only and alpha_only not in variants:
            variants.add(alpha_only)

    # Discard empty / too-short variants (avoid false positives)
    result = [v for v in variants if len(v) >= 2]
    if result:
        logger.debug(f"YouTube mention variants: {result}")
    return result


def _extract_handle_for_channel(channel_id: str, html: str) -> str:
    """Extract the channel handle for a specific channel ID from page HTML.

    Searches for ``browseEndpoint`` objects that pair the given channel ID
    with a ``canonicalBaseUrl`` containing the handle.
    """
    escaped = re.escape(channel_id)
    # {"browseId":"UCxxx","canonicalBaseUrl":"/@handle"}
    m = re.search(
        rf'"browseId"\s*:\s*"{escaped}"\s*,\s*"canonicalBaseUrl"\s*:\s*"/@([^"]+)"',
        html,
    )
    if not m:
        # Reverse field order
        m = re.search(
            rf'"canonicalBaseUrl"\s*:\s*"/@([^"]+)"\s*,\s*"browseId"\s*:\s*"{escaped}"',
            html,
        )
    return m.group(1).strip() if m else ""


class YouTubeChatConnection(BaseChatConnection):
    """YouTube live chat connection.

    Uses pytchat library for receiving live chat messages.
    Supports InnerTube API for sending messages when cookies are configured.
    """

    def __init__(self, youtube_settings: YouTubeSettings | None = None, parent=None):
        super().__init__(parent)
        self._pytchat = None  # pytchat.LiveChat instance
        self._processor: LivestreamListProcessor | None = None
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
        self._slow_mode_seconds: int = 0  # Known slow mode interval from room state
        self._last_send_time: float = 0.0  # monotonic timestamp of last successful send
        self._nick_variants: list[str] = []  # All name forms for mention matching
        self._http_session: object | None = None  # Persistent aiohttp.ClientSession

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
            import pytchat  # type: ignore[import-untyped]
        except ImportError:
            self._emit_error("pytchat library not installed. Install with: pip install pytchat")
            return

        # Parse cookies for sending support
        yt_settings = self._youtube_settings
        if yt_settings and yt_settings.cookies:
            self._cookies = parse_cookie_string(yt_settings.cookies)
            if REQUIRED_COOKIE_KEYS.issubset(self._cookies.keys()):
                await self._extract_send_params(video_id)

        # Build mention variants from login_name even without cookies
        if not self._nick_variants and yt_settings:
            login = getattr(yt_settings, "login_name", "")
            if login:
                self._nick_variants = _build_nick_variants("", login)

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
        await self._close_http_session()

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

        # Client-side slow mode prevention: block if we're still in the cooldown
        if self._slow_mode_seconds > 0 and self._last_send_time > 0:
            remaining = self._get_slow_mode_remaining()
            if remaining > 0:
                self._emit_error(f"Slow mode: wait {remaining}s before sending again.")
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

        if result == "slow_mode":
            if self._slow_mode_seconds > 0:
                self._emit_error(
                    f"Slow mode: wait {self._slow_mode_seconds}s before sending again."
                )
            else:
                self._emit_error("Slow mode is active. Please wait before sending again.")
            return False

        if result == "rejected":
            # Error already emitted by _do_send_yt with YouTube's reason
            return False

        # Generic failure
        self._emit_error("Failed to send YouTube message")
        return False

    def _get_slow_mode_remaining(self) -> int:
        """Calculate approximate seconds remaining before next send is allowed."""
        if self._slow_mode_seconds <= 0 or self._last_send_time <= 0:
            return 0
        elapsed = time.monotonic() - self._last_send_time
        remaining = self._slow_mode_seconds - int(elapsed)
        return max(remaining, 0)

    @staticmethod
    def _extract_error_text(error_msg: dict) -> str:
        """Extract human-readable text from YouTube's errorMessage object.

        YouTube uses {"simpleText": "..."} or {"runs": [{"text": "..."}, ...]}.
        """
        if isinstance(error_msg, dict):
            simple = error_msg.get("simpleText")
            if simple:
                return str(simple)
            runs = error_msg.get("runs")
            if isinstance(runs, list):
                return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
        return ""

    @staticmethod
    def _is_slow_mode_error(text: str) -> bool:
        """Check if an API response indicates a slow mode / rate limit violation."""
        lower = text.lower()
        return any(
            kw in lower
            for kw in (
                "slow mode",
                "slowmode",
                "too quickly",
                "too fast",
                "livechatslowmode",
                "wait before sending",
                "sending messages too",
                "rate limit exceeded",
                "failed_precondition",
            )
        )

    @staticmethod
    def _parse_slow_mode_from_response(data: dict) -> int:
        """Extract slow mode seconds from a YouTube send_message response.

        Searches all string values in the response for a number followed by
        "second"/"sec", and also checks timeoutDurationUsec.

        Returns seconds (>0) if found, 0 otherwise.
        """
        import json as _json

        # Walk the entire response looking for seconds in text fields
        resp_str = _json.dumps(data)
        sec_match = re.search(r"(\d+)\s*(?:second|sec)", resp_str, re.IGNORECASE)
        if sec_match:
            return int(sec_match.group(1))

        # Fallback: use timeoutDurationUsec (microseconds → seconds)
        timeout_usec = data.get("timeoutDurationUsec")
        if timeout_usec:
            try:
                return int(timeout_usec) // 1_000_000
            except (ValueError, TypeError):
                pass
        return 0

    async def _get_http_session(self):
        """Get or create a persistent aiohttp session for YouTube API calls.

        Reusing a single session keeps TCP connections pooled and ensures
        YouTube sees consistent visitor/session state across requests,
        preventing anti-spam filters from dropping messages.
        """
        import aiohttp

        if self._http_session is None or getattr(self._http_session, "closed", True):
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._http_session

    async def _close_http_session(self) -> None:
        """Close the persistent HTTP session."""
        if self._http_session and not getattr(self._http_session, "closed", True):
            await self._http_session.close()
            self._http_session = None

    async def _do_send_yt(self, text: str) -> bool | str:
        """Perform the actual YouTube send HTTP request.

        Returns:
            True on success, or a string indicating the failure type:
            "auth_expired", "rate_limited", "slow_mode", "server_error",
            or False.
        """
        try:
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

            session = await self._get_http_session()
            async with session.post(
                url,
                json=body,
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    # Parse response to detect rejection.
                    # YouTube returns 200 even when rejecting messages:
                    #   Success → actions contain "addChatItemAction"
                    #   Rejected → "dimChatItemAction" + "errorMessage"
                    try:
                        import json as _json

                        resp_text = await resp.text()
                        data = _json.loads(resp_text)
                        actions = data.get("actions", [])
                        has_add = any("addChatItemAction" in a for a in actions)
                        has_dim = any("dimChatItemAction" in a for a in actions)
                        error_msg_obj = data.get("errorMessage")

                        if has_dim or error_msg_obj:
                            # Explicit rejection from YouTube
                            error_text = (
                                self._extract_error_text(error_msg_obj) if error_msg_obj else ""
                            )
                            if error_text and self._is_slow_mode_error(error_text):
                                logger.warning(f"YouTube send rejected (slow mode): {error_text}")
                                slow_sec = self._parse_slow_mode_from_response(data)
                                if slow_sec > 0:
                                    self._slow_mode_seconds = slow_sec
                                    self._emit_room_state(ChatRoomState(slow=slow_sec))
                                return "slow_mode"

                            # If the message was ALSO added (has_add), it's
                            # not actually rejected — some responses contain
                            # both addChatItemAction AND errorMessage/dim.
                            if has_add:
                                logger.info(
                                    "YouTube send has both addChatItemAction "
                                    "and dim/error — treating as success"
                                )
                            else:
                                # Non-slow-mode rejection
                                if error_text:
                                    self._emit_error(f"YouTube: {error_text}")
                                else:
                                    self._emit_error("Message rejected by YouTube")
                                return "rejected"

                        if not has_add:
                            # No addChatItemAction means YouTube silently
                            # rejected the message (rate limit, spam filter,
                            # etc.) — the message will NOT appear in chat.
                            logger.warning(
                                "YouTube send got 200 without addChatItemAction "
                                f"(keys: {list(data.keys())})"
                            )
                            self._emit_error(
                                "Message not delivered — YouTube may be "
                                "rate limiting. Try again in a moment."
                            )
                            return "rejected"

                        # Success — extract slow mode interval from response
                        # timeoutDurationUsec is present when slow mode is active
                        timeout_usec = data.get("timeoutDurationUsec")
                        if timeout_usec:
                            timeout_sec = int(timeout_usec) // 1_000_000
                            if timeout_sec > 0:
                                self._slow_mode_seconds = timeout_sec
                                self._emit_room_state(ChatRoomState(slow=timeout_sec))

                    except Exception as e:
                        logger.debug(f"YouTube send response parse error: {e}")
                    self._last_send_time = time.monotonic()
                    return True
                elif resp.status in (400, 409):
                    error_text = await resp.text()
                    if self._is_slow_mode_error(error_text):
                        logger.warning(f"YouTube send rejected: slow mode ({resp.status})")
                        return "slow_mode"
                    logger.warning(
                        f"YouTube send_message failed ({resp.status}): {error_text[:200]}"
                    )
                    return False
                elif resp.status in (401, 403):
                    error_text = await resp.text()
                    # Check for slow mode before treating as auth failure
                    if self._is_slow_mode_error(error_text):
                        logger.warning(f"YouTube send rejected: slow mode ({resp.status})")
                        return "slow_mode"
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
                    if self._is_slow_mode_error(error_text):
                        logger.warning(f"YouTube send rejected: slow mode ({resp.status})")
                        return "slow_mode"
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

            session = await self._get_http_session()
            async with session.get(
                url,
                headers=headers,
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
                logger.debug(f"YouTube display name: {self._nick}")

            # Detect channel handle
            detected_handle = ""
            # YouTube often returns the handle (with @) as the authorName
            if self._nick and self._nick.startswith("@"):
                detected_handle = self._nick[1:]
            # Fallback: try browseEndpoint pairing with datasyncId (UC channel IDs)
            if not detected_handle and self._datasync_id:
                channel_id = self._datasync_id.split("||")[0].strip()
                if channel_id.startswith("UC"):
                    detected_handle = _extract_handle_for_channel(channel_id, html)
            if detected_handle:
                logger.info(f"YouTube handle detected: @{detected_handle}")

            # Auto-save detected handle to settings (first time only)
            yt_s = self._youtube_settings
            if detected_handle and yt_s and not getattr(yt_s, "login_name", ""):
                yt_s.login_name = detected_handle

            # Build mention-matching name variants
            login = getattr(yt_s, "login_name", "") if yt_s else ""
            self._nick_variants = _build_nick_variants(self._nick if author_match else "", login)

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
            slow_seconds = 0
            slow_match = re.search(
                r'"slowModeRenderer"\s*:\s*\{.{0,500}?"slowModeDurationSeconds"\s*:\s*"?(\d+)',
                html,
                re.DOTALL,
            )
            if slow_match:
                slow_seconds = int(slow_match.group(1))
            else:
                # Fallback: search for slowModeDurationSeconds anywhere
                slow_match2 = re.search(r'"?slowModeDurationSeconds"?\s*[:=]\s*"?(\d+)', html)
                if slow_match2:
                    slow_seconds = int(slow_match2.group(1))

            if slow_seconds > 0:
                self._slow_mode_seconds = slow_seconds
                self._emit_room_state(ChatRoomState(slow=slow_seconds))
                logger.info(f"YouTube slow mode detected from page: {slow_seconds}s")

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
                if self._should_flush_batch():
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
            if state.slow >= 0:
                self._slow_mode_seconds = state.slow
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

        Emoji shortcodes (e.g. :two_hearts:) without image URLs are converted
        to Unicode characters so they render natively in the chat.

        Returns:
            Tuple of (full text, list of (start, end, ChatEmote))
        """
        message_ex = getattr(item, "messageEx", None)
        if not message_ex or not isinstance(message_ex, list):
            # Fall back to plain message, converting any emoji shortcodes
            text = getattr(item, "message", "") or ""
            return _replace_emoji_shortcodes(text), []

        full_text = ""
        emote_positions: list[tuple[int, int, ChatEmote]] = []

        for segment in message_ex:
            if isinstance(segment, str):
                # Convert any :emoji_shortcode: patterns in text segments
                full_text += _replace_emoji_shortcodes(segment)
            elif isinstance(segment, dict):
                emote_id = segment.get("id", "")
                emote_txt = segment.get("txt", "")
                emote_url = segment.get("url", "")

                if emote_txt:
                    if emote_url:
                        start = len(full_text)
                        full_text += emote_txt
                        end = len(full_text)

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
                    else:
                        # No image URL — convert shortcode to Unicode emoji
                        name = emote_txt.strip(":")
                        unicode_char = _shortcode_to_unicode(name) if name else None
                        full_text += unicode_char if unicode_char else emote_txt

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

    def _cleanup_pytchat(self) -> None:
        """Clean up pytchat instance."""
        if self._pytchat:
            try:
                self._pytchat.terminate()
            except Exception:
                pass
            self._pytchat = None
        self._processor = None
