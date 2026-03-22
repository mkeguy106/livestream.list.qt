"""Chat manager - orchestrates connections, emote loading, and badge fetching."""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.models import Livestream, StreamPlatform
from ..core.settings import Settings
from .chat_log_store import ChatLogWriter
from .connections.base import BaseChatConnection
from .emotes.cache import DOWNLOAD_PRIORITY_HIGH, EmoteCache
from .emotes.image import GifTimer, ImageExpirationPool, ImageSet, ImageSpec
from .emotes.matcher import find_third_party_emotes
from .models import ChatBadge, ChatEmote, ChatMessage, ChatUser
from .workers import (
    AsyncTaskWorker,
    ChatConnectionWorker,
    EmoteFetchWorker,
    HypeTrainEventSubWorker,
    WhisperEventSubWorker,
    _fetch_global_emotes,
    _fetch_user_emotes,
)

logger = logging.getLogger(__name__)

# Fallback Twitch client ID (same as api.twitch.DEFAULT_CLIENT_ID)
_DEFAULT_TWITCH_CLIENT_ID = "gnvljs5w28wkpz60vfug0z5rp5d66h"
GLOBAL_EMOTE_TTL = 24 * 60 * 60  # 24 hours
USER_EMOTE_TTL = 30 * 60  # 30 minutes
CHANNEL_EMOTE_TTL = 6 * 60 * 60  # 6 hours
MAX_RECENT_CHANNELS = 30
MESSAGE_FLUSH_INTERVAL_MS = 50
MAX_MESSAGES_PER_FLUSH = 200
MAX_PENDING_MESSAGES = 5000


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
    # Emitted when emote map is updated (channel_key or "" for all)
    emote_map_updated = Signal(str)
    # Emitted when auth state changes (True = authenticated)
    auth_state_changed = Signal(bool)
    # Emitted when a connection is established (channel_key)
    chat_connected = Signal(str)
    # Emitted on connection errors (channel_key, error_message)
    chat_error = Signal(str, str)
    # Emitted when channel socials are fetched (channel_key, {platform: url})
    socials_fetched = Signal(str, dict)
    # Emitted when sub anniversary info is fetched (channel_key, sub_info dict)
    sub_anniversary_fetched = Signal(str, dict)
    # Emitted when a whisper/DM is received (platform_str, ChatMessage)
    whisper_received = Signal(str, object)
    # Emitted when room state changes (channel_key, ChatRoomState)
    room_state_changed = Signal(str, object)
    # Emitted when a connection is lost (channel_key)
    chat_disconnected = Signal(str)
    # Emitted when reconnecting with delay (channel_key, delay_seconds)
    chat_reconnecting = Signal(str, float)
    # Emitted when reconnection attempts are exhausted (channel_key)
    chat_reconnect_failed = Signal(str)
    # Emitted on hype train events (channel_key, HypeTrainEvent)
    hype_train_event = Signal(str, object)
    # Emitted on raid events (channel_key, ChatMessage)
    raid_received = Signal(str, object)
    # Emitted on @mention of our user (channel_key, ChatMessage)
    mention_received = Signal(str, object)
    # Emitted when badge URL map is ready for a channel (widgets should re-resolve)
    badge_map_ready = Signal(str)  # channel_key
    # Emitted when settings were modified and need saving
    settings_changed = Signal()

    def __init__(self, settings: Settings, monitor=None, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._monitor = monitor
        self._workers: dict[str, ChatConnectionWorker] = {}
        self._connections: dict[str, BaseChatConnection] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._emote_fetch_workers: dict[str, EmoteFetchWorker] = {}
        self._socials_fetch_workers: dict[str, AsyncTaskWorker] = {}
        self._sub_anniversary_workers: dict[str, AsyncTaskWorker] = {}
        self._global_emote_worker: AsyncTaskWorker | None = None
        self._user_emote_worker: AsyncTaskWorker | None = None

        # Emote cache shared across all widgets
        self._emote_cache = EmoteCache(parent=self)
        self._emote_cache.emote_loaded.connect(self._on_emote_loaded)
        self._emote_cache.set_disk_limit_mb(self.settings.emote_cache_mb)
        logger.info(f"Emote disk cache limit: {self.settings.emote_cache_mb}MB")
        self._gif_timer = GifTimer(parent=self)
        self._image_expiration_pool = ImageExpirationPool(self._emote_cache, parent=self)

        # Global emotes (shared across channels)
        self._global_common_emotes: dict[str, ChatEmote] = {}
        self._global_twitch_emotes: dict[str, ChatEmote] = {}
        # User emotes (Twitch subscriber/follower emotes)
        self._user_emotes: dict[str, ChatEmote] = {}
        # Channel-specific emotes: channel_key -> {name: emote}
        self._channel_emote_maps: dict[str, dict[str, ChatEmote]] = {}
        # Resolved per-channel emote maps (global + user + channel)
        self._resolved_emote_maps: dict[str, dict[str, ChatEmote]] = {}
        self._global_emotes_fetched_at: float = 0.0
        self._user_emotes_fetched_at: float = 0.0
        self._channel_emotes_fetched_at: dict[str, float] = {}

        # Badge data from Twitch API, per-channel: channel_key -> {badge_id -> (url, title)}
        # Channel-specific because subscriber/bits badges differ per channel
        self._badge_url_map: dict[str, dict[str, tuple[str, str]]] = {}
        self._badge_image_sets: dict[str, dict[str, ImageSet]] = {}
        self._badges_fetched_at: dict[str, float] = {}

        # Track which badge URLs we've already queued for download, per-channel
        self._queued_badge_urls: dict[str, set[str]] = {}

        # Debounce emote cache updates to reduce UI churn
        self._emote_update_pending = False
        self._emote_update_timer = QTimer(self)
        self._emote_update_timer.setSingleShot(True)
        self._emote_update_timer.timeout.connect(self._emit_emote_cache_update)

        self._last_emote_providers = tuple(self.settings.chat.builtin.emote_providers)

        # YouTube web embed channels (no native connection needed)
        self._web_chat_keys: set[str] = set()

        # Message batching to throttle UI updates
        self._pending_messages: dict[str, list[ChatMessage]] = {}
        self._message_flush_timer = QTimer(self)
        self._message_flush_timer.setInterval(MESSAGE_FLUSH_INTERVAL_MS)
        self._message_flush_timer.timeout.connect(self._flush_pending_messages)

        # Whisper EventSub worker (receives Twitch whispers via EventSub WebSocket)
        self._whisper_worker: WhisperEventSubWorker | None = None

        # Hype Train EventSub worker
        self._hype_train_worker: HypeTrainEventSubWorker | None = None
        # channel_key -> broadcaster numeric ID (from ROOMSTATE room-id)
        self._twitch_broadcaster_ids: dict[str, str] = {}

        # Chat log writer
        self._chat_log_writer = ChatLogWriter(settings.chat.logging)
        self._chat_log_flush_timer = QTimer(self)
        self._chat_log_flush_timer.setInterval(5000)
        self._chat_log_flush_timer.timeout.connect(self._flush_chat_logs)
        if settings.chat.logging.enabled:
            self._chat_log_flush_timer.start()
        # Periodic disk limit enforcement
        self._chat_log_enforce_timer = QTimer(self)
        self._chat_log_enforce_timer.setInterval(60000)
        self._chat_log_enforce_timer.timeout.connect(self._enforce_chat_log_limits)
        if settings.chat.logging.enabled:
            self._chat_log_enforce_timer.start()

        # Kick off global/user emote fetch in background
        self._ensure_global_emotes()
        self._ensure_user_emotes()
        self._ensure_whisper_listener()

        # Listen for streams coming online to trigger immediate reconnect
        if self._monitor:
            self._monitor.on_stream_online(self._on_stream_came_online)

    def _on_stream_came_online(self, livestream: Livestream) -> None:
        """Handle a stream coming online — trigger immediate reconnect if we have a worker."""
        channel_key = livestream.channel.unique_key
        worker = self._workers.get(channel_key)
        if not worker or not worker.isRunning():
            return

        # For YouTube, update the video_id in case it changed
        if livestream.channel.platform == StreamPlatform.YOUTUBE:
            new_kwargs = self._get_connection_kwargs(livestream)
            worker.update_kwargs(**new_kwargs)

        logger.info(f"Stream came online for {channel_key}, triggering immediate reconnect")
        worker.connection._reset_backoff()
        worker.request_immediate_reconnect()

    @property
    def emote_cache(self) -> EmoteCache:
        """Get the shared emote cache."""
        return self._emote_cache

    @property
    def gif_timer(self) -> GifTimer:
        """Get the shared GIF animation timer."""
        return self._gif_timer

    def get_metrics(self) -> dict[str, int]:
        """Return lightweight performance metrics for the UI."""
        pending_messages = sum(len(v) for v in self._pending_messages.values())
        return {
            "emote_mem": len(self._emote_cache.pixmap_dict),
            "emote_animated": len(self._emote_cache.animated_dict),
            "emote_pending": self._emote_cache.pending_count(),
            "disk_bytes": self._emote_cache.get_disk_usage_bytes(),
            "disk_limit_mb": int(self.settings.emote_cache_mb),
            "downloads_queued": self._emote_cache.downloads_queued(),
            "downloads_inflight": self._emote_cache.downloads_inflight(),
            "message_queue": pending_messages,
        }

    def set_emote_cache_limit(self, mb: int) -> None:
        """Update emote disk cache limit (MB)."""
        self._emote_cache.set_disk_limit_mb(mb)

    @property
    def emote_map(self) -> dict[str, ChatEmote]:
        """Get the global emote map (third-party globals)."""
        return self._global_common_emotes

    def get_emote_map(self, channel_key: str) -> dict[str, ChatEmote]:
        """Get the combined emote map for a specific channel."""
        if channel_key in self._resolved_emote_maps:
            return self._resolved_emote_maps[channel_key]
        return self._rebuild_emote_map(channel_key)

    def get_channel_emote_names(self, channel_key: str) -> set[str]:
        """Get names of channel-specific emotes (not global)."""
        return set(self._channel_emote_maps.get(channel_key, {}).keys())

    def get_user_emote_names(self) -> set[str]:
        """Get names of emotes the authenticated user can use (subscriptions etc)."""
        return set(self._user_emotes.keys())

    def open_chat(self, livestream: Livestream) -> None:
        """Open a chat connection for a livestream."""
        channel_key = livestream.channel.unique_key
        if channel_key in self._connections or channel_key in self._web_chat_keys:
            self.chat_opened.emit(channel_key, livestream)
            return

        self._livestreams[channel_key] = livestream
        self._record_recent_channel(channel_key)

        # YouTube and Chaturbate use embedded web views — no native connection
        if livestream.channel.platform in (
            StreamPlatform.YOUTUBE,
            StreamPlatform.CHATURBATE,
        ):
            self._web_chat_keys.add(channel_key)
            self._fetch_socials_for_channel(channel_key, livestream)
            self.chat_opened.emit(channel_key, livestream)
            return

        self._start_connection(channel_key, livestream)

        # Start emote fetching for this channel
        self._fetch_emotes_for_channel(channel_key, livestream)

        # Start socials fetching for all platforms
        self._fetch_socials_for_channel(channel_key, livestream)

        # Check for Twitch sub anniversary
        self._fetch_sub_anniversary(channel_key, livestream)

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
            lambda msgs, key=channel_key: self._on_messages_received(key, msgs)
        )
        connection.moderation_event.connect(
            lambda evt, key=channel_key: self.moderation_received.emit(key, evt)
        )
        connection.error.connect(lambda msg, key=channel_key: self._on_connection_error(key, msg))
        connection.connected.connect(lambda key=channel_key: self._on_chat_connected(key))
        connection.disconnected.connect(lambda key=channel_key: self.chat_disconnected.emit(key))
        connection.room_state_changed.connect(
            lambda state, key=channel_key: self.room_state_changed.emit(key, state)
        )
        # Track broadcaster ID for Twitch hype train subscriptions
        if livestream.channel.platform == StreamPlatform.TWITCH:
            connection.broadcaster_id_resolved.connect(
                lambda bid, key=channel_key: self._on_broadcaster_id_resolved(key, bid)
            )

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
        worker.reconnecting.connect(
            lambda delay, key=channel_key: self.chat_reconnecting.emit(key, delay)
        )
        worker.reconnect_failed.connect(
            lambda key=channel_key: self.chat_reconnect_failed.emit(key)
        )
        self._workers[channel_key] = worker
        worker.start()

    def _reconnect_platform(
        self,
        platform: StreamPlatform,
        has_auth: bool,
        *,
        post_reconnect: Callable[[], None] | None = None,
        emit_on_empty: bool = True,
    ) -> None:
        """Reconnect all chat connections for a platform."""
        keys = [key for key, ls in self._livestreams.items() if ls.channel.platform == platform]
        if not keys:
            if emit_on_empty:
                self.auth_state_changed.emit(has_auth)
            return

        logger.info(f"Reconnecting {len(keys)} {platform.value} chats")

        for key in keys:
            worker = self._workers.pop(key, None)
            if worker:
                worker.stop()
                worker.wait(500)
            self._disconnect_connection_signals(key)
            self._start_connection(key, self._livestreams[key])

        if post_reconnect:
            post_reconnect()
        self.auth_state_changed.emit(has_auth)

    def reconnect_twitch(self) -> None:
        """Reconnect all Twitch chat connections with the current token."""

        def _post() -> None:
            self._ensure_user_emotes(force=True)
            self._stop_whisper_listener()
            self._ensure_whisper_listener()

        self._reconnect_platform(
            StreamPlatform.TWITCH,
            bool(self.settings.twitch.access_token),
            post_reconnect=_post,
            emit_on_empty=False,
        )

    def reconnect_kick(self) -> None:
        """Reconnect all Kick chat connections with the current token."""
        self._reconnect_platform(
            StreamPlatform.KICK,
            bool(self.settings.kick.access_token),
        )

    def reconnect_youtube(self) -> None:
        """Notify auth state for YouTube (web embed handles its own connection)."""
        self.auth_state_changed.emit(bool(self.settings.youtube.cookies))

    def reconnect_chaturbate(self) -> None:
        """Notify auth state for Chaturbate (web embed handles its own connection)."""
        self.auth_state_changed.emit(bool(self.settings.chaturbate.login_name))

    def close_chat(self, channel_key: str) -> None:
        """Close a chat connection."""
        # YouTube web embed channels have no native connection
        if channel_key in self._web_chat_keys:
            self._web_chat_keys.discard(channel_key)
            self._livestreams.pop(channel_key, None)
            self.chat_closed.emit(channel_key)
            return

        worker = self._workers.pop(channel_key, None)
        if worker:
            worker.stop()
            worker.wait(500)

        # Disconnect signals to break reference cycles before removing connection
        self._disconnect_connection_signals(channel_key)

        self._livestreams.pop(channel_key, None)

        # Unsubscribe from hype train events
        self._twitch_broadcaster_ids.pop(channel_key, None)
        if self._hype_train_worker:
            self._hype_train_worker.unsubscribe_channel(channel_key)

        # Clean up emote fetch worker and clear emote cache timestamps
        # so reopening the chat fetches fresh emotes
        fetch_worker = self._emote_fetch_workers.pop(channel_key, None)
        if fetch_worker and fetch_worker.isRunning():
            fetch_worker.wait(500)
        self._channel_emotes_fetched_at.pop(channel_key, None)
        self._channel_emote_maps.pop(channel_key, None)
        self._resolved_emote_maps.pop(channel_key, None)

        self.chat_closed.emit(channel_key)

    def on_refresh_complete(self, livestreams: list[Livestream] | None = None) -> None:
        """Handle refresh complete to keep emotes fresh."""
        # Keep global/user emotes fresh without manual refresh.
        self._ensure_global_emotes()
        self._ensure_user_emotes()

    def _record_recent_channel(self, channel_key: str) -> None:
        """Record a channel in the recent chat list."""
        recents = list(self.settings.chat.recent_channels or [])
        if channel_key in recents:
            recents.remove(channel_key)
        recents.insert(0, channel_key)
        recents = recents[:MAX_RECENT_CHANNELS]
        self.settings.chat.recent_channels = recents
        self.settings.save()

    def send_message(
        self,
        channel_key: str,
        text: str,
        reply_to_msg_id: str = "",
        reply_parent_display_name: str = "",
        reply_parent_text: str = "",
    ) -> None:
        """Send a message to a channel's chat."""
        connection = self._connections.get(channel_key)
        if not connection or not connection.is_connected:
            logger.warning(f"Cannot send message: not connected to {channel_key}")
            return

        worker = self._workers.get(channel_key)
        if not worker or not worker._loop:
            logger.warning(
                f"Cannot send message: no worker/loop for {channel_key} "
                f"(worker={worker is not None}, loop={getattr(worker, '_loop', None) is not None})"
            )
            return

        future = asyncio.run_coroutine_threadsafe(
            connection.send_message(text, reply_to_msg_id), worker._loop
        )

        def _on_send_done(f: "asyncio.Future[bool]") -> None:
            try:
                f.result()
            except Exception as e:
                logger.error(f"YouTube send_message future error: {e}", exc_info=True)

        future.add_done_callback(_on_send_done)

        # Local echo: Twitch IRC doesn't echo your own messages back.
        # Kick echoes via Pusher websocket, YouTube echoes via pytchat poll.
        # Only add local echo for Twitch to avoid duplicates.
        livestream = self._livestreams.get(channel_key)
        if livestream and livestream.channel.platform not in (
            StreamPlatform.KICK,
            StreamPlatform.YOUTUBE,
        ):
            # Use display_name (proper case) if available, fall back to nick
            display_name = getattr(connection, "_display_name", "") or getattr(
                connection, "_nick", "You"
            )
            nick = getattr(connection, "_nick", "You")
            user_badges = getattr(connection, "_user_badges", [])
            user_color = getattr(connection, "_user_color", None)
            local_msg = ChatMessage(
                id=str(uuid.uuid4()),
                user=ChatUser(
                    id="self",
                    name=nick,
                    display_name=display_name,
                    platform=livestream.channel.platform,
                    color=user_color,
                    badges=list(user_badges),
                ),
                text=text,
                timestamp=datetime.now(timezone.utc),
                platform=livestream.channel.platform,
                reply_parent_display_name=reply_parent_display_name,
                reply_parent_text=reply_parent_text,
            )
            # Route through _on_messages_received for emote matching
            self._on_messages_received(channel_key, [local_msg])

    def _on_twitch_login_detected(self, login_name: str) -> None:
        """Store the Twitch login name when detected from token validation."""
        if login_name and login_name != self.settings.twitch.login_name:
            self.settings.twitch.login_name = login_name
            logger.info(f"Twitch login name stored: {login_name}")

    def _on_eventsub_whisper(self, message: ChatMessage) -> None:
        """Handle incoming whisper from EventSub worker — forward to UI."""
        logger.info(
            f"Whisper received from {message.user.display_name} "
            f"(id={message.user.id}): {message.text[:50]}"
        )
        # Persist to local storage
        from .whisper_store import save_whisper

        save_whisper(message.user.display_name, message)
        # Find any active Twitch channel_key for emote/badge processing
        twitch_key = ""
        for key, ls in self._livestreams.items():
            if ls.channel.platform == StreamPlatform.TWITCH:
                twitch_key = key
                break
        if twitch_key:
            self._process_messages(twitch_key, [message])
        self.whisper_received.emit("twitch", message)

    def send_whisper(self, to_user_id: str, to_display_name: str, text: str) -> None:
        """Send a Twitch whisper.

        Uses an active Twitch connection if available, otherwise sends
        directly via a background thread using the stored token.
        """
        logger.info(f"send_whisper: to={to_display_name} (id={to_user_id}), text={text[:50]!r}")

        # Try to use an active Twitch connection (has event loop already)
        connection = None
        worker = None
        for key, conn in self._connections.items():
            livestream = self._livestreams.get(key)
            if livestream and livestream.channel.platform == StreamPlatform.TWITCH:
                connection = conn
                worker = self._workers.get(key)
                break

        if connection and worker and worker._loop:
            asyncio.run_coroutine_threadsafe(
                connection.send_whisper(to_user_id, text), worker._loop
            )
        elif self.settings.twitch.access_token:
            # No active chat connection — send directly via background thread
            self._send_whisper_standalone(to_user_id, text)
        else:
            logger.warning("Cannot send whisper: not logged in to Twitch")
            return

        # Local echo for sent whisper
        display_name = self.settings.twitch.login_name or "You"
        if connection:
            display_name = getattr(connection, "_display_name", "") or getattr(
                connection, "_nick", display_name
            )
        local_msg = ChatMessage(
            id=str(uuid.uuid4()),
            user=ChatUser(
                id="self",
                name=display_name.lower(),
                display_name=display_name,
                platform=StreamPlatform.TWITCH,
            ),
            text=text,
            timestamp=datetime.now(timezone.utc),
            platform=StreamPlatform.TWITCH,
            is_whisper=True,
            whisper_target=to_display_name,
        )
        # Persist to local storage
        from .whisper_store import save_whisper

        save_whisper(to_display_name, local_msg)
        self.whisper_received.emit("twitch", local_msg)

    def _send_whisper_standalone(self, to_user_id: str, text: str) -> None:
        """Send a whisper via Helix API in a background thread (no active connection needed)."""
        token = self.settings.twitch.access_token
        client_id = self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID

        async def send():
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError("Token validation failed")
                    data = await resp.json()
                    from_user_id = data.get("user_id", "")

                if from_user_id == to_user_id:
                    raise RuntimeError("Cannot whisper yourself")

                logger.info(f"Standalone whisper: from={from_user_id} to={to_user_id}")
                async with session.post(
                    "https://api.twitch.tv/helix/whispers",
                    params={
                        "from_user_id": from_user_id,
                        "to_user_id": to_user_id,
                    },
                    json={"message": text},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Client-Id": client_id,
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 204:
                        body = await resp.text()
                        raise RuntimeError(f"Whisper failed (HTTP {resp.status}): {body}")

        worker = AsyncTaskWorker(send, parent=self)
        worker.error_occurred.connect(lambda msg: logger.error(f"Standalone whisper error: {msg}"))
        worker.start()

    def _ensure_whisper_listener(self) -> None:
        """Start the EventSub whisper listener if Twitch auth is available."""
        if not self.settings.twitch.access_token:
            return
        if self._whisper_worker and self._whisper_worker.isRunning():
            return
        client_id = self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID
        self._whisper_worker = WhisperEventSubWorker(
            oauth_token=self.settings.twitch.access_token,
            client_id=client_id,
            parent=self,
        )
        self._whisper_worker.whisper_received.connect(self._on_eventsub_whisper)
        self._whisper_worker.authenticated_as.connect(self._on_twitch_login_detected)
        self._whisper_worker.start()
        logger.info("Started WhisperEventSub listener")

    def _stop_whisper_listener(self) -> None:
        """Stop the EventSub whisper listener."""
        if self._whisper_worker:
            self._whisper_worker.stop()
            self._whisper_worker.wait(2000)
            self._whisper_worker = None

    def _on_broadcaster_id_resolved(self, channel_key: str, broadcaster_id: str) -> None:
        """Handle broadcaster ID resolved from ROOMSTATE room-id tag."""
        if channel_key in self._twitch_broadcaster_ids:
            return  # Already known
        self._twitch_broadcaster_ids[channel_key] = broadcaster_id
        logger.debug(f"Broadcaster ID for {channel_key}: {broadcaster_id}")
        self._ensure_hype_train_listener()
        if self._hype_train_worker:
            self._hype_train_worker.subscribe_channel(channel_key, broadcaster_id)

    def _ensure_hype_train_listener(self) -> None:
        """Start the hype train EventSub listener if Twitch auth is available."""
        if not self.settings.twitch.access_token:
            return
        if self._hype_train_worker and self._hype_train_worker.isRunning():
            return
        client_id = self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID
        self._hype_train_worker = HypeTrainEventSubWorker(
            oauth_token=self.settings.twitch.access_token,
            client_id=client_id,
            parent=self,
        )
        self._hype_train_worker.hype_train_event.connect(
            lambda key, evt: self.hype_train_event.emit(key, evt)
        )
        self._hype_train_worker.start()
        logger.info("Started HypeTrainEventSub listener")

    def _stop_hype_train_listener(self) -> None:
        """Stop the hype train EventSub listener."""
        if self._hype_train_worker:
            self._hype_train_worker.stop()
            self._hype_train_worker.wait(2000)
            self._hype_train_worker = None
        self._twitch_broadcaster_ids.clear()

    def disconnect_all(self) -> None:
        """Disconnect all active chat connections."""
        for key in list(self._workers.keys()):
            self.close_chat(key)
        self._message_flush_timer.stop()
        self._stop_whisper_listener()
        self._stop_hype_train_listener()
        # Flush chat logs on shutdown
        self._chat_log_writer.flush_all()
        self._chat_log_flush_timer.stop()
        self._chat_log_enforce_timer.stop()

        # Stop emote cache background workers
        self._emote_cache.stop()

        # Clean up global emote/badge state when all chats are closed
        # This prevents unbounded memory growth in long-running sessions
        self._global_common_emotes.clear()
        self._global_twitch_emotes.clear()
        self._user_emotes.clear()
        self._channel_emote_maps.clear()
        self._resolved_emote_maps.clear()
        self._badge_url_map.clear()
        self._badge_image_sets.clear()
        self._badges_fetched_at.clear()
        self._queued_badge_urls.clear()

    def is_connected(self, channel_key: str) -> bool:
        """Check if a channel's chat is connected."""
        if channel_key in self._web_chat_keys:
            return True
        conn = self._connections.get(channel_key)
        return conn.is_connected if conn else False

    def _on_chat_connected(self, channel_key: str) -> None:
        """Handle a chat connection being established."""
        self.chat_connected.emit(channel_key)
        # Persist auto-detected YouTube handle
        if channel_key.startswith("youtube:") and self.settings.youtube.login_name:
            self.settings_changed.emit()

    def _on_connection_error(self, channel_key: str, message: str) -> None:
        """Handle connection error - log and forward to UI."""
        logger.error(f"Chat error for {channel_key}: {message}")
        self.chat_error.emit(channel_key, message)

    def _bind_emote_image_set(self, emote: ChatEmote) -> ImageSet | None:
        """Ensure an emote has an ImageSet bound to the shared cache."""
        image_set = emote.image_set
        if image_set is None and emote.url_template:
            specs = {}
            for scale in (1, 2, 3):
                key = f"emote:{emote.provider}:{emote.id}@{scale}x"
                specs[scale] = ImageSpec(scale=scale, key=key, url=emote.url_template)
            image_set = ImageSet(specs)
            emote.image_set = image_set
        if image_set is None:
            return None
        bound = image_set.bind(self._emote_cache)
        if bound is not image_set:
            emote.image_set = bound
        return emote.image_set

    def _ensure_badge_image_set(self, badge: ChatBadge, channel_key: str) -> ImageSet | None:
        """Ensure a badge has an ImageSet bound to the shared cache.

        Badge maps are per-channel because subscriber/bits badges differ per channel.
        Also applies the descriptive title from the badge API (e.g. "6-Month Subscriber").
        """
        if badge.image_set:
            return badge.image_set
        channel_badges = self._badge_url_map.get(channel_key, {})
        badge_data = channel_badges.get(badge.id)
        if badge_data:
            url, title = badge_data
            if title and not badge.title:
                badge.title = title
        else:
            url = badge.image_url
        if not url:
            self._queued_badge_urls.setdefault(channel_key, set()).add(badge.id)
            return None
        channel_sets = self._badge_image_sets.setdefault(channel_key, {})
        image_set = channel_sets.get(badge.id)
        if image_set is None:
            # Use channel-scoped cache key so subscriber badges don't collide
            cache_key = f"badge:{channel_key}:{badge.id}"
            specs = {
                scale: ImageSpec(
                    scale=scale,
                    key=f"{cache_key}@{scale}x",
                    url=url,
                )
                for scale in (1, 2, 3)
            }
            image_set = ImageSet(specs).bind(self._emote_cache)
            channel_sets[badge.id] = image_set
        badge.image_set = image_set
        return image_set

    def _on_messages_received(self, channel_key: str, messages: list) -> None:
        """Enqueue incoming messages for throttled processing."""
        if not messages:
            return
        pending = self._pending_messages.setdefault(channel_key, [])
        for msg in messages:
            if isinstance(msg, ChatMessage):
                pending.append(msg)
        if len(pending) > MAX_PENDING_MESSAGES:
            del pending[: len(pending) - MAX_PENDING_MESSAGES]
        if not self._message_flush_timer.isActive():
            self._message_flush_timer.start()

    def _flush_pending_messages(self) -> None:
        """Process queued messages in batches to avoid UI stalls."""
        if not self._pending_messages:
            self._message_flush_timer.stop()
            return

        for channel_key in list(self._pending_messages.keys()):
            queue = self._pending_messages.get(channel_key, [])
            if not queue:
                self._pending_messages.pop(channel_key, None)
                continue
            batch = queue[:MAX_MESSAGES_PER_FLUSH]
            del queue[:MAX_MESSAGES_PER_FLUSH]
            if not queue:
                self._pending_messages.pop(channel_key, None)

            processed = self._process_messages(channel_key, batch)
            if processed:
                self.messages_received.emit(channel_key, processed)
                # Log to disk
                self._chat_log_writer.append(channel_key, processed)
                # Emit raid signal for any raid messages
                for msg in processed:
                    if msg.is_raid:
                        self.raid_received.emit(channel_key, msg)
                # Detect own resub USERNOTICE → dismiss anniversary banner
                self._check_resub_shared(channel_key, processed)

        if not self._pending_messages:
            self._message_flush_timer.stop()

    def _process_messages(self, channel_key: str, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Handle incoming messages - queue badge/emote downloads, then forward."""
        if not messages:
            return []
        emote_map = self.get_emote_map(channel_key)
        for msg in messages:
            # Queue badge image downloads
            for badge in msg.user.badges:
                badge_set = self._ensure_badge_image_set(badge, channel_key)
                if badge_set:
                    badge_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_HIGH)

            # Match third-party emotes (7TV, BTTV, FFZ) in message text
            if emote_map:
                self._match_third_party_emotes(msg, emote_map)

            # Queue emote image downloads (native + third-party)
            for start, end, emote in msg.emote_positions:
                image_set = self._bind_emote_image_set(emote)
                if image_set:
                    image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_HIGH)

        # Learn emote names from incoming messages (e.g. Kick native emotes from
        # [emote:ID:name] tokens) so they appear in the spellcheck dictionary and
        # autocomplete.  Only emotes not already in the resolved map are added.
        channel_map = self._channel_emote_maps.get(channel_key, {})
        new_emote_count = 0
        for msg in messages:
            for _start, _end, emote in msg.emote_positions:
                if emote.name and emote.name not in emote_map and emote.name not in channel_map:
                    channel_map[emote.name] = emote
                    new_emote_count += 1
        if new_emote_count:
            self._channel_emote_maps[channel_key] = channel_map
            self._resolved_emote_maps.pop(channel_key, None)
            emote_map = self._rebuild_emote_map(channel_key)
            self.emote_map_updated.emit(channel_key)

        # Detect @mentions of our username
        nick_variants = self._get_our_nick_variants(channel_key)
        if nick_variants:
            mention_patterns = [f"@{v}" for v in nick_variants]
            for msg in messages:
                text_lower = msg.text.lower()
                if any(p in text_lower for p in mention_patterns):
                    msg.is_mention = True
                    # Emit mention signal (skip our own messages)
                    sender = msg.user.name.lower()
                    if sender not in nick_variants:
                        self.mention_received.emit(channel_key, msg)

        # Detect custom highlight keywords
        keywords = self.settings.chat.builtin.highlight_keywords
        if keywords:
            for msg in messages:
                if not msg.is_mention:
                    text_lower = msg.text.lower()
                    for kw in keywords:
                        if kw.lower() in text_lower:
                            msg.is_mention = True
                            break

        return messages

    def _flush_chat_logs(self) -> None:
        """Periodic timer callback to flush chat log buffers to disk."""
        if self._chat_log_writer.should_flush():
            self._chat_log_writer.flush_all()

    def _enforce_chat_log_limits(self) -> None:
        """Periodic timer callback to enforce disk limits on chat logs."""
        self._chat_log_writer.enforce_disk_limit()

    @property
    def chat_log_writer(self) -> ChatLogWriter:
        """Expose the chat log writer for external use (e.g., loading history)."""
        return self._chat_log_writer

    def update_chat_logging_settings(self, settings) -> None:
        """Update chat logging settings and start/stop timers accordingly."""
        self._chat_log_writer.settings = settings
        if settings.enabled:
            if not self._chat_log_flush_timer.isActive():
                self._chat_log_flush_timer.start()
            if not self._chat_log_enforce_timer.isActive():
                self._chat_log_enforce_timer.start()
        else:
            self._chat_log_flush_timer.stop()
            self._chat_log_enforce_timer.stop()

    def _get_our_nick_variants(self, channel_key: str) -> list[str]:
        """Get all lowercase name variants for mention matching.

        For YouTube this includes the display name, channel handle, and
        normalised forms (without spaces, etc.).  For Twitch/Kick it returns
        the single login name.
        """
        connection = self._connections.get(channel_key)
        if not connection:
            return []

        # YouTube connections carry a pre-built list of name variants
        variants = getattr(connection, "_nick_variants", None)
        if variants:
            return variants

        # Twitch / Kick: single nick
        nick = getattr(connection, "_nick", None)
        if nick and not nick.startswith("justinfan"):
            return [nick.lower()]
        return []

    def _rebuild_emote_map(self, channel_key: str) -> dict[str, ChatEmote]:
        """Build and cache a per-channel emote map."""
        merged: dict[str, ChatEmote] = {}
        merged.update(self._global_common_emotes)
        livestream = self._livestreams.get(channel_key)
        if livestream and livestream.channel.platform == StreamPlatform.TWITCH:
            merged.update(self._global_twitch_emotes)
            merged.update(self._user_emotes)
        merged.update(self._channel_emote_maps.get(channel_key, {}))
        self._resolved_emote_maps[channel_key] = merged
        return merged

    def _match_third_party_emotes(self, msg: ChatMessage, emote_map: dict[str, ChatEmote]) -> None:
        """Scan message text for third-party emote names and add to emote_positions."""
        text = msg.text
        if not text or not emote_map:
            return
        new_positions = find_third_party_emotes(
            text, emote_map, [(start, end) for start, end, _ in msg.emote_positions]
        )

        if new_positions:
            # Merge with existing positions and sort by start
            msg.emote_positions = sorted(msg.emote_positions + new_positions, key=lambda x: x[0])
            logger.debug(f"Matched {len(new_positions)} 3rd-party emotes in message")

    def backfill_third_party_emotes(self, channel_key: str, messages: list[ChatMessage]) -> int:
        """Backfill third-party emotes for existing messages.

        Returns number of messages updated.
        """
        emote_map = self.get_emote_map(channel_key)
        if not emote_map or not messages:
            return 0

        updated = 0
        for msg in messages:
            if not isinstance(msg, ChatMessage):
                continue
            before = len(msg.emote_positions)
            self._match_third_party_emotes(msg, emote_map)
            if len(msg.emote_positions) != before:
                updated += 1

        return updated

    def _fetch_emotes_for_channel(self, channel_key: str, livestream: Livestream) -> None:
        """Kick off async emote/badge fetching for a channel."""
        self._ensure_global_emotes()
        if livestream.channel.platform == StreamPlatform.TWITCH:
            self._ensure_user_emotes()
        providers = self.settings.chat.builtin.emote_providers
        platform_name = livestream.channel.platform.value.lower()
        channel_id = livestream.channel.channel_id

        now = time.monotonic()
        last_fetch = self._channel_emotes_fetched_at.get(channel_key, 0)
        badges_stale = (
            livestream.channel.platform == StreamPlatform.TWITCH
            and (now - self._badges_fetched_at.get(channel_key, 0)) >= CHANNEL_EMOTE_TTL
        )
        if last_fetch and (now - last_fetch) < CHANNEL_EMOTE_TTL:
            self._resolved_emote_maps.pop(channel_key, None)
            self._rebuild_emote_map(channel_key)
            self.emote_map_updated.emit(channel_key)
            if badges_stale:
                badge_worker = EmoteFetchWorker(
                    channel_key=channel_key,
                    platform=platform_name,
                    channel_id=channel_id,
                    providers=providers,
                    oauth_token=self.settings.twitch.access_token,
                    client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
                    fetch_emotes=False,
                    fetch_badges=True,
                    parent=self,
                )
                badge_worker.badges_fetched.connect(self._on_badges_fetched)
                self._emote_fetch_workers[channel_key] = badge_worker
                badge_worker.start()
            return

        worker = EmoteFetchWorker(
            channel_key=channel_key,
            platform=platform_name,
            channel_id=channel_id,
            providers=providers if self.settings.chat.builtin.show_emotes else [],
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            fetch_emotes=True,
            fetch_badges=True,
            parent=self,
        )
        worker.emotes_fetched.connect(self._on_emotes_fetched)
        worker.badges_fetched.connect(self._on_badges_fetched)
        self._emote_fetch_workers[channel_key] = worker
        worker.start()

    def _fetch_socials_for_channel(self, channel_key: str, livestream: Livestream) -> None:
        """Kick off async socials fetching for any platform."""
        if not self.settings.chat.builtin.show_socials_banner:
            return

        channel_id = livestream.channel.channel_id
        platform = livestream.channel.platform

        async def fetch():
            from .socials import fetch_socials

            return (channel_key, await fetch_socials(channel_id, platform))

        worker = AsyncTaskWorker(fetch, parent=self)
        worker.result_ready.connect(self._on_socials_fetched)
        self._socials_fetch_workers[channel_key] = worker
        worker.start()

    def _on_socials_fetched(self, result: object) -> None:
        """Handle fetched social links - emit to UI."""
        channel_key, socials = result
        if not socials:
            return
        logger.info(f"Fetched socials for {channel_key}: {list(socials.keys())}")
        self.socials_fetched.emit(channel_key, socials)

    def _fetch_sub_anniversary(self, channel_key: str, livestream: Livestream) -> None:
        """Fetch Twitch subscription info and detect if anniversary is shareable.

        The anniversary share window is available for the first ~7 days after
        a billing cycle renews. There is no direct GQL field for this, so we
        compute it from ``renewsAt`` and ``subscriptionTenure.daysRemaining``.
        """
        if livestream.channel.platform != StreamPlatform.TWITCH:
            return
        if not self.settings.chat.builtin.show_sub_anniversary_banner:
            return
        # GQL requires browser auth token (OAuth access token is rejected)
        auth_token = self.settings.twitch.browser_auth_token
        if not auth_token:
            return

        channel_login = livestream.channel.channel_id

        async def fetch():
            import aiohttp

            gql_url = "https://gql.twitch.tv/gql"
            headers = {
                "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
                "Authorization": f"OAuth {auth_token}",
                "Content-Type": "application/json",
            }
            query = {
                "query": """
                    query SubAnniversary($login: String!) {
                        user(login: $login) {
                            id
                            displayName
                            self {
                                subscriptionBenefit {
                                    id
                                    tier
                                    renewsAt
                                    purchasedWithPrime
                                    gift {
                                        isGift
                                    }
                                }
                                subscriptionTenure(tenureMethod: CUMULATIVE) {
                                    months
                                    daysRemaining
                                }
                            }
                        }
                    }
                """,
                "variables": {"login": channel_login},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(gql_url, json=query, headers=headers) as resp:
                    if resp.status != 200:
                        return (channel_key, None)
                    data = await resp.json()

            user = (data.get("data") or {}).get("user")
            if not user:
                return (channel_key, None)
            self_data = user.get("self")
            if not self_data:
                return (channel_key, None)
            sub_benefit = self_data.get("subscriptionBenefit")
            if not sub_benefit:
                return (channel_key, None)

            tenure = self_data.get("subscriptionTenure") or {}
            months = tenure.get("months", 0)
            days_remaining = tenure.get("daysRemaining")

            # Determine if the anniversary share window is active.
            # renewsAt is when the NEXT billing cycle starts. If there
            # are >= 22 days left, the sub renewed within the last ~8
            # days and the anniversary share is likely still available.
            renews_at_str = sub_benefit.get("renewsAt")
            if not renews_at_str:
                return (channel_key, None)
            renews_at = datetime.fromisoformat(renews_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days_until_renewal = (renews_at - now).total_seconds() / 86400
            if days_until_renewal < 22:
                return (channel_key, None)

            tier = sub_benefit.get("tier", "1000")
            is_prime = sub_benefit.get("purchasedWithPrime", False)
            is_gift = False
            gift = sub_benefit.get("gift")
            if gift:
                is_gift = gift.get("isGift", False)

            return (
                channel_key,
                {
                    "months": months,
                    "days_remaining": days_remaining,
                    "tier": tier,
                    "is_prime": is_prime,
                    "is_gift": is_gift,
                    "channel_display_name": user.get("displayName", channel_login),
                    "channel_login": channel_login,
                    "renews_at": renews_at_str,
                },
            )

        worker = AsyncTaskWorker(fetch, error_log_level=logging.DEBUG, parent=self)
        worker.result_ready.connect(self._on_sub_anniversary_fetched)
        self._sub_anniversary_workers[channel_key] = worker
        worker.start()

    def _on_sub_anniversary_fetched(self, result: object) -> None:
        """Handle fetched sub anniversary info - emit to UI."""
        channel_key, sub_info = result
        if not sub_info:
            return
        months = sub_info.get("months", 0)
        days_left = sub_info.get("days_remaining")
        logger.info(
            f"Sub anniversary active for {channel_key}: {months} months, {days_left} days remaining"
        )
        self.sub_anniversary_fetched.emit(channel_key, sub_info)

    def _check_resub_shared(self, channel_key: str, messages: list[ChatMessage]) -> None:
        """Detect own resub USERNOTICE and dismiss the anniversary banner.

        When the user clicks "Share" on Twitch, a USERNOTICE with msg-id=resub
        is broadcast in IRC. We detect this by checking for system messages from
        our own Twitch username that contain "subscribed".
        """
        login = self.settings.twitch.login_name
        if not login:
            return
        login_lower = login.lower()
        for msg in messages:
            if (
                msg.is_system
                and msg.platform == StreamPlatform.TWITCH
                and msg.user.name.lower() == login_lower
                and "subscribed" in (msg.system_text or "").lower()
            ):
                logger.info(f"Own resub detected for {channel_key}, dismissing anniversary banner")
                self.sub_anniversary_fetched.emit(channel_key, {"redeemed": True})
                return

    def _on_emotes_fetched(self, channel_key: str, emotes: list) -> None:
        """Handle fetched emote list - add to map (images load on-demand when rendered)."""
        channel_map = self._channel_emote_maps.setdefault(channel_key, {})
        for emote in emotes:
            if not isinstance(emote, ChatEmote):
                continue
            # Bind image_set for on-demand loading when emote is rendered
            self._bind_emote_image_set(emote)
            channel_map[emote.name] = emote

        # Debug: log short emote names and emotes containing "om"
        short_emotes = [e.name for e in emotes if len(e.name) <= 4]
        om_emotes = [e.name for e in emotes if "om" in e.name.lower()]
        self._resolved_emote_maps.pop(channel_key, None)
        resolved_map = self._rebuild_emote_map(channel_key)
        self._channel_emotes_fetched_at[channel_key] = time.monotonic()
        logger.info(
            f"Loaded {len(emotes)} emotes for {channel_key}, emote map size: {len(resolved_map)}"
        )
        if short_emotes:
            logger.info(f"Short emotes (<=4 chars): {short_emotes}")
        if om_emotes:
            logger.debug(f"Emotes containing 'om': {om_emotes[:20]}")

        # Notify widgets immediately so autocomplete has the emote map
        if channel_key in self._livestreams:
            self.emote_cache_updated.emit()
            self.emote_map_updated.emit(channel_key)

        # Downloads are handled by the image store

    def _on_user_emotes_fetched(self, emotes: list) -> None:
        """Handle fresh user emotes from background fetch - update cache.

        Part of stale-while-revalidate: this is called when fresh emotes arrive.
        We update the cache and add any new emotes to the emote map.
        """
        if not emotes:
            return

        old_ids = {e.id for e in self._user_emotes.values()}
        new_list = [e for e in emotes if isinstance(e, ChatEmote)]
        new_ids = {e.id for e in new_list}

        if old_ids != new_ids:
            added = new_ids - old_ids
            removed = old_ids - new_ids
            logger.info(
                f"User emotes changed: +{len(added)} -{len(removed)} (total: {len(new_ids)})"
            )

            self._user_emotes = {e.name: e for e in new_list}

            # Bind image sets for on-demand loading (no prefetch)
            for emote in new_list:
                if emote.id in added:
                    self._bind_emote_image_set(emote)

            # Refresh emote maps for all Twitch channels
            for channel_key, livestream in self._livestreams.items():
                if livestream.channel.platform == StreamPlatform.TWITCH:
                    self._resolved_emote_maps.pop(channel_key, None)
                    self._rebuild_emote_map(channel_key)

            self.emote_cache_updated.emit()
            self.emote_map_updated.emit("")

        self._user_emotes_fetched_at = time.monotonic()

    def _on_badges_fetched(self, channel_key: str, badge_map: dict) -> None:
        """Handle fetched badge data from Twitch API.

        badge_map: {badge_id: (image_url, title)}
        """
        channel_badges = self._badge_url_map.setdefault(channel_key, {})
        channel_badges.update(badge_map)
        self._badges_fetched_at[channel_key] = time.monotonic()
        logger.info(f"Badge URL map updated for {channel_key}: {len(channel_badges)} entries")

        # Build or update badge image sets for this channel
        channel_sets = self._badge_image_sets.setdefault(channel_key, {})
        for badge_id, (url, _title) in badge_map.items():
            if badge_id not in channel_sets:
                cache_key = f"badge:{channel_key}:{badge_id}"
                specs = {
                    scale: ImageSpec(
                        scale=scale,
                        key=f"{cache_key}@{scale}x",
                        url=url,
                    )
                    for scale in (1, 2, 3)
                }
                channel_sets[badge_id] = ImageSet(specs).bind(self._emote_cache)

        # Re-queue badges that were attempted before the map was ready
        requeue_count = 0
        queued = self._queued_badge_urls.get(channel_key, set())
        for badge_id in list(queued):
            cleaned_id = badge_id.removeprefix("badge:")
            image_set = channel_sets.get(cleaned_id)
            if image_set:
                image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_HIGH)
                queued.discard(badge_id)
                requeue_count += 1

        if requeue_count:
            logger.debug(f"Re-queued {requeue_count} badges with correct URLs")

        # Notify widgets so they can re-resolve badges on already-displayed messages
        self.badge_map_ready.emit(channel_key)

    def resolve_badges_on_messages(self, channel_key: str, messages: list[ChatMessage]) -> int:
        """Re-resolve badge ImageSets on existing messages after badge map arrives.

        Returns the number of badges that were newly resolved.
        """
        resolved = 0
        for msg in messages:
            for badge in msg.user.badges:
                if badge.image_set:
                    continue
                image_set = self._ensure_badge_image_set(badge, channel_key)
                if image_set:
                    image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_HIGH)
                    resolved += 1
        return resolved

    def on_emote_settings_changed(self) -> None:
        """Handle changes to emote provider settings."""
        providers = tuple(self.settings.chat.builtin.emote_providers)
        if providers == self._last_emote_providers:
            return
        self._last_emote_providers = providers
        self._global_common_emotes.clear()
        self._global_emotes_fetched_at = 0.0
        self._channel_emote_maps.clear()
        self._resolved_emote_maps.clear()
        self._channel_emotes_fetched_at.clear()
        self._ensure_global_emotes(force=True)
        for channel_key, livestream in self._livestreams.items():
            self._fetch_emotes_for_channel(channel_key, livestream)

    def _ensure_global_emotes(self, force: bool = False) -> None:
        """Fetch global emotes if stale."""
        if not self.settings.chat.builtin.show_emotes:
            return
        if self._global_emote_worker and self._global_emote_worker.isRunning():
            return
        now = time.monotonic()
        if (
            not force
            and self._global_common_emotes
            and (now - self._global_emotes_fetched_at < GLOBAL_EMOTE_TTL)
        ):
            return
        providers = self.settings.chat.builtin.emote_providers
        oauth_token = self.settings.twitch.access_token
        client_id = self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID

        async def fetch():
            return await _fetch_global_emotes(providers, oauth_token, client_id)

        self._global_emote_worker = AsyncTaskWorker(fetch, parent=self)
        self._global_emote_worker.result_ready.connect(self._on_global_emotes_fetched)
        self._global_emote_worker.start()

    def _on_global_emotes_fetched(self, payload: object) -> None:
        """Handle global emote fetch results."""
        if not payload:
            return
        twitch_list = payload.get("twitch", [])
        common_list = payload.get("common", [])
        self._global_twitch_emotes = {}
        for emote in twitch_list:
            if not isinstance(emote, ChatEmote):
                continue
            self._bind_emote_image_set(emote)
            self._global_twitch_emotes[emote.name] = emote

        self._global_common_emotes = {}
        for emote in common_list:
            if not isinstance(emote, ChatEmote):
                continue
            self._bind_emote_image_set(emote)
            self._global_common_emotes[emote.name] = emote
        self._global_emotes_fetched_at = time.monotonic()

        # Rebuild emote maps for all open chats
        for channel_key in self._livestreams.keys():
            self._resolved_emote_maps.pop(channel_key, None)
            self._rebuild_emote_map(channel_key)

        self.emote_map_updated.emit("")

    def _ensure_user_emotes(self, force: bool = False) -> None:
        """Fetch user emotes if stale."""
        if not self.settings.chat.builtin.show_emotes:
            return
        if not self.settings.twitch.access_token:
            return
        if self._user_emote_worker and self._user_emote_worker.isRunning():
            return
        now = time.monotonic()
        time_since_fetch = now - self._user_emotes_fetched_at
        if not force and self._user_emotes and time_since_fetch < USER_EMOTE_TTL:
            return
        oauth_token = self.settings.twitch.access_token
        client_id = self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID

        async def fetch():
            return await _fetch_user_emotes(oauth_token, client_id)

        self._user_emote_worker = AsyncTaskWorker(fetch, parent=self)
        self._user_emote_worker.result_ready.connect(self._on_user_emotes_fetched)
        self._user_emote_worker.start()

    def _on_emote_loaded(self, key: str) -> None:
        """Handle emote cache updated signal - trigger repaint."""
        if not self._emote_update_pending:
            self._emote_update_pending = True
            self._emote_update_timer.start(50)

    def _emit_emote_cache_update(self) -> None:
        self._emote_update_pending = False
        self.emote_cache_updated.emit()

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
        elif platform == StreamPlatform.CHATURBATE:
            from .connections.chaturbate import ChaturbateChatConnection

            return ChaturbateChatConnection(parent=self)
        return None

    def _get_connection_kwargs(self, livestream: Livestream) -> dict:
        """Get platform-specific connection kwargs."""
        kwargs: dict = {}
        if livestream.channel.platform == StreamPlatform.KICK:
            kwargs["chatroom_id"] = getattr(livestream, "chatroom_id", None)
        elif livestream.channel.platform == StreamPlatform.YOUTUBE:
            kwargs["video_id"] = livestream.video_id
        return kwargs

    def _disconnect_connection_signals(self, channel_key: str) -> None:
        """Disconnect signals from a connection to break reference cycles.

        This should be called before removing a connection from the dict
        to prevent memory leaks from signal connections.
        """
        connection = self._connections.pop(channel_key, None)
        if connection:
            try:
                connection.messages_received.disconnect()
                connection.moderation_event.disconnect()
                connection.error.disconnect()
                connection.connected.disconnect()
                connection.disconnected.disconnect()
                connection.room_state_changed.disconnect()
            except (RuntimeError, TypeError):
                # Signal may already be disconnected
                pass
