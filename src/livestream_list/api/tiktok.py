"""TikTok API client using HTTP scraping."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import TikTokSettings
from .base import BaseApiClient

logger = logging.getLogger(__name__)

# Regex to extract JSON data from TikTok HTML.
# Primary: SIGI_STATE (current TikTok live page format).
# Fallback: __UNIVERSAL_DATA_FOR_REHYDRATION__ (older format).
_SIGI_STATE_RE = re.compile(
    r'<script\s+id="SIGI_STATE"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)
_REHYDRATION_RE = re.compile(
    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s+type="application/json">'
    r"(.*?)</script>",
    re.DOTALL,
)

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TikTokApiClient(BaseApiClient):
    """Client for TikTok using HTTP scraping.

    TikTok has no official public API. Live status is determined by scraping
    the user's live page and parsing embedded JSON (SIGI_STATE or
    __UNIVERSAL_DATA_FOR_REHYDRATION__).
    """

    def __init__(self, settings: TikTokSettings, concurrency: int = 5) -> None:
        super().__init__()
        self.settings = settings
        self.concurrency = concurrency

    @property
    def platform(self) -> StreamPlatform:
        return StreamPlatform.TIKTOK

    @property
    def name(self) -> str:
        return "TikTok"

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def is_authorized(self) -> bool:
        return True

    async def authorize(self) -> bool:
        return True

    def _parse_sigi_state(self, html: str) -> dict | None:
        """Extract and parse the SIGI_STATE JSON (current TikTok format)."""
        match = _SIGI_STATE_RE.search(html)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    def _parse_rehydration_json(self, html: str) -> dict | None:
        """Extract and parse the __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON."""
        match = _REHYDRATION_RE.search(html)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    async def get_channel_info(self, channel_id: str) -> Channel | None:
        """Get channel info by username."""
        # Strip @ prefix if present
        channel_id = channel_id.lstrip("@")
        try:
            url = f"https://www.tiktok.com/@{channel_id}"
            async with self.session.get(url, headers=self._get_headers()) as resp:
                if resp.status != 200:
                    logger.warning(f"TikTok: HTTP {resp.status} for {channel_id}")
                    return None

                html = await resp.text()
                data = self._parse_rehydration_json(html)
                if not data:
                    logger.warning(f"TikTok: No rehydration data for {channel_id}")
                    return None

                # Navigate to user info
                webapp_data = data.get("__DEFAULT_SCOPE__", {})
                user_detail = webapp_data.get("webapp.user-detail", {})
                user_info = user_detail.get("userInfo", {})
                user = user_info.get("user", {})

                if not user:
                    return None

                display_name = user.get("nickname") or user.get("uniqueId") or channel_id

                return Channel(
                    channel_id=user.get("uniqueId", channel_id),
                    platform=StreamPlatform.TIKTOK,
                    display_name=display_name,
                )

        except aiohttp.ClientError as e:
            logger.error(f"TikTok: Network error getting channel {channel_id}: {e}")
            return None

    def _extract_room_data(self, html: str) -> dict:
        """Extract live room data from TikTok HTML.

        Tries SIGI_STATE first (current format), then falls back to
        __UNIVERSAL_DATA_FOR_REHYDRATION__ (older format).
        """
        # Primary: SIGI_STATE → LiveRoom → liveRoomUserInfo → liveRoom
        sigi = self._parse_sigi_state(html)
        if sigi:
            live_room_info = sigi.get("LiveRoom", {}).get("liveRoomUserInfo", {})
            room_data = live_room_info.get("liveRoom", {})
            if room_data:
                return room_data

        # Fallback: rehydration JSON → webapp.live-detail → liveRoomUserInfo → liveRoom
        rehydration = self._parse_rehydration_json(html)
        if rehydration:
            scope = rehydration.get("__DEFAULT_SCOPE__", {})
            live_detail = scope.get("webapp.live-detail", {})
            live_room_info = live_detail.get("liveRoomUserInfo", {})
            room_data = live_room_info.get("liveRoom", {})
            if room_data:
                return room_data

        return {}

    async def get_livestream(self, channel: Channel) -> Livestream:
        """Get livestream status for a channel."""

        async def do_request() -> Livestream:
            url = f"https://www.tiktok.com/@{channel.channel_id}/live"
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

                html = await resp.text()
                room_data = self._extract_room_data(html)

                if not room_data:
                    return Livestream(
                        channel=channel,
                        live=False,
                        error_message="Could not parse TikTok page",
                    )

                # Status: 2 = live, 4 = offline/ended
                status = room_data.get("status", 0)
                is_live = status == 2

                if not is_live:
                    return Livestream(channel=channel, live=False)

                # Parse start time (startTime or createTime, unix timestamp)
                start_time = None
                ts = (
                    room_data.get("startTime")
                    or room_data.get("createTime")
                    or room_data.get("create_time")
                )
                if ts:
                    try:
                        start_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    except (ValueError, OSError):
                        pass

                # Viewer count
                stats = room_data.get("liveRoomStats", {})
                viewers = int(stats.get("userCount", 0))

                # Thumbnail (coverUrl string or cover dict with url_list)
                thumbnail_url = None
                cover_url = room_data.get("coverUrl")
                if isinstance(cover_url, str) and cover_url:
                    thumbnail_url = cover_url
                else:
                    cover = room_data.get("cover")
                    if isinstance(cover, dict):
                        url_list = cover.get("url_list", [])
                        if url_list:
                            thumbnail_url = url_list[0]
                    elif isinstance(cover, str) and cover:
                        thumbnail_url = cover

                return Livestream(
                    channel=channel,
                    live=True,
                    title=room_data.get("title") or None,
                    viewers=viewers,
                    start_time=start_time,
                    thumbnail_url=thumbnail_url,
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

        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_with_semaphore(channel: Channel) -> Livestream:
            async with semaphore:
                return await self.get_livestream(channel)

        tasks = [fetch_with_semaphore(channel) for channel in channels]
        results = await asyncio.gather(*tasks, return_exceptions=True)

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
