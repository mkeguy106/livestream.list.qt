"""Chat manager - orchestrates connections, emote loading, and badge fetching."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap

from ..core.models import Livestream, StreamPlatform
from ..core.settings import Settings
from .connections.base import BaseChatConnection
from .emotes.cache import (
    DEFAULT_EMOTE_HEIGHT,
    EmoteCache,
    EmoteLoaderWorker,
    _extract_frames,
)
from .emotes.provider import BTTVProvider, FFZProvider, SevenTVProvider, TwitchProvider
from .models import ChatEmote, ChatMessage, ChatUser

logger = logging.getLogger(__name__)

# Fallback Twitch client ID (same as api.twitch.DEFAULT_CLIENT_ID)
_DEFAULT_TWITCH_CLIENT_ID = "gnvljs5w28wkpz60vfug0z5rp5d66h"


class ChatConnectionWorker(QThread):
    """Worker thread that runs a chat connection's async event loop."""

    def __init__(self, connection: BaseChatConnection, channel_id: str, parent=None, **kwargs):
        super().__init__(parent)
        self.connection = connection
        self.channel_id = channel_id
        self.kwargs = kwargs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._should_stop = False

    def run(self):
        """Run the connection in a new event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(
                self.connection.connect_to_channel(self.channel_id, **self.kwargs)
            )
        except Exception as e:
            if not self._should_stop:
                logger.error(f"Chat worker error: {e}")
                self.connection._emit_error(str(e))
        finally:
            self._loop.close()
            self._loop = None

    def stop(self):
        """Request the worker to stop."""
        self._should_stop = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


class EmoteFetchWorker(QThread):
    """Worker thread that fetches emote lists and badge data from providers."""

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
        parent=None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.platform = platform
        self.channel_id = channel_id
        self.providers = providers
        self.oauth_token = oauth_token
        self.client_id = client_id

    def run(self):
        """Fetch emotes and badges."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Resolve numeric Twitch user ID if needed (for 7TV/BTTV/FFZ APIs)
            resolved_id = self.channel_id
            if self.platform == "twitch":
                numeric_id = loop.run_until_complete(self._resolve_twitch_user_id())
                if numeric_id:
                    resolved_id = numeric_id

            emotes = loop.run_until_complete(self._fetch_all(resolved_id))
            self.emotes_fetched.emit(self.channel_key, emotes)

            # Fetch Twitch badges (try authenticated API first, fall back to public)
            if self.platform == "twitch":
                # Use resolved numeric ID for badge API too
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

    async def _fetch_all(self, channel_id: str) -> list[ChatEmote]:
        """Fetch global + channel emotes from all providers."""
        all_emotes: list[ChatEmote] = []

        # Fetch native platform emotes first
        if self.platform == "twitch":
            twitch_provider = TwitchProvider(
                oauth_token=self.oauth_token,
                client_id=self.client_id,
            )
            try:
                global_emotes = await twitch_provider.get_global_emotes()
                all_emotes.extend(global_emotes)
                logger.debug(f"Fetched {len(global_emotes)} global emotes from twitch")
            except Exception as e:
                logger.debug(f"Failed to fetch global emotes from twitch: {e}")

            try:
                channel_emotes = await twitch_provider.get_channel_emotes(
                    self.platform, channel_id
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
                global_emotes = await provider.get_global_emotes()
                all_emotes.extend(global_emotes)
                logger.debug(f"Fetched {len(global_emotes)} global emotes from {name}")
            except Exception as e:
                logger.debug(f"Failed to fetch global emotes from {name}: {e}")

            try:
                channel_emotes = await provider.get_channel_emotes(self.platform, channel_id)
                all_emotes.extend(channel_emotes)
                logger.debug(f"Fetched {len(channel_emotes)} channel emotes from {name}")
            except Exception as e:
                logger.debug(f"Failed to fetch channel emotes from {name}: {e}")

        return all_emotes

    async def _fetch_twitch_badges(self) -> dict[str, str]:
        """Fetch Twitch badge image URLs (global + channel).

        Tries authenticated Helix API first, falls back to public badge API.
        """
        import aiohttp

        badge_map: dict[str, str] = {}  # "name/version" -> image_url

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
                                    if set_id and vid and url:
                                        badge_map[f"{set_id}/{vid}"] = url
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
                                    if set_id and vid and url:
                                        badge_map[f"{set_id}/{vid}"] = url
            except Exception as e:
                logger.warning(f"Failed to fetch Twitch badges via Helix: {e}")

        # Fall back to public badge API if Helix didn't work
        if not badge_map:
            badge_map = await self._fetch_public_twitch_badges()

        logger.debug(f"Fetched {len(badge_map)} Twitch badge URLs")
        return badge_map

    async def _fetch_public_twitch_badges(self) -> dict[str, str]:
        """Fetch Twitch badges from the public (unauthenticated) badge API."""
        import aiohttp

        badge_map: dict[str, str] = {}
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
                                if url:
                                    badge_map[f"{set_id}/{vid}"] = url
                    else:
                        logger.warning(f"Public badge API returned {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to fetch Twitch badges from public API: {e}")

        return badge_map


class ChatManager(QObject):
    """Manages chat connections and coordinates with the UI.

    Owns chat connections, handles opening/closing chats,
    and bridges connection signals to the chat UI.
    Also manages emote/badge image loading.
    """

    # Emitted when a new chat tab should be opened
    chat_opened = Signal(str, object)  # channel_key, Livestream
    # Emitted when a chat should be closed
    chat_closed = Signal(str)  # channel_key
    # Emitted with messages for a specific channel
    messages_received = Signal(str, list)  # channel_key, list[ChatMessage]
    # Emitted with moderation events for a specific channel
    moderation_received = Signal(str, object)  # channel_key, ModerationEvent
    # Emitted when emote cache is updated (widgets should repaint)
    emote_cache_updated = Signal()
    # Emitted when auth state changes (True = authenticated)
    auth_state_changed = Signal(bool)
    # Emitted when a connection is established (channel_key)
    chat_connected = Signal(str)
    # Emitted on connection errors (channel_key, error_message)
    chat_error = Signal(str, str)

    def __init__(self, settings: Settings, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._workers: dict[str, ChatConnectionWorker] = {}
        self._connections: dict[str, BaseChatConnection] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._emote_fetch_workers: dict[str, EmoteFetchWorker] = {}

        # Emote cache shared across all widgets
        self._emote_cache = EmoteCache(parent=self)
        self._emote_cache.emote_loaded.connect(self._on_emote_loaded)

        # Emote loader for downloading images
        self._loader: EmoteLoaderWorker | None = None
        self._download_queue: list[tuple[str, str]] = []  # (cache_key, url)

        # Map of emote name -> ChatEmote for all loaded channels
        self._emote_map: dict[str, ChatEmote] = {}

        # Badge URL mapping from Twitch API: "name/version" -> image_url
        self._badge_url_map: dict[str, str] = {}

        # Track which badge URLs we've already queued for download
        self._queued_badge_urls: set[str] = set()

    @property
    def emote_cache(self) -> EmoteCache:
        """Get the shared emote cache."""
        return self._emote_cache

    @property
    def emote_map(self) -> dict[str, ChatEmote]:
        """Get the combined emote name map."""
        return self._emote_map

    def open_chat(self, livestream: Livestream) -> None:
        """Open a chat connection for a livestream."""
        channel_key = livestream.channel.unique_key
        if channel_key in self._connections:
            self.chat_opened.emit(channel_key, livestream)
            return

        self._livestreams[channel_key] = livestream
        self._start_connection(channel_key, livestream)

        # Start emote fetching for this channel
        self._fetch_emotes_for_channel(channel_key, livestream)

        self.chat_opened.emit(channel_key, livestream)

    def _start_connection(
        self,
        channel_key: str,
        livestream: Livestream,
    ) -> None:
        """Create and start a chat connection for a channel."""
        connection = self._create_connection(livestream.channel.platform)
        if not connection:
            logger.warning(f"No chat connection for {livestream.channel.platform}")
            return

        # Wire connection signals
        connection.messages_received.connect(
            lambda msgs, key=channel_key: (self._on_messages_received(key, msgs))
        )
        connection.moderation_event.connect(
            lambda evt, key=channel_key: (self.moderation_received.emit(key, evt))
        )
        connection.error.connect(lambda msg, key=channel_key: (self._on_connection_error(key, msg)))
        connection.connected.connect(lambda key=channel_key: self.chat_connected.emit(key))

        self._connections[channel_key] = connection

        # Build connection kwargs
        kwargs = self._get_connection_kwargs(livestream)

        # Start worker thread
        worker = ChatConnectionWorker(
            connection,
            livestream.channel.channel_id,
            parent=self,
            **kwargs,
        )
        self._workers[channel_key] = worker
        worker.start()

    def reconnect_twitch(self) -> None:
        """Reconnect all Twitch chat connections with the current token.

        Call this after re-login to pick up new OAuth scopes.
        """
        twitch_keys = [
            key
            for key, ls in self._livestreams.items()
            if ls.channel.platform == StreamPlatform.TWITCH
        ]
        if not twitch_keys:
            return

        logger.info(f"Reconnecting {len(twitch_keys)} Twitch chats with updated token")

        for key in twitch_keys:
            # Stop old worker/connection
            worker = self._workers.pop(key, None)
            if worker:
                worker.stop()
                worker.wait(3000)
            self._connections.pop(key, None)

            # Restart with new token
            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self.auth_state_changed.emit(bool(self.settings.twitch.access_token))

    def reconnect_kick(self) -> None:
        """Reconnect all Kick chat connections with the current token.

        Call this after login to enable sending messages.
        """
        kick_keys = [
            key
            for key, ls in self._livestreams.items()
            if ls.channel.platform == StreamPlatform.KICK
        ]
        if not kick_keys:
            # No active Kick chats, just emit auth change
            self.auth_state_changed.emit(bool(self.settings.kick.access_token))
            return

        logger.info(f"Reconnecting {len(kick_keys)} Kick chats with updated token")

        for key in kick_keys:
            worker = self._workers.pop(key, None)
            if worker:
                worker.stop()
                worker.wait(3000)
            self._connections.pop(key, None)

            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self.auth_state_changed.emit(bool(self.settings.kick.access_token))

    def reconnect_youtube(self) -> None:
        """Reconnect all YouTube chat connections with current cookies.

        Call this after saving cookies to enable sending messages.
        """
        youtube_keys = [
            key
            for key, ls in self._livestreams.items()
            if ls.channel.platform == StreamPlatform.YOUTUBE
        ]
        if not youtube_keys:
            # No active YouTube chats, just emit auth change
            self.auth_state_changed.emit(bool(self.settings.youtube.cookies))
            return

        logger.debug(f"Reconnecting {len(youtube_keys)} YouTube chats with updated cookies")

        for key in youtube_keys:
            worker = self._workers.pop(key, None)
            if worker:
                worker.stop()
                worker.wait(3000)
            self._connections.pop(key, None)

            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self.auth_state_changed.emit(bool(self.settings.youtube.cookies))

    def close_chat(self, channel_key: str) -> None:
        """Close a chat connection."""
        worker = self._workers.pop(channel_key, None)
        if worker:
            worker.stop()
            worker.wait(3000)

        self._connections.pop(channel_key, None)
        self._livestreams.pop(channel_key, None)

        # Clean up emote fetch worker
        fetch_worker = self._emote_fetch_workers.pop(channel_key, None)
        if fetch_worker and fetch_worker.isRunning():
            fetch_worker.wait(2000)

        self.chat_closed.emit(channel_key)

    def send_message(self, channel_key: str, text: str) -> None:
        """Send a message to a channel's chat."""
        connection = self._connections.get(channel_key)
        if not connection or not connection.is_connected:
            logger.warning(f"Cannot send message: not connected to {channel_key}")
            return

        worker = self._workers.get(channel_key)
        if worker and worker._loop:
            asyncio.run_coroutine_threadsafe(connection.send_message(text), worker._loop)

            # Local echo: Twitch doesn't echo your own messages back.
            # Kick DOES echo via websocket, so skip local echo for Kick.
            livestream = self._livestreams.get(channel_key)
            if livestream and livestream.channel.platform != StreamPlatform.KICK:
                # Use display_name (proper case) if available, fall back to nick
                display_name = getattr(connection, "_display_name", "") or getattr(
                    connection, "_nick", "You"
                )
                nick = getattr(connection, "_nick", "You")
                local_msg = ChatMessage(
                    id=str(uuid.uuid4()),
                    user=ChatUser(
                        id="self",
                        name=nick,
                        display_name=display_name,
                        platform=livestream.channel.platform,
                        color=None,
                        badges=[],
                    ),
                    text=text,
                    timestamp=datetime.now(timezone.utc),
                    platform=livestream.channel.platform,
                )
                # Route through _on_messages_received for emote matching
                self._on_messages_received(channel_key, [local_msg])

    def disconnect_all(self) -> None:
        """Disconnect all active chat connections."""
        for key in list(self._workers.keys()):
            self.close_chat(key)
        self._stop_loader()

    def is_connected(self, channel_key: str) -> bool:
        """Check if a channel's chat is connected."""
        conn = self._connections.get(channel_key)
        return conn.is_connected if conn else False

    def _on_connection_error(self, channel_key: str, message: str) -> None:
        """Handle connection error - log and forward to UI."""
        logger.error(f"Chat error for {channel_key}: {message}")
        self.chat_error.emit(channel_key, message)

    def _on_messages_received(self, channel_key: str, messages: list) -> None:
        """Handle incoming messages - queue badge/emote downloads, then forward."""
        for msg in messages:
            if not isinstance(msg, ChatMessage):
                continue
            # Queue badge image downloads
            for badge in msg.user.badges:
                badge_key = f"badge:{badge.id}"
                if self._emote_cache.has(badge_key) or badge_key in self._queued_badge_urls:
                    # Ensure loaded into memory if only on disk
                    if badge_key not in self._emote_cache.pixmap_dict:
                        self._emote_cache.get(badge_key)
                    continue
                # Mark as seen (will be re-queued when badge map arrives)
                self._queued_badge_urls.add(badge_key)
                # Use URL from badge API map, fall back to badge.image_url if set
                badge_url = self._badge_url_map.get(badge.id) or badge.image_url
                if badge_url:
                    self._download_queue.append((badge_key, badge_url))

            # Match third-party emotes (7TV, BTTV, FFZ) in message text
            if self._emote_map:
                self._match_third_party_emotes(msg)

            # Queue emote image downloads (native + third-party)
            for start, end, emote in msg.emote_positions:
                emote_key = f"emote:{emote.provider}:{emote.id}"
                if self._emote_cache.is_pending(emote_key):
                    continue
                if not self._emote_cache.has(emote_key):
                    url = emote.url_template.replace("{size}", "2.0")
                    self._emote_cache.mark_pending(emote_key)
                    self._download_queue.append((emote_key, url))
                elif not self._emote_cache.has_animation_data(emote_key):
                    # Legacy PNG-only cache - re-download for animation detection
                    url = emote.url_template.replace("{size}", "2.0")
                    self._emote_cache.mark_pending(emote_key)
                    self._download_queue.append((emote_key, url))
                elif emote_key not in self._emote_cache.pixmap_dict:
                    # Cached on disk but not in memory - load it
                    self._emote_cache.get(emote_key)

        # Detect @mentions of our username
        our_nick = self._get_our_nick(channel_key)
        if our_nick:
            mention_pattern = f"@{our_nick}".lower()
            for msg in messages:
                if isinstance(msg, ChatMessage) and mention_pattern in msg.text.lower():
                    msg.is_mention = True

        # Start downloading if we have items queued
        if self._download_queue:
            self._start_downloads()

        self.messages_received.emit(channel_key, messages)

    def _get_our_nick(self, channel_key: str) -> str | None:
        """Get the authenticated user's display name for the given channel's connection."""
        connection = self._connections.get(channel_key)
        if connection:
            nick = getattr(connection, "_nick", None)
            if nick and not nick.startswith("justinfan"):
                return nick
        return None

    def _match_third_party_emotes(self, msg: ChatMessage) -> None:
        """Scan message text for third-party emote names and add to emote_positions."""
        text = msg.text
        if not text:
            return

        # Build set of character positions already claimed by native emotes
        claimed: set[int] = set()
        for start, end, _ in msg.emote_positions:
            claimed.update(range(start, end))

        # Split text into words and match against emote map
        new_positions: list[tuple[int, int, ChatEmote]] = []
        i = 0
        while i < len(text):
            # Skip whitespace
            if text[i] == " ":
                i += 1
                continue

            # Find word boundary
            j = i
            while j < len(text) and text[j] != " ":
                j += 1

            # Check if this word position is already claimed
            if i not in claimed:
                word = text[i:j]
                emote = self._emote_map.get(word)
                if emote:
                    new_positions.append((i, j, emote))

            i = j

        if new_positions:
            # Merge with existing positions and sort by start
            msg.emote_positions = sorted(msg.emote_positions + new_positions, key=lambda x: x[0])
            logger.debug(f"Matched {len(new_positions)} 3rd-party emotes in message")

    def _fetch_emotes_for_channel(self, channel_key: str, livestream: Livestream) -> None:
        """Kick off async emote/badge fetching for a channel."""
        providers = self.settings.chat.builtin.emote_providers
        platform_name = livestream.channel.platform.value.lower()
        channel_id = livestream.channel.channel_id

        worker = EmoteFetchWorker(
            channel_key=channel_key,
            platform=platform_name,
            channel_id=channel_id,
            providers=providers if self.settings.chat.builtin.show_emotes else [],
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            parent=self,
        )
        worker.emotes_fetched.connect(self._on_emotes_fetched)
        worker.badges_fetched.connect(self._on_badges_fetched)
        self._emote_fetch_workers[channel_key] = worker
        worker.start()

    def _on_emotes_fetched(self, channel_key: str, emotes: list) -> None:
        """Handle fetched emote list - add to map and queue image downloads."""
        for emote in emotes:
            if not isinstance(emote, ChatEmote):
                continue
            self._emote_map[emote.name] = emote

            # Queue image download (or load from disk cache)
            emote_key = f"emote:{emote.provider}:{emote.id}"
            if self._emote_cache.is_pending(emote_key):
                continue
            if not self._emote_cache.has(emote_key):
                # Not cached at all - download
                self._emote_cache.mark_pending(emote_key)
                self._download_queue.append((emote_key, emote.url_template))
            elif not self._emote_cache.has_animation_data(emote_key):
                # Legacy PNG cache only - re-download for animation detection
                self._emote_cache.mark_pending(emote_key)
                self._download_queue.append((emote_key, emote.url_template))
            elif emote_key not in self._emote_cache.pixmap_dict:
                # Has raw/animation data on disk but not in memory - load it
                self._emote_cache.get(emote_key)

        # Debug: log short emote names and emotes containing "om"
        short_emotes = [e.name for e in emotes if len(e.name) <= 4]
        om_emotes = [e.name for e in emotes if "om" in e.name.lower()]
        logger.info(
            f"Loaded {len(emotes)} emotes for {channel_key}, "
            f"total emote map: {len(self._emote_map)}"
        )
        if short_emotes:
            logger.info(f"Short emotes (<=4 chars): {short_emotes}")
        if om_emotes:
            logger.debug(f"Emotes containing 'om': {om_emotes[:20]}")

        # Notify widgets immediately so autocomplete has the emote map
        self.emote_cache_updated.emit()

        # Start downloading
        if self._download_queue:
            self._start_downloads()

    def _on_badges_fetched(self, channel_key: str, badge_map: dict) -> None:
        """Handle fetched badge URL data from Twitch API."""
        self._badge_url_map.update(badge_map)
        logger.info(f"Badge URL map updated: {len(self._badge_url_map)} entries")

        # Re-queue badges that were attempted before the map was ready
        requeue_count = 0
        for badge_key in list(self._queued_badge_urls):
            if self._emote_cache.has(badge_key):
                # Ensure loaded into memory if only on disk
                if badge_key not in self._emote_cache.pixmap_dict:
                    self._emote_cache.get(badge_key)
                continue
            # Extract badge_id from "badge:name/version"
            badge_id = badge_key.removeprefix("badge:")
            url = badge_map.get(badge_id)
            if url:
                self._download_queue.append((badge_key, url))
                requeue_count += 1

        if requeue_count:
            logger.debug(f"Re-queued {requeue_count} badges with correct URLs")
            self._start_downloads()

    def _start_downloads(self) -> None:
        """Start or continue the emote loader worker."""
        if self._loader and self._loader.isRunning():
            # Worker is running, it will pick up items when it checks queue
            for key, url in self._download_queue:
                self._loader.enqueue(key, url)
            self._download_queue.clear()
            return

        # Create new loader
        self._loader = EmoteLoaderWorker(parent=self)
        self._loader.emote_ready.connect(self._on_emote_downloaded)
        self._loader.animated_emote_ready.connect(self._on_animated_emote_downloaded)
        self._loader.finished.connect(self._on_loader_finished)

        for key, url in self._download_queue:
            self._loader.enqueue(key, url)
        self._download_queue.clear()

        self._loader.start()

    def _on_emote_downloaded(self, key: str, pixmap: object, raw_data: bytes = b"") -> None:
        """Handle a downloaded emote/badge image."""
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._emote_cache.put(key, pixmap)
            # Save raw bytes for emotes so has_animation_data() returns True,
            # preventing re-downloads on future launches.
            if raw_data and key.startswith("emote:"):
                self._emote_cache._put_disk_raw(key, raw_data)

    def _on_animated_emote_downloaded(self, key: str, raw_data: bytes) -> None:
        """Handle a downloaded animated emote - extract frames on GUI thread."""
        result = _extract_frames(raw_data)
        if result:
            frames, delays = result
            self._emote_cache.put_animated(key, frames, delays)
            self._emote_cache._put_disk_raw(key, raw_data)
        else:
            # Detection said animated but extraction failed - treat as static
            pixmap = QPixmap()
            if pixmap.loadFromData(raw_data):
                if pixmap.height() > 0 and pixmap.height() != DEFAULT_EMOTE_HEIGHT:
                    pixmap = pixmap.scaledToHeight(
                        DEFAULT_EMOTE_HEIGHT,
                        mode=Qt.TransformationMode.SmoothTransformation,
                    )
                self._emote_cache.put(key, pixmap)
                self._emote_cache._put_disk_raw(key, raw_data)

    def _on_emote_loaded(self, key: str) -> None:
        """Handle emote cache updated signal - trigger repaint."""
        self.emote_cache_updated.emit()

    def _on_loader_finished(self) -> None:
        """Handle loader worker finishing."""
        # If more items were queued while running, start again
        if self._download_queue:
            self._start_downloads()

    def _stop_loader(self) -> None:
        """Stop the emote loader worker."""
        if self._loader:
            self._loader.stop()
            self._loader.wait(3000)
            self._loader = None

    def _create_connection(self, platform: StreamPlatform) -> BaseChatConnection | None:
        """Create a chat connection for the given platform."""
        if platform == StreamPlatform.TWITCH:
            from .connections.twitch import TwitchChatConnection

            return TwitchChatConnection(
                oauth_token=self.settings.twitch.access_token,
                parent=self,
            )
        elif platform == StreamPlatform.KICK:
            from .connections.kick import KickChatConnection

            return KickChatConnection(
                kick_settings=self.settings.kick,
                parent=self,
            )
        elif platform == StreamPlatform.YOUTUBE:
            from .connections.youtube import YouTubeChatConnection

            return YouTubeChatConnection(
                youtube_settings=self.settings.youtube,
                parent=self,
            )
        return None

    def _get_connection_kwargs(self, livestream: Livestream) -> dict:
        """Get platform-specific connection kwargs."""
        kwargs: dict = {}
        if livestream.channel.platform == StreamPlatform.KICK:
            kwargs["chatroom_id"] = getattr(livestream, "chatroom_id", None)
        elif livestream.channel.platform == StreamPlatform.YOUTUBE:
            kwargs["video_id"] = livestream.video_id
        return kwargs
