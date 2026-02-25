"""Worker threads for chat connections, emote fetching, and EventSub."""

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.models import StreamPlatform
from .connections.base import BaseChatConnection
from .emotes.provider import BTTVProvider, FFZProvider, SevenTVProvider, TwitchProvider
from .models import ChatEmote, ChatMessage, ChatUser, HypeTrainEvent

logger = logging.getLogger(__name__)


class AsyncTaskWorker(QThread):
    """One-shot worker that runs a sync or async callable in a background thread.

    The callable should take no arguments — use a closure to capture context.
    """

    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        task: Callable[[], Any],
        *,
        parent=None,
        error_log_level: int = logging.ERROR,
    ):
        super().__init__(parent)
        self._task = task
        self._error_log_level = error_log_level

    def run(self):
        try:
            if inspect.iscoroutinefunction(self._task):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(self._task())
                finally:
                    loop.close()
            else:
                result = self._task()
            self.result_ready.emit(result)
        except Exception as e:
            logger.log(self._error_log_level, f"AsyncTaskWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))


async def _fetch_global_emotes(providers: list[str], oauth_token: str, client_id: str) -> dict:
    """Fetch Twitch + third-party global emotes."""
    import aiohttp

    twitch_globals: list[ChatEmote] = []
    common_globals: list[ChatEmote] = []

    async with aiohttp.ClientSession() as session:
        twitch_provider = TwitchProvider(
            oauth_token=oauth_token,
            client_id=client_id,
        )
        try:
            twitch_globals = await twitch_provider.get_global_emotes(session=session)
            logger.debug(f"Fetched {len(twitch_globals)} Twitch global emotes")
        except Exception as e:
            logger.debug(f"Failed to fetch Twitch global emotes: {e}")

        provider_map = {
            "7tv": SevenTVProvider,
            "bttv": BTTVProvider,
            "ffz": FFZProvider,
        }
        for name in providers:
            provider_cls = provider_map.get(name)
            if not provider_cls:
                continue
            provider = provider_cls()
            try:
                emotes = await provider.get_global_emotes(session=session)
                common_globals.extend(emotes)
                logger.debug(f"Fetched {len(emotes)} global emotes from {name}")
            except Exception as e:
                logger.debug(f"Failed to fetch global emotes from {name}: {e}")

    return {"twitch": twitch_globals, "common": common_globals}


async def _fetch_user_emotes(oauth_token: str, client_id: str) -> list[ChatEmote]:
    """Fetch Twitch user emotes for the authenticated user."""
    import aiohttp

    if not oauth_token:
        return []

    # Resolve authenticated user ID
    user_id = None
    if oauth_token and client_id:
        try:
            headers = {
                "Authorization": f"Bearer {oauth_token}",
                "Client-Id": client_id,
            }
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    "https://api.twitch.tv/helix/users",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        users = data.get("data", [])
                        if users:
                            user_id = users[0].get("id", "") or None
        except Exception as e:
            logger.debug(f"Failed to get authenticated user ID: {e}")

    if not user_id:
        return []

    twitch_provider = TwitchProvider(
        oauth_token=oauth_token,
        client_id=client_id,
    )
    try:
        async with aiohttp.ClientSession() as session:
            emotes = await twitch_provider.get_user_emotes(user_id, session=session)
        logger.debug(f"Fetched {len(emotes)} user emotes")
        return emotes
    except Exception as e:
        logger.debug(f"Failed to fetch user emotes: {e}")
        return []


class ChatConnectionWorker(QThread):
    """Worker thread that runs a chat connection's async event loop.

    Wraps connect_to_channel in a reconnect loop using the base connection's
    backoff infrastructure. After an unintentional disconnect the worker will
    retry up to ``_max_reconnect_attempts`` times with exponential backoff.
    """

    reconnecting = Signal(float)  # delay in seconds before next attempt
    reconnect_failed = Signal()  # exhausted all attempts

    def __init__(self, connection: BaseChatConnection, channel_id: str, parent=None, **kwargs):
        super().__init__(parent)
        self.connection = connection
        self.channel_id = channel_id
        self.kwargs = dict(kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False
        self._wake_event: asyncio.Event | None = None

    def run(self):
        """Run the connection in a new event loop with reconnect support."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_with_reconnect())
        except Exception as e:
            if not self._should_stop:
                logger.error(f"Chat worker error: {e}")
                self.connection._emit_error(str(e))
        finally:
            self._loop.close()
            self._loop = None

    async def _run_with_reconnect(self):
        """Connect, and reconnect on unintentional disconnects."""
        self._wake_event = asyncio.Event()
        attempts = 0

        while not self._should_stop:
            try:
                await self.connection.connect_to_channel(self.channel_id, **self.kwargs)
            except Exception as e:
                if not self._should_stop:
                    logger.error(f"Chat worker error: {e}")
                    self.connection._emit_error(str(e))

            # After connect_to_channel returns, decide whether to reconnect
            if self._should_stop or not self.connection._should_reconnect:
                break

            attempts += 1
            max_attempts = self.connection._max_reconnect_attempts
            if max_attempts and attempts >= max_attempts:
                logger.warning(f"Chat reconnect exhausted ({attempts} attempts)")
                self.reconnect_failed.emit()
                break

            delay = self.connection._get_next_backoff()
            logger.info(f"Chat reconnecting in {delay:.1f}s (attempt {attempts})")
            self.reconnecting.emit(delay)

            # Interruptible sleep — wake_event is set by request_immediate_reconnect
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                # Woken early — reset backoff for immediate retry
                self.connection._reset_backoff()
                attempts = 0
            except asyncio.TimeoutError:
                pass  # normal backoff elapsed

    def request_immediate_reconnect(self):
        """Wake the backoff sleep so the worker reconnects now (thread-safe)."""
        if self._loop and self._wake_event is not None:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    def update_kwargs(self, **new_kwargs):
        """Update connection kwargs (e.g. YouTube video_id on stream restart)."""
        self.kwargs.update(new_kwargs)

    def stop(self):
        """Request the worker to stop (no reconnect)."""
        self._should_stop = True
        self.connection._should_reconnect = False
        if self._wake_event is not None and self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._wake_event.set)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


class EmoteFetchWorker(QThread):
    """Worker thread that fetches channel emotes and badge data from providers."""

    emotes_fetched = Signal(str, list)  # channel_key, list[ChatEmote]
    badges_fetched = Signal(str, dict)  # channel_key, {badge_id: image_url}

    def __init__(
        self,
        channel_key: str,
        platform: str,
        channel_id: str,
        providers: list[str],
        oauth_token: str = "",
        client_id: str = "",
        fetch_emotes: bool = True,
        fetch_badges: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.platform = platform
        self.channel_id = channel_id
        self.providers = providers
        self.oauth_token = oauth_token
        self.client_id = client_id
        self.fetch_emotes = fetch_emotes
        self.fetch_badges = fetch_badges

    def run(self):
        """Fetch channel emotes and badges."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            resolved_id = self.channel_id
            if self.platform == "twitch":
                numeric_id = loop.run_until_complete(self._resolve_twitch_user_id())
                if numeric_id:
                    resolved_id = numeric_id

            if self.fetch_emotes:
                emotes = loop.run_until_complete(self._fetch_channel_emotes(resolved_id))
                self.emotes_fetched.emit(self.channel_key, emotes)

            if self.fetch_badges and self.platform == "twitch":
                self._resolved_broadcaster_id = resolved_id
                badge_map = loop.run_until_complete(self._fetch_twitch_badges())
                if badge_map:
                    self.badges_fetched.emit(self.channel_key, badge_map)
        except Exception as e:
            logger.error(f"Emote/badge fetch error: {e}")
        finally:
            loop.close()

    async def _resolve_twitch_user_id(self) -> str | None:
        """Resolve a Twitch login name to numeric user ID.

        Tries Helix API first (if OAuth available), then falls back to public IVR API.
        """
        import aiohttp

        # If channel_id is already numeric, no need to resolve
        if self.channel_id.isdigit():
            return self.channel_id

        # Try Helix API if we have credentials
        if self.oauth_token and self.client_id:
            try:
                headers = {
                    "Authorization": f"Bearer {self.oauth_token}",
                    "Client-Id": self.client_id,
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(
                        "https://api.twitch.tv/helix/users",
                        params={"login": self.channel_id},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            users = data.get("data", [])
                            if users:
                                user_id = users[0].get("id", "")
                                if user_id:
                                    logger.debug(
                                        f"Resolved Twitch login '{self.channel_id}' "
                                        f"to user ID {user_id} (Helix)"
                                    )
                                    return user_id
            except Exception as e:
                logger.debug(f"Helix API failed for {self.channel_id}: {e}")

        # Fallback to public IVR API (no auth required)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.ivr.fi/v2/twitch/user?login={self.channel_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and data:
                            user_id = data[0].get("id", "")
                            if user_id:
                                logger.debug(
                                    f"Resolved Twitch login '{self.channel_id}' "
                                    f"to user ID {user_id} (IVR)"
                                )
                                return user_id
        except Exception as e:
            logger.debug(f"IVR API failed for {self.channel_id}: {e}")

        return None

    async def _fetch_channel_emotes(self, channel_id: str) -> list[ChatEmote]:
        """Fetch channel emotes from all providers using a shared session."""
        import aiohttp

        all_emotes: list[ChatEmote] = []

        async with aiohttp.ClientSession() as session:
            # Fetch native platform emotes first
            if self.platform == "twitch":
                twitch_provider = TwitchProvider(
                    oauth_token=self.oauth_token,
                    client_id=self.client_id,
                )
                try:
                    channel_emotes = await twitch_provider.get_channel_emotes(
                        self.platform, channel_id, session=session
                    )
                    all_emotes.extend(channel_emotes)
                    logger.debug(f"Fetched {len(channel_emotes)} channel emotes from twitch")
                except Exception as e:
                    logger.debug(f"Failed to fetch channel emotes from twitch: {e}")

            # Fetch third-party emotes
            provider_map = {
                "7tv": SevenTVProvider,
                "bttv": BTTVProvider,
                "ffz": FFZProvider,
            }

            for name in self.providers:
                provider_cls = provider_map.get(name)
                if not provider_cls:
                    continue

                provider = provider_cls()
                try:
                    channel_emotes = await provider.get_channel_emotes(
                        self.platform, channel_id, session=session
                    )
                    all_emotes.extend(channel_emotes)
                    logger.debug(f"Fetched {len(channel_emotes)} channel emotes from {name}")
                except Exception as e:
                    logger.debug(f"Failed to fetch channel emotes from {name}: {e}")

        return all_emotes

    async def _fetch_twitch_badges(self) -> dict[str, tuple[str, str]]:
        """Fetch Twitch badge image URLs and titles (global + channel).

        Tries authenticated Helix API first, falls back to public badge API.
        Returns: {badge_id: (image_url, title)}
        """
        import aiohttp

        badge_map: dict[str, tuple[str, str]] = {}

        # Try Helix API if we have auth credentials
        if self.oauth_token and self.client_id:
            headers = {
                "Authorization": f"Bearer {self.oauth_token}",
                "Client-Id": self.client_id,
            }
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    # Global badges
                    async with session.get(
                        "https://api.twitch.tv/helix/chat/badges/global",
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for badge_set in data.get("data", []):
                                set_id = badge_set.get("set_id", "")
                                for version in badge_set.get("versions", []):
                                    vid = version.get("id", "")
                                    url = (
                                        version.get("image_url_2x")
                                        or version.get("image_url_1x")
                                        or ""
                                    )
                                    title = version.get("title", "")
                                    if set_id and vid and url:
                                        badge_map[f"{set_id}/{vid}"] = (url, title)
                        else:
                            logger.warning(
                                f"Helix badge API returned {resp.status}, trying public API"
                            )

                    # Channel badges (use resolved numeric ID)
                    broadcaster_id = getattr(self, "_resolved_broadcaster_id", self.channel_id)
                    async with session.get(
                        "https://api.twitch.tv/helix/chat/badges",
                        params={"broadcaster_id": broadcaster_id},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for badge_set in data.get("data", []):
                                set_id = badge_set.get("set_id", "")
                                for version in badge_set.get("versions", []):
                                    vid = version.get("id", "")
                                    url = (
                                        version.get("image_url_2x")
                                        or version.get("image_url_1x")
                                        or ""
                                    )
                                    title = version.get("title", "")
                                    if set_id and vid and url:
                                        badge_map[f"{set_id}/{vid}"] = (url, title)
            except Exception as e:
                logger.warning(f"Failed to fetch Twitch badges via Helix: {e}")

        # Fall back to public badge API if Helix didn't work
        if not badge_map:
            badge_map = await self._fetch_public_twitch_badges()

        logger.debug(f"Fetched {len(badge_map)} Twitch badge URLs")
        return badge_map

    async def _fetch_public_twitch_badges(self) -> dict[str, tuple[str, str]]:
        """Fetch Twitch badges from the public (unauthenticated) badge API."""
        import aiohttp

        badge_map: dict[str, tuple[str, str]] = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://badges.twitch.tv/v1/badges/global/display",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        badge_sets = data.get("badge_sets", {})
                        for set_id, set_data in badge_sets.items():
                            versions = set_data.get("versions", {})
                            for vid, version_data in versions.items():
                                url = (
                                    version_data.get("image_url_2x")
                                    or version_data.get("image_url_1x")
                                    or ""
                                )
                                title = version_data.get("title", "")
                                if url:
                                    badge_map[f"{set_id}/{vid}"] = (url, title)
                    else:
                        logger.warning(f"Public badge API returned {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to fetch Twitch badges from public API: {e}")

        return badge_map


EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"


class WhisperEventSubWorker(QThread):
    """Worker thread that connects to Twitch EventSub WebSocket for whisper delivery.

    Subscribes to user.whisper.message and emits incoming whispers as ChatMessage.
    """

    whisper_received = Signal(object)  # ChatMessage
    authenticated_as = Signal(str)  # login name of the authenticated user

    def __init__(self, oauth_token: str, client_id: str, parent=None):
        super().__init__(parent)
        self.oauth_token = oauth_token
        self.client_id = client_id
        self._should_stop = False

    def stop(self):
        self._should_stop = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_eventsub())
        except Exception as e:
            if not self._should_stop:
                logger.error(f"WhisperEventSub error: {e}")
        finally:
            loop.close()

    async def _run_eventsub(self):
        import aiohttp

        # Resolve our own user ID first
        user_id = await self._get_user_id()
        if not user_id:
            logger.warning("WhisperEventSub: could not resolve user ID, aborting")
            return

        async with aiohttp.ClientSession() as session:
            while not self._should_stop:
                try:
                    await self._connect_and_listen(session, user_id)
                except Exception as e:
                    if self._should_stop:
                        return
                    logger.warning(f"WhisperEventSub connection error: {e}, reconnecting in 5s")
                    await asyncio.sleep(5)

    async def _connect_and_listen(self, session, user_id: str):
        import aiohttp

        async with session.ws_connect(
            EVENTSUB_WS_URL, timeout=aiohttp.ClientTimeout(total=30)
        ) as ws:
            # Wait for welcome message (must arrive within 10s)
            welcome = await asyncio.wait_for(ws.receive_json(), timeout=15)
            if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                logger.warning(f"WhisperEventSub: unexpected first message: {welcome}")
                return

            session_id = welcome["payload"]["session"]["id"]
            keepalive_timeout = welcome["payload"]["session"].get("keepalive_timeout_seconds", 30)
            logger.info(f"WhisperEventSub connected, session={session_id}")

            # Subscribe to user.whisper.message
            ok = await self._subscribe(session, session_id, user_id)
            if not ok:
                return

            # Listen for events
            while not self._should_stop:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=keepalive_timeout + 10)
                except asyncio.TimeoutError:
                    logger.warning("WhisperEventSub: keepalive timeout, reconnecting")
                    return

                if msg.type == aiohttp.WSMsgType.TEXT:
                    import json

                    data = json.loads(msg.data)
                    msg_type = data.get("metadata", {}).get("message_type", "")

                    if msg_type == "notification":
                        self._handle_notification(data)
                    elif msg_type == "session_keepalive":
                        pass  # Expected, connection is healthy
                    elif msg_type == "session_reconnect":
                        # Server wants us to reconnect to a new URL
                        logger.info("WhisperEventSub: reconnect requested")
                        return
                    elif msg_type == "revocation":
                        reason = (
                            data.get("payload", {}).get("subscription", {}).get("status", "unknown")
                        )
                        logger.warning(f"WhisperEventSub: subscription revoked: {reason}")
                        return

                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    return

    async def _subscribe(self, session, session_id: str, user_id: str) -> bool:
        """Subscribe to user.whisper.message via Helix API."""
        import aiohttp

        headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "Client-Id": self.client_id,
            "Content-Type": "application/json",
        }
        payload = {
            "type": "user.whisper.message",
            "version": "1",
            "condition": {"user_id": user_id},
            "transport": {"method": "websocket", "session_id": session_id},
        }
        async with session.post(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 202):
                logger.info("WhisperEventSub: subscribed to user.whisper.message")
                return True
            body = await resp.text()
            logger.error(f"WhisperEventSub: subscribe failed (HTTP {resp.status}): {body}")
            return False

    def _handle_notification(self, data: dict) -> None:
        """Parse an EventSub whisper notification into a ChatMessage."""
        logger.info(f"WhisperEventSub: received notification: {data.get('metadata', {})}")
        event = data.get("payload", {}).get("event", {})
        whisper = event.get("whisper", {})

        from_user_id = event.get("from_user_id", "")
        from_user_name = event.get("from_user_name", "")
        from_user_login = event.get("from_user_login", "")
        text = whisper.get("text", "")
        whisper_id = event.get("whisper_id", str(uuid.uuid4()))
        logger.info(
            f"WhisperEventSub: whisper from {from_user_name} ({from_user_id}): {text[:50]!r}"
        )

        user = ChatUser(
            id=from_user_id,
            name=from_user_login,
            display_name=from_user_name or from_user_login,
            platform=StreamPlatform.TWITCH,
        )

        message = ChatMessage(
            id=whisper_id,
            user=user,
            text=text,
            timestamp=datetime.now(timezone.utc),
            platform=StreamPlatform.TWITCH,
            is_whisper=True,
        )

        self.whisper_received.emit(message)

    async def _get_user_id(self) -> str | None:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {self.oauth_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        user_id = data.get("user_id")
                        login = data.get("login", "?")
                        logger.info(
                            f"WhisperEventSub: authenticated as {login} (user_id={user_id})"
                        )
                        # Fetch properly-cased display_name from /helix/users
                        display_name = login
                        if user_id:
                            display_name = await self._fetch_display_name(session, user_id) or login
                        if display_name and display_name != "?":
                            self.authenticated_as.emit(display_name)
                        return user_id
        except Exception as e:
            logger.warning(f"WhisperEventSub: failed to get user ID: {e}")
        return None

    async def _fetch_display_name(self, session, user_id: str) -> str | None:
        """Fetch properly-cased display_name from /helix/users."""
        import aiohttp

        try:
            async with session.get(
                f"https://api.twitch.tv/helix/users?id={user_id}",
                headers={
                    "Authorization": f"Bearer {self.oauth_token}",
                    "Client-Id": self.client_id,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    users = data.get("data", [])
                    if users:
                        return users[0].get("display_name")
        except aiohttp.ClientError:
            pass
        return None


class HypeTrainEventSubWorker(QThread):
    """Worker thread that connects to Twitch EventSub WebSocket for hype train events.

    Supports dynamic subscribe/unsubscribe per channel via thread-safe methods.
    Subscribes to channel.hype_train.begin, .progress, and .end.
    """

    hype_train_event = Signal(str, object)  # channel_key, HypeTrainEvent

    def __init__(self, oauth_token: str, client_id: str, parent=None):
        super().__init__(parent)
        self._scope_warning_logged = False
        self.oauth_token = oauth_token
        self.client_id = client_id
        self._should_stop = False
        # channel_key -> broadcaster_id
        self._channels: dict[str, str] = {}
        self._pending_subscribe: list[tuple[str, str]] = []  # (channel_key, broadcaster_id)
        self._pending_unsubscribe: list[str] = []  # channel_keys
        import threading

        self._lock = threading.Lock()

    def subscribe_channel(self, channel_key: str, broadcaster_id: str) -> None:
        """Thread-safe request to subscribe to hype train events for a channel."""
        with self._lock:
            self._pending_subscribe.append((channel_key, broadcaster_id))

    def unsubscribe_channel(self, channel_key: str) -> None:
        """Thread-safe request to unsubscribe from a channel's hype train events."""
        with self._lock:
            self._pending_unsubscribe.append(channel_key)

    def stop(self):
        self._should_stop = True

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_eventsub())
        except Exception as e:
            if not self._should_stop:
                logger.error(f"HypeTrainEventSub error: {e}")
        finally:
            loop.close()

    async def _run_eventsub(self):
        import aiohttp

        backoff = 5
        async with aiohttp.ClientSession() as session:
            while not self._should_stop:
                try:
                    had_subs = await self._connect_and_listen(session)
                    if had_subs:
                        backoff = 5  # Reset on successful session
                    else:
                        # No subscriptions succeeded (not a mod of any channel).
                        # Use exponential backoff up to 5 minutes.
                        logger.debug(
                            f"HypeTrainEventSub: no active subscriptions, retrying in {backoff}s"
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 300)
                        continue
                except Exception as e:
                    if self._should_stop:
                        return
                    logger.warning(
                        f"HypeTrainEventSub connection error: {e}, reconnecting in {backoff}s"
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)

    async def _connect_and_listen(self, session) -> bool:
        import aiohttp

        async with session.ws_connect(
            EVENTSUB_WS_URL, timeout=aiohttp.ClientTimeout(total=30)
        ) as ws:
            welcome = await asyncio.wait_for(ws.receive_json(), timeout=15)
            if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                logger.warning(f"HypeTrainEventSub: unexpected first message: {welcome}")
                return False

            session_id = welcome["payload"]["session"]["id"]
            keepalive_timeout = welcome["payload"]["session"].get("keepalive_timeout_seconds", 30)
            logger.info(f"HypeTrainEventSub connected, session={session_id}")

            # Subscribe all currently tracked channels
            has_any_sub = False
            with self._lock:
                for channel_key, broadcaster_id in self._channels.items():
                    if await self._subscribe_channel(session, session_id, broadcaster_id):
                        has_any_sub = True
                self._pending_subscribe.clear()
                self._pending_unsubscribe.clear()

            while not self._should_stop:
                # Process pending subscribe/unsubscribe requests
                with self._lock:
                    subs = list(self._pending_subscribe)
                    self._pending_subscribe.clear()
                    unsubs = list(self._pending_unsubscribe)
                    self._pending_unsubscribe.clear()

                for channel_key, broadcaster_id in subs:
                    self._channels[channel_key] = broadcaster_id
                    if await self._subscribe_channel(session, session_id, broadcaster_id):
                        has_any_sub = True

                for channel_key in unsubs:
                    self._channels.pop(channel_key, None)

                try:
                    msg = await asyncio.wait_for(
                        ws.receive(), timeout=min(keepalive_timeout + 10, 5)
                    )
                except asyncio.TimeoutError:
                    # Check for pending operations on timeout
                    continue

                if msg.type == aiohttp.WSMsgType.TEXT:
                    import json

                    data = json.loads(msg.data)
                    msg_type = data.get("metadata", {}).get("message_type", "")

                    if msg_type == "notification":
                        self._handle_notification(data)
                    elif msg_type == "session_keepalive":
                        pass
                    elif msg_type == "session_reconnect":
                        logger.info("HypeTrainEventSub: reconnect requested")
                        return has_any_sub
                    elif msg_type == "revocation":
                        reason = (
                            data.get("payload", {}).get("subscription", {}).get("status", "unknown")
                        )
                        logger.warning(f"HypeTrainEventSub: subscription revoked: {reason}")

                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    return has_any_sub

    async def _subscribe_channel(self, session, session_id: str, broadcaster_id: str) -> bool:
        """Subscribe to all 3 hype train event types for a broadcaster.

        Returns True if at least one subscription succeeded.
        """
        event_types = [
            ("channel.hype_train.begin", "2"),
            ("channel.hype_train.progress", "2"),
            ("channel.hype_train.end", "2"),
        ]
        headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "Client-Id": self.client_id,
            "Content-Type": "application/json",
        }
        any_success = False
        for event_type, version in event_types:
            payload = {
                "type": event_type,
                "version": version,
                "condition": {"broadcaster_user_id": broadcaster_id},
                "transport": {"method": "websocket", "session_id": session_id},
            }
            try:
                async with session.post(
                    "https://api.twitch.tv/helix/eventsub/subscriptions",
                    json=payload,
                    headers=headers,
                    timeout=__import__("aiohttp").ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 202):
                        logger.debug(
                            f"HypeTrainEventSub: subscribed to {event_type} "
                            f"for broadcaster {broadcaster_id}"
                        )
                        any_success = True
                    elif resp.status == 403:
                        if not self._scope_warning_logged:
                            self._scope_warning_logged = True
                            logger.debug(
                                f"HypeTrainEventSub: 403 for {event_type} "
                                f"(not a mod of this channel)"
                            )
                    else:
                        body = await resp.text()
                        logger.warning(
                            f"HypeTrainEventSub: subscribe failed for {event_type} "
                            f"(HTTP {resp.status}): {body}"
                        )
            except Exception as e:
                logger.warning(f"HypeTrainEventSub: subscribe error for {event_type}: {e}")
        return any_success

    def _handle_notification(self, data: dict) -> None:
        """Parse an EventSub hype train notification into a HypeTrainEvent."""
        sub_type = data.get("payload", {}).get("subscription", {}).get("type", "")
        event = data.get("payload", {}).get("event", {})
        broadcaster_id = event.get("broadcaster_user_id", "")

        # Find channel_key for this broadcaster_id
        channel_key = ""
        with self._lock:
            for key, bid in self._channels.items():
                if bid == broadcaster_id:
                    channel_key = key
                    break

        if not channel_key:
            logger.debug(
                f"HypeTrainEventSub: notification for unknown broadcaster {broadcaster_id}"
            )
            return

        # Determine event type
        if sub_type == "channel.hype_train.begin":
            ht_type = "begin"
        elif sub_type == "channel.hype_train.progress":
            ht_type = "progress"
        elif sub_type == "channel.hype_train.end":
            ht_type = "end"
        else:
            return

        ht_event = HypeTrainEvent(
            type=ht_type,
            level=event.get("level", 1),
            total=event.get("total", 0),
            goal=event.get("goal", 0),
            started_at=event.get("started_at", ""),
            expires_at=event.get("expires_at", ""),
            ended_at=event.get("ended_at", ""),
        )

        logger.info(
            f"HypeTrainEventSub: {ht_type} for {channel_key} "
            f"level={ht_event.level} total={ht_event.total}/{ht_event.goal}"
        )
        self.hype_train_event.emit(channel_key, ht_event)
