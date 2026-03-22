"""Chaturbate API client.

Uses the bulk room-list API (with session cookies) for efficient monitoring
of many channels. Falls back to individual chatvideocontext requests when
session cookies are unavailable.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import ChaturbateSettings
from .base import BaseApiClient, safe_json

logger = logging.getLogger(__name__)

# Public API (no auth required)
BASE_URL = "https://chaturbate.com"


class ChaturbateApiClient(BaseApiClient):
    """Client for Chaturbate API.

    Primary: bulk room-list endpoint with session cookies (1-2 requests
    for any number of followed channels).
    Fallback: per-channel chatvideocontext endpoint (public, no auth).
    """

    def __init__(self, settings: ChaturbateSettings) -> None:
        super().__init__()
        self.settings = settings

    @property
    def platform(self) -> StreamPlatform:
        return StreamPlatform.CHATURBATE

    @property
    def name(self) -> str:
        return "Chaturbate"

    def _get_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def is_authorized(self) -> bool:
        """Public API — always authorized for reading."""
        return True

    async def authorize(self) -> bool:
        """No auth needed for public data."""
        return True

    async def get_channel_info(self, channel_id: str) -> Channel | None:
        """Get channel info by username."""
        try:
            url = f"{BASE_URL}/api/chatvideocontext/{channel_id}/"
            async with self.session.get(url, headers=self._get_headers()) as resp:
                if resp.status != 200:
                    return None
                data = await safe_json(resp)
                if not data or not isinstance(data, dict):
                    return None
                return Channel(
                    channel_id=channel_id.lower(),
                    platform=StreamPlatform.CHATURBATE,
                    display_name=data.get("broadcaster_username", channel_id),
                )
        except aiohttp.ClientError as e:
            logger.error(f"Chaturbate: Network error getting channel {channel_id}: {e}")
            return None

    async def get_livestream(self, channel: Channel) -> Livestream:
        """Get livestream status for a single channel (per-channel endpoint)."""

        async def do_request() -> Livestream:
            url = f"{BASE_URL}/api/chatvideocontext/{channel.channel_id}/"
            async with self.session.get(url, headers=self._get_headers()) as resp:
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

                data = await safe_json(resp)
                if not data or not isinstance(data, dict):
                    return Livestream(
                        channel=channel,
                        live=False,
                        error_message="Invalid JSON response",
                    )

                room_status = data.get("room_status", "offline")
                is_live = room_status == "public"

                if not is_live:
                    return Livestream(channel=channel, live=False, room_status=room_status)

                viewers = data.get("num_viewers", 0)
                if isinstance(viewers, str):
                    try:
                        viewers = int(viewers)
                    except ValueError:
                        viewers = 0

                title = data.get("room_title", "")

                return Livestream(
                    channel=channel,
                    live=True,
                    title=title,
                    viewers=viewers,
                    start_time=datetime.now(timezone.utc),
                    is_mature=True,
                    room_status="public",
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
        """Get livestream status for multiple channels.

        Uses the bulk room-list API when session cookies are available
        (1-2 requests for all followed channels). Falls back to throttled
        individual requests otherwise.
        """
        if not channels:
            return []

        # Try bulk API first
        try:
            bulk_result = await self._get_livestreams_bulk(channels)
            if bulk_result is not None:
                return bulk_result
        except Exception as e:
            logger.warning(f"Chaturbate: bulk API failed, using individual: {e}")

        # Fallback: individual requests with throttling
        return await self._get_livestreams_individual(channels)

    async def _get_livestreams_bulk(self, channels: list[Channel]) -> list[Livestream] | None:
        """Use room-list/?follow=true bulk API to check all channels.

        Returns None if session cookies are unavailable (triggers fallback).
        """
        cookie_str = self._get_cookie_string()
        if not cookie_str:
            logger.debug("Chaturbate bulk: no cookies available, skipping")
            return None

        # Fetch all online followed rooms (paginated, typically 1-2 requests)
        online_rooms: dict[str, dict] = {}
        offset = 0
        headers = {**self._get_headers(), "Cookie": cookie_str}

        # Log cookie names (not values) for debugging
        cookie_names = [c.split("=")[0] for c in cookie_str.split("; ")]
        logger.debug(f"Chaturbate bulk: using cookies: {cookie_names}")

        try:
            while True:
                url = f"{BASE_URL}/api/ts/roomlist/room-list/?follow=true&limit=90&offset={offset}"
                async with self.session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Chaturbate bulk API: HTTP {resp.status}")
                        return None
                    data = await safe_json(resp)
                    if not data or not isinstance(data, dict):
                        logger.warning("Chaturbate bulk API: invalid response")
                        return None

                    rooms = data.get("rooms", [])
                    total = data.get("total_count", 0)
                    logger.debug(
                        f"Chaturbate bulk page: {len(rooms)} rooms, "
                        f"total_count={total}, offset={offset}"
                    )
                    if not rooms:
                        break

                    if offset == 0:
                        logger.debug(f"Chaturbate room-list sample keys: {list(rooms[0].keys())}")

                    for room in rooms:
                        username = self._extract_username(room)
                        if username:
                            online_rooms[username] = room

                    total = data.get("total_count", 0)
                    offset += 90
                    if offset >= total:
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Chaturbate bulk API error: {e}")
            return None

        # Build results
        results: list[Livestream] = []
        live_indices: list[int] = []
        for channel in channels:
            cid = channel.channel_id.lower()
            room = online_rooms.get(cid)
            if room:
                live_indices.append(len(results))
                results.append(self._room_to_livestream(channel, room))
            else:
                results.append(Livestream(channel=channel, live=False))

        # Check room_status for live channels (small set, fast)
        # to detect private/hidden shows reported as online by bulk API
        if live_indices:
            live_channels = [results[i].channel for i in live_indices]
            statuses = await asyncio.gather(*[self._get_room_status(ch) for ch in live_channels])
            for idx, status in zip(live_indices, statuses):
                results[idx].room_status = status

        live_count = sum(1 for r in results if r.live)
        private_count = sum(
            1 for r in results if r.room_status and r.room_status not in ("public", "offline")
        )
        logger.info(
            f"Chaturbate bulk: {live_count} online"
            f" ({private_count} private/hidden), "
            f"{len(channels) - live_count} offline "
            f"({len(channels)} total)"
        )
        return results

    async def _get_room_status(self, channel: Channel) -> str:
        """Get room_status for a single channel via individual API."""
        try:
            url = f"{BASE_URL}/api/chatvideocontext/{channel.channel_id}/"
            async with self.session.get(
                url, headers=self._get_headers(), timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return "offline"
                data = await safe_json(resp)
                if not data or not isinstance(data, dict):
                    return "offline"
                return data.get("room_status", "offline")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "offline"

    async def _get_livestreams_individual(self, channels: list[Channel]) -> list[Livestream]:
        """Fallback: check channels one at a time with delays."""
        results: list[Livestream] = []
        for i, channel in enumerate(channels):
            try:
                result = await self.get_livestream(channel)
                results.append(result)
            except Exception as e:
                results.append(Livestream(channel=channel, live=False, error_message=str(e)))
            # Throttle to avoid 429 rate limiting
            if i < len(channels) - 1:
                await asyncio.sleep(2.0)
        return results

    def _get_cookie_string(self) -> str:
        """Get Chaturbate session cookies from QWebEngine profile."""
        try:
            from ..gui.chat.chaturbate_web_chat import get_chaturbate_cookie_string

            return get_chaturbate_cookie_string()
        except ImportError:
            return ""

    @staticmethod
    def _extract_username(room: dict) -> str:
        """Extract username from a room-list API response item."""
        val = room.get("username", "")
        return str(val).lower() if val else ""

    @staticmethod
    def _room_to_livestream(channel: Channel, room: dict) -> Livestream:
        """Convert a room-list API item to a Livestream object."""
        viewers = room.get("num_users", 0)
        if isinstance(viewers, str):
            try:
                viewers = int(viewers)
            except ValueError:
                viewers = 0

        title = room.get("room_subject", room.get("subject", ""))

        # Parse start time from the API response
        start_time = datetime.now(timezone.utc)
        start_dt = room.get("start_dt_utc")
        if start_dt:
            try:
                start_time = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                pass

        return Livestream(
            channel=channel,
            live=True,
            title=title,
            viewers=viewers,
            start_time=start_time,
            thumbnail_url=room.get("img", ""),
            is_mature=True,
        )
