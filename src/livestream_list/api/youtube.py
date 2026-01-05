"""YouTube client using yt-dlp for stream detection."""

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Optional

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import YouTubeSettings
from .base import BaseApiClient

logger = logging.getLogger(__name__)


class YouTubeApiClient(BaseApiClient):
    """Client for YouTube using yt-dlp for stream detection.

    Uses yt-dlp (no API key required) to:
    - Resolve channel handles to channel IDs
    - Detect live streams
    - Get stream metadata (title, viewers, etc.)
    """

    def __init__(self, settings: YouTubeSettings) -> None:
        super().__init__()
        self.settings = settings
        self._ytdlp_path: Optional[str] = None
        self._check_ytdlp()

    def _check_ytdlp(self) -> None:
        """Check if yt-dlp is available."""
        self._ytdlp_path = shutil.which("yt-dlp")
        if not self._ytdlp_path:
            logger.warning("yt-dlp not found - YouTube stream detection will not work")

    @property
    def platform(self) -> StreamPlatform:
        return StreamPlatform.YOUTUBE

    @property
    def name(self) -> str:
        return "YouTube"

    async def is_authorized(self) -> bool:
        """Check if yt-dlp is available."""
        return self._ytdlp_path is not None

    async def authorize(self) -> bool:
        """No authorization needed - yt-dlp works without API key."""
        return self._ytdlp_path is not None

    def _run_ytdlp(self, url: str, extra_args: list[str] = None) -> Optional[dict]:
        """Run yt-dlp and return JSON output."""
        if not self._ytdlp_path:
            return None

        args = [
            self._ytdlp_path,
            "--dump-json",
            "--no-download",
            "--no-warnings",
            "--ignore-errors",
        ]
        if extra_args:
            args.extend(extra_args)
        args.append(url)

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                # yt-dlp may output multiple JSON objects for playlists
                # Take the first one
                first_line = result.stdout.strip().split('\n')[0]
                return json.loads(first_line)
        except subprocess.TimeoutExpired:
            logger.warning(f"yt-dlp timed out for {url}")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse yt-dlp output for {url}: {e}")
        except Exception as e:
            logger.warning(f"yt-dlp error for {url}: {e}")

        return None

    async def _run_ytdlp_async(self, url: str, extra_args: list[str] = None) -> Optional[dict]:
        """Run yt-dlp asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_ytdlp, url, extra_args)

    async def _get_last_video_date(self, channel: Channel) -> Optional[datetime]:
        """Get the upload date of the most recent video on the channel."""
        channel_id = channel.channel_id
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/videos"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}/videos"
        else:
            url = f"https://www.youtube.com/@{channel_id}/videos"

        try:
            # Get just the first (most recent) video with full metadata
            data = await self._run_ytdlp_async(url, ["--playlist-items", "1"])
            if not data:
                return None

            # Try to get upload date from various fields
            upload_date = data.get("upload_date")  # Format: YYYYMMDD
            timestamp = data.get("timestamp")
            release_timestamp = data.get("release_timestamp")

            if timestamp:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            elif release_timestamp:
                return datetime.fromtimestamp(release_timestamp, tz=timezone.utc)
            elif upload_date:
                # Parse YYYYMMDD format
                return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)

        except Exception as e:
            logger.debug(f"Could not get last video date for {channel.display_name}: {e}")

        return None

    async def get_channel_info(self, channel_id: str) -> Optional[Channel]:
        """
        Get channel info using yt-dlp.
        channel_id can be a channel ID (UC...), username, or handle (@...).
        """
        if not self._ytdlp_path:
            # Fallback: just create a channel with the given ID
            # The actual name will be resolved when we check stream status
            display_name = channel_id.lstrip("@")
            return Channel(
                channel_id=channel_id,
                platform=StreamPlatform.YOUTUBE,
                display_name=display_name,
            )

        # Build the channel URL
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}"
        else:
            url = f"https://www.youtube.com/@{channel_id}"

        # Try to get channel info by fetching the live tab
        # Using --playlist-items 0 to not actually fetch videos
        data = await self._run_ytdlp_async(
            f"{url}/live",
            ["--playlist-items", "0"]
        )

        if data:
            return Channel(
                channel_id=data.get("channel_id", channel_id),
                platform=StreamPlatform.YOUTUBE,
                display_name=data.get("channel", data.get("uploader", channel_id.lstrip("@"))),
            )

        # Fallback: return basic channel
        return Channel(
            channel_id=channel_id,
            platform=StreamPlatform.YOUTUBE,
            display_name=channel_id.lstrip("@"),
        )

    async def get_livestream(self, channel: Channel) -> Livestream:
        """Get livestream status for a channel using yt-dlp."""
        if not self._ytdlp_path:
            return Livestream(
                channel=channel,
                live=False,
                error_message="yt-dlp not installed",
            )

        # Build the channel live URL
        channel_id = channel.channel_id
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/live"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}/live"
        else:
            url = f"https://www.youtube.com/@{channel_id}/live"

        try:
            data = await self._run_ytdlp_async(url)

            if not data:
                return Livestream(channel=channel, live=False)

            # Check if it's actually live
            is_live = data.get("is_live", False)

            if not is_live:
                # Update channel display name if we got it
                if data.get("channel") or data.get("uploader"):
                    channel.display_name = data.get("channel", data.get("uploader"))

                # Try to get last stream/video date
                last_live_time = await self._get_last_video_date(channel)
                return Livestream(channel=channel, live=False, last_live_time=last_live_time)

            # Parse start time
            start_time = None
            release_timestamp = data.get("release_timestamp")
            if release_timestamp:
                try:
                    start_time = datetime.fromtimestamp(release_timestamp, tz=timezone.utc)
                except (ValueError, OSError):
                    pass

            # Get viewer count
            viewers = data.get("concurrent_view_count", 0) or 0

            # Update channel display name
            display_name = data.get("channel", data.get("uploader", channel.display_name))
            channel.display_name = display_name

            return Livestream(
                channel=channel,
                live=True,
                title=data.get("title", ""),
                game=data.get("categories", [""])[0] if data.get("categories") else None,
                viewers=viewers,
                start_time=start_time,
                thumbnail_url=data.get("thumbnail"),
                language=data.get("language"),
                video_id=data.get("id"),  # YouTube video ID for live chat
            )

        except Exception as e:
            logger.error(f"Error checking YouTube stream for {channel.display_name}: {e}")
            return Livestream(
                channel=channel,
                live=False,
                error_message=str(e),
            )

    async def get_livestreams(self, channels: list[Channel]) -> list[Livestream]:
        """Get livestream status for multiple channels."""
        # Process channels concurrently but limit parallelism
        results: list[Livestream] = []

        # Process in batches of 5 to avoid overwhelming the system
        batch_size = 5
        for i in range(0, len(channels), batch_size):
            batch = channels[i:i + batch_size]
            tasks = [self.get_livestream(channel) for channel in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    results.append(Livestream(
                        channel=batch[j],
                        live=False,
                        error_message=str(result),
                    ))
                else:
                    results.append(result)

        return results

    async def search_channels(self, query: str, limit: int = 25) -> list[Channel]:
        """Search for channels - not supported without API key."""
        # yt-dlp doesn't support channel search
        # Users must add channels by URL or handle
        return []
