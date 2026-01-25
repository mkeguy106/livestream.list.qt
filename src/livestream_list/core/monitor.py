"""Stream monitoring service."""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime

from ..api.base import BaseApiClient
from ..api.kick import KickApiClient
from ..api.twitch import TwitchApiClient
from ..api.youtube import YouTubeApiClient
from .models import Channel, Livestream, StreamPlatform
from .settings import Settings, get_data_dir

logger = logging.getLogger(__name__)

# Debounce delay for saving channels (in seconds)
SAVE_DEBOUNCE_DELAY = 2.0


class StreamMonitor:
    """
    Central service for monitoring livestreams across platforms.
    Handles channel persistence, periodic refreshing, and event notifications.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._channels: dict[str, Channel] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._clients: dict[StreamPlatform, BaseApiClient] = {}
        self._running = False
        self._refresh_task: asyncio.Task | None = None

        # Event callbacks
        self._on_stream_online: list[Callable[[Livestream], None]] = []
        self._on_stream_offline: list[Callable[[Livestream], None]] = []
        self._on_refresh_complete: list[Callable[[list[Livestream]], None]] = []

        # Track initial load to suppress startup notifications
        self._initial_load_complete = False

        # Debounced save mechanism
        self._save_timer: threading.Timer | None = None
        self._save_lock = threading.Lock()
        self._pending_save = False

        # Lock for protecting channel/livestream state from concurrent access
        self._state_lock = threading.RLock()

        # Initialize API clients
        self._init_clients()

    def _init_clients(self) -> None:
        """Initialize API clients for each platform."""
        self._clients[StreamPlatform.TWITCH] = TwitchApiClient(self.settings.twitch)
        self._clients[StreamPlatform.YOUTUBE] = YouTubeApiClient(
            self.settings.youtube,
            concurrency=self.settings.performance.youtube_concurrency,
        )
        self._clients[StreamPlatform.KICK] = KickApiClient(
            self.settings.kick,
            concurrency=self.settings.performance.kick_concurrency,
        )

    @property
    def channels(self) -> list[Channel]:
        """Get all monitored channels (thread-safe snapshot)."""
        with self._state_lock:
            return list(self._channels.values())

    @property
    def livestreams(self) -> list[Livestream]:
        """Get all livestreams (live and offline) (thread-safe snapshot)."""
        with self._state_lock:
            return list(self._livestreams.values())

    @property
    def live_streams(self) -> list[Livestream]:
        """Get only live streams (thread-safe snapshot)."""
        with self._state_lock:
            return [s for s in self._livestreams.values() if s.live]

    def get_client(self, platform: StreamPlatform) -> BaseApiClient:
        """Get the API client for a platform."""
        return self._clients[platform]

    def on_stream_online(self, callback: Callable[[Livestream], None]) -> None:
        """Register a callback for when a stream goes online."""
        self._on_stream_online.append(callback)

    def on_stream_offline(self, callback: Callable[[Livestream], None]) -> None:
        """Register a callback for when a stream goes offline."""
        self._on_stream_offline.append(callback)

    def on_refresh_complete(self, callback: Callable[[list[Livestream]], None]) -> None:
        """Register a callback for when a refresh cycle completes."""
        self._on_refresh_complete.append(callback)

    async def initialize(self) -> None:
        """Initialize the monitor service."""
        # Load saved channels
        await self._load_channels()

        # Authorize API clients
        for platform, client in self._clients.items():
            if self._has_channels_for_platform(platform):
                if not await client.is_authorized():
                    try:
                        await client.authorize()
                    except Exception as e:
                        logger.error(f"Failed to authorize {client.name}: {e}")

        # Initial refresh (notifications suppressed)
        await self.refresh()

        # Mark initial load complete - subsequent refreshes will send notifications
        self._initial_load_complete = True

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return

        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False

        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        # Close API clients
        for client in self._clients.values():
            await client.close()

    async def _refresh_loop(self) -> None:
        """Main refresh loop."""
        while self._running:
            try:
                await asyncio.sleep(self.settings.refresh_interval)
                await self.refresh()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in refresh loop: {e}")

    async def refresh(self) -> None:
        """Refresh all livestream statuses."""
        # Take a snapshot of channels under lock to avoid iteration issues
        with self._state_lock:
            if not self._channels:
                return
            channels_snapshot = list(self._channels.values())

        # Group channels by platform
        by_platform: dict[StreamPlatform, list[Channel]] = {}
        for channel in channels_snapshot:
            if channel.platform not in by_platform:
                by_platform[channel.platform] = []
            by_platform[channel.platform].append(channel)

        # Query each platform concurrently
        tasks = []
        for platform, channels in by_platform.items():
            client = self._clients[platform]
            tasks.append(self._query_platform(client, channels))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        all_livestreams: list[Livestream] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Platform query error: {result}")
                continue
            all_livestreams.extend(result)

        # Update internal state and fire events
        events_to_fire: list[tuple[str, Livestream]] = []  # (event_type, livestream)

        with self._state_lock:
            for livestream in all_livestreams:
                key = livestream.channel.unique_key
                existing = self._livestreams.get(key)

                if existing:
                    went_live = existing.update_from(livestream)
                    if went_live:
                        events_to_fire.append(("online", existing))
                    elif not livestream.live and existing.live:
                        events_to_fire.append(("offline", existing))
                else:
                    self._livestreams[key] = livestream
                    if livestream.live:
                        events_to_fire.append(("online", livestream))

        # Fire events outside the lock to avoid deadlocks
        for event_type, livestream in events_to_fire:
            if event_type == "online":
                self._fire_stream_online(livestream)
            else:
                self._fire_stream_offline(livestream)

        # Fire refresh complete
        for callback in self._on_refresh_complete:
            try:
                callback(self.livestreams)
            except Exception as e:
                logger.error(f"Refresh callback error: {e}")

    async def _query_platform(
        self, client: BaseApiClient, channels: list[Channel]
    ) -> list[Livestream]:
        """Query a platform for livestream status."""
        try:
            if not await client.is_authorized():
                if not await client.authorize():
                    # Return offline status for all channels
                    return [
                        Livestream(
                            channel=ch,
                            live=False,
                            error_message=f"{client.name} not authorized",
                        )
                        for ch in channels
                    ]

            return await client.get_livestreams(channels)
        except Exception as e:
            logger.error(f"Error querying {client.name}: {e}")
            return [Livestream(channel=ch, live=False, error_message=str(e)) for ch in channels]

    def _fire_stream_online(self, livestream: Livestream) -> None:
        """Fire stream online callbacks."""
        # Suppress notifications during initial startup
        if not self._initial_load_complete:
            return

        if livestream.channel.dont_notify:
            return

        for callback in self._on_stream_online:
            try:
                callback(livestream)
            except Exception as e:
                logger.error(f"Stream online callback error: {e}")

    def _fire_stream_offline(self, livestream: Livestream) -> None:
        """Fire stream offline callbacks."""
        for callback in self._on_stream_offline:
            try:
                callback(livestream)
            except Exception as e:
                logger.error(f"Stream offline callback error: {e}")

    def _has_channels_for_platform(self, platform: StreamPlatform) -> bool:
        """Check if we have any channels for a platform."""
        with self._state_lock:
            return any(c.platform == platform for c in self._channels.values())

    async def add_channel(self, channel_id: str, platform: StreamPlatform) -> Channel | None:
        """Add a channel to monitor."""
        client = self._clients[platform]

        # Verify the channel exists
        channel = await client.get_channel_info(channel_id)
        if not channel:
            return None

        with self._state_lock:
            # Check for duplicates
            if channel.unique_key in self._channels:
                return self._channels[channel.unique_key]

            self._channels[channel.unique_key] = channel

        # Create initial livestream entry
        livestream = await client.get_livestream(channel)

        with self._state_lock:
            self._livestreams[channel.unique_key] = livestream

        # Save to disk
        await self._save_channels()

        return channel

    async def remove_channel(self, channel: Channel) -> None:
        """Remove a channel from monitoring."""
        key = channel.unique_key

        with self._state_lock:
            self._channels.pop(key, None)
            self._livestreams.pop(key, None)

        await self._save_channels()

    async def import_follows(self, username: str, platform: StreamPlatform) -> list[Channel]:
        """Import followed channels for a user."""
        client = self._clients[platform]

        try:
            channels = await client.get_followed_channels(username)
        except NotImplementedError:
            return []

        added: list[Channel] = []
        with self._state_lock:
            for channel in channels:
                if channel.unique_key not in self._channels:
                    self._channels[channel.unique_key] = channel
                    self._livestreams[channel.unique_key] = Livestream(channel=channel)
                    added.append(channel)

        if added:
            await self._save_channels()
            await self.refresh()

        return added

    def add_channel_direct(self, channel: Channel) -> bool:
        """Add a channel directly without API verification.

        Used for import operations where the channel data is already known.
        Returns True if the channel was added (not a duplicate).
        """
        key = channel.unique_key
        with self._state_lock:
            if key in self._channels:
                return False
            self._channels[key] = channel
            self._livestreams[key] = Livestream(channel=channel)
            return True

    def remove_channels(self, keys: list[str]) -> None:
        """Remove multiple channels by their unique keys."""
        with self._state_lock:
            for key in keys:
                self._channels.pop(key, None)
                self._livestreams.pop(key, None)

    def has_channel(self, key: str) -> bool:
        """Check if a channel exists by its unique key."""
        with self._state_lock:
            return key in self._channels

    def reset_all_sessions(self) -> None:
        """Reset all API client sessions.

        Call before running async operations in a new event loop.
        """
        for client in self._clients.values():
            client.reset_session()

    async def close_all_sessions(self) -> None:
        """Close all API client sessions."""
        for client in self._clients.values():
            await client.close()

    async def load_channels(self) -> None:
        """Public method to load channels from disk."""
        await self._load_channels()

    def set_initial_load_complete(self) -> None:
        """Mark initial load as complete, enabling notifications."""
        self._initial_load_complete = True

    def suppress_notifications(self) -> None:
        """Temporarily suppress stream online notifications."""
        self._initial_load_complete = False

    def resume_notifications(self) -> None:
        """Resume stream online notifications after suppression."""
        self._initial_load_complete = True

    def set_dont_notify(self, channel: Channel, dont_notify: bool) -> None:
        """Set the notification preference for a channel."""
        with self._state_lock:
            if channel.unique_key in self._channels:
                self._channels[channel.unique_key].dont_notify = dont_notify
        self._schedule_debounced_save()

    def set_favorite(self, channel: Channel, favorite: bool) -> None:
        """Set the favorite status for a channel."""
        with self._state_lock:
            if channel.unique_key in self._channels:
                self._channels[channel.unique_key].favorite = favorite
        self._schedule_debounced_save()

    def _schedule_debounced_save(self) -> None:
        """Schedule a debounced save operation.

        This prevents excessive disk writes when multiple flag changes happen
        in quick succession (e.g., bulk operations).
        """
        with self._save_lock:
            # Cancel existing timer if any
            if self._save_timer is not None:
                self._save_timer.cancel()

            self._pending_save = True

            # Schedule new save after delay
            self._save_timer = threading.Timer(SAVE_DEBOUNCE_DELAY, self._execute_debounced_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _execute_debounced_save(self) -> None:
        """Execute the debounced save synchronously."""
        with self._save_lock:
            if not self._pending_save:
                return
            self._pending_save = False
            self._save_timer = None

        # Save synchronously (this runs in timer thread)
        self._save_channels_sync()

    def _serialize_channels(self) -> list[dict]:
        """Serialize channels to a list of dicts for JSON persistence."""
        data = []
        with self._state_lock:
            for ch in self._channels.values():
                ch_data = {
                    "channel_id": ch.channel_id,
                    "platform": ch.platform.value,
                    "display_name": ch.display_name,
                    "imported_by": ch.imported_by,
                    "dont_notify": ch.dont_notify,
                    "favorite": ch.favorite,
                    "added_at": ch.added_at.isoformat(),
                }
                livestream = self._livestreams.get(ch.unique_key)
                if livestream and livestream.last_live_time:
                    ch_data["last_live_time"] = livestream.last_live_time.isoformat()
                data.append(ch_data)
        return data

    def _save_channels_sync(self) -> None:
        """Save channels to disk synchronously."""
        path = get_data_dir() / "channels.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._serialize_channels(), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving channels: {e}")

    def flush_pending_save(self) -> None:
        """Immediately save any pending changes.

        Call this on application shutdown to ensure no data is lost.
        """
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

            if self._pending_save:
                self._pending_save = False
                self._save_channels_sync()

    async def save_channels(self) -> None:
        """Public method to save channels to disk."""
        await self._save_channels()

    async def _load_channels(self) -> None:
        """Load channels from disk."""
        path = get_data_dir() / "channels.json"

        if not path.exists():
            return

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            for ch_data in data:
                channel = Channel(
                    channel_id=ch_data["channel_id"],
                    platform=StreamPlatform(ch_data["platform"]),
                    display_name=ch_data.get("display_name"),
                    imported_by=ch_data.get("imported_by"),
                    dont_notify=ch_data.get("dont_notify", False),
                    favorite=ch_data.get("favorite", False),
                )

                if "added_at" in ch_data:
                    try:
                        channel.added_at = datetime.fromisoformat(ch_data["added_at"])
                    except ValueError:
                        pass

                self._channels[channel.unique_key] = channel
                livestream = Livestream(channel=channel)
                # Restore last_live_time if saved
                if "last_live_time" in ch_data:
                    try:
                        livestream.last_live_time = datetime.fromisoformat(
                            ch_data["last_live_time"]
                        )
                    except ValueError:
                        pass
                self._livestreams[channel.unique_key] = livestream

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Error loading channels: {e}")

    async def _save_channels(self) -> None:
        """Save channels to disk."""
        path = get_data_dir() / "channels.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._serialize_channels(), f, indent=2)
