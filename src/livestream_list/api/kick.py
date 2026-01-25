"""Kick API client."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import KickSettings
from .base import BaseApiClient

logger = logging.getLogger(__name__)


class KickApiClient(BaseApiClient):
    """Client for Kick API.

    Note: Kick's official API doesn't support importing followed channels.
    Channels must be added manually.
    """

    # Unofficial API (no auth required)
    BASE_URL = "https://kick.com/api/v2"
    BASE_URL_V1 = "https://kick.com/api/v1"

    def __init__(self, settings: KickSettings, concurrency: int = 10) -> None:
        super().__init__()
        self.settings = settings
        self.concurrency = concurrency

    @property
    def platform(self) -> StreamPlatform:
        return StreamPlatform.KICK

    @property
    def name(self) -> str:
        return "Kick"

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }

    async def is_authorized(self) -> bool:
        """Check if we have a valid access token."""
        # For public data, no auth needed
        return True

    async def authorize(self) -> bool:
        """Kick doesn't require authentication for public data."""
        return True

    async def get_channel_info(self, channel_id: str) -> Channel | None:
        """Get channel info by username."""
        try:
            url = f"{self.BASE_URL}/channels/{channel_id}"
            logger.info(f"Kick: Fetching channel info from {url}")
            async with self.session.get(
                url,
                headers=self._get_headers(),
            ) as resp:
                logger.info(f"Kick: Response status {resp.status} for {channel_id}")
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(
                        f"Kick: Failed to get channel {channel_id}: "
                        f"HTTP {resp.status}, body: {text[:500]}"
                    )
                    return None

                data = await resp.json()

                return Channel(
                    channel_id=data.get("slug", channel_id),
                    platform=StreamPlatform.KICK,
                    display_name=data.get("user", {}).get("username", channel_id),
                )

        except aiohttp.ClientError as e:
            logger.error(f"Kick: Network error getting channel {channel_id}: {e}")
            return None

    async def _get_last_video_date(self, channel: Channel) -> datetime | None:
        """Get the start time of the most recent video/VOD."""
        try:
            async with self.session.get(
                f"{self.BASE_URL}/channels/{channel.channel_id}/videos",
                headers=self._get_headers(),
            ) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                if not data or not isinstance(data, list) or len(data) == 0:
                    return None

                # Get the first (most recent) video
                video = data[0]
                start_time = video.get("start_time") or video.get("created_at")
                if start_time:
                    try:
                        # Kick uses format like "2026-01-03 02:44:40"
                        dt = datetime.fromisoformat(start_time.replace(" ", "T"))
                        # Add UTC timezone if not present
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except ValueError:
                        pass

        except aiohttp.ClientError:
            pass

        return None

    async def get_livestream(self, channel: Channel) -> Livestream:
        """Get livestream status for a channel."""

        async def do_request() -> Livestream:
            async with self.session.get(
                f"{self.BASE_URL}/channels/{channel.channel_id}",
                headers=self._get_headers(),
            ) as resp:
                if self._is_retryable_status(resp.status):
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status
                    )
                if resp.status != 200:
                    return Livestream(
                        channel=channel,
                        live=False,
                        error_message=f"HTTP {resp.status}",
                    )

                data = await resp.json()
                livestream_data = data.get("livestream")

                if not livestream_data or not livestream_data.get("is_live"):
                    # Get last video date for offline channels
                    last_live_time = await self._get_last_video_date(channel)
                    return Livestream(channel=channel, live=False, last_live_time=last_live_time)

                # Parse start time (prefer start_time over created_at)
                start_time = None
                time_str = livestream_data.get("start_time") or livestream_data.get("created_at")
                if time_str:
                    try:
                        # Kick uses format like "2024-01-15 12:30:00" in UTC
                        start_time = datetime.fromisoformat(time_str.replace(" ", "T"))
                        # Add UTC timezone if not present
                        if start_time.tzinfo is None:
                            start_time = start_time.replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

                # Get category/game
                game = None
                categories = livestream_data.get("categories", [])
                if categories:
                    game = categories[0].get("name")

                # Handle thumbnail safely (can be None or a dict)
                thumbnail = livestream_data.get("thumbnail")
                thumbnail_url = thumbnail.get("url") if thumbnail else None

                # Extract chatroom ID for built-in chat
                chatroom_id = None
                chatroom_data = data.get("chatroom", {})
                if chatroom_data:
                    chatroom_id = chatroom_data.get("id")

                return Livestream(
                    channel=channel,
                    live=True,
                    title=livestream_data.get("session_title"),
                    game=game,
                    viewers=livestream_data.get("viewer_count", 0),
                    start_time=start_time,
                    thumbnail_url=thumbnail_url,
                    language=livestream_data.get("language"),
                    is_mature=livestream_data.get("is_mature", False),
                    chatroom_id=chatroom_id,
                )

        try:
            return await self._retry_with_backoff(do_request)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return Livestream(
                channel=channel,
                live=False,
                error_message=str(e),
            )

    async def get_livestreams(self, channels: list[Channel]) -> list[Livestream]:
        """Get livestream status for multiple channels."""
        if not channels:
            return []

        # Kick doesn't have a batch endpoint, so we query individually
        # but run them concurrently with a semaphore to limit parallel requests
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_with_semaphore(channel: Channel) -> Livestream:
            async with semaphore:
                return await self.get_livestream(channel)

        tasks = [fetch_with_semaphore(channel) for channel in channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to offline Livestream objects
        final_results: list[Livestream] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(
                    Livestream(
                        channel=channels[i],
                        live=False,
                        error_message=str(result),
                    )
                )
            else:
                final_results.append(result)

        return final_results

    async def get_top_streams(
        self,
        game_id: str | None = None,
        limit: int = 25,
    ) -> list[Livestream]:
        """Get top live streams."""
        streams: list[Livestream] = []

        try:
            url = f"{self.BASE_URL_V1}/livestreams"
            params: dict[str, Any] = {"limit": min(limit, 100)}

            if game_id:
                # For category-specific streams
                url = f"{self.BASE_URL_V1}/categories/{game_id}/livestreams"

            async with self.session.get(
                url,
                headers=self._get_headers(),
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for stream_data in data.get("data", data if isinstance(data, list) else []):
                    channel_data = stream_data.get("channel", {})

                    start_time = None
                    time_str = stream_data.get("start_time") or stream_data.get("created_at")
                    if time_str:
                        try:
                            start_time = datetime.fromisoformat(time_str.replace(" ", "T"))
                            if start_time.tzinfo is None:
                                start_time = start_time.replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass

                    game = None
                    categories = stream_data.get("categories", [])
                    if categories:
                        game = categories[0].get("name")

                    channel = Channel(
                        channel_id=channel_data.get("slug", ""),
                        platform=StreamPlatform.KICK,
                        display_name=channel_data.get("user", {}).get("username", ""),
                    )

                    # Handle thumbnail safely (can be None or a dict)
                    thumbnail = stream_data.get("thumbnail")
                    thumbnail_url = thumbnail.get("url") if thumbnail else None

                    streams.append(
                        Livestream(
                            channel=channel,
                            live=True,
                            title=stream_data.get("session_title"),
                            game=game,
                            viewers=stream_data.get("viewer_count", 0),
                            start_time=start_time,
                            thumbnail_url=thumbnail_url,
                            language=stream_data.get("language"),
                            is_mature=stream_data.get("is_mature", False),
                        )
                    )

        except aiohttp.ClientError:
            pass

        return streams

    async def search_channels(self, query: str, limit: int = 25) -> list[Channel]:
        """Search for channels."""
        channels: list[Channel] = []

        try:
            async with self.session.get(
                f"{self.BASE_URL_V1}/search",
                headers=self._get_headers(),
                params={"query": query},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for ch in data.get("channels", [])[:limit]:
                    channels.append(
                        Channel(
                            channel_id=ch.get("slug", ""),
                            platform=StreamPlatform.KICK,
                            display_name=ch.get("user", {}).get("username", ""),
                        )
                    )

        except aiohttp.ClientError:
            pass

        return channels

    async def get_categories(self, query: str = "", limit: int = 25) -> list[dict[str, str]]:
        """Search for categories/games."""
        categories: list[dict[str, str]] = []

        try:
            url = f"{self.BASE_URL_V1}/categories"
            params: dict[str, Any] = {"limit": min(limit, 100)}

            if query:
                params["query"] = query

            async with self.session.get(
                url,
                headers=self._get_headers(),
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for cat in data.get("data", data if isinstance(data, list) else []):
                    categories.append(
                        {
                            "id": str(cat.get("id", "")),
                            "name": cat.get("name", ""),
                            "slug": cat.get("slug", ""),
                        }
                    )

        except aiohttp.ClientError:
            pass

        return categories
