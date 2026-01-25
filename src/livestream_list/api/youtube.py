"""YouTube client using HTML scraping for stream detection (yt-dlp fallback)."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import YouTubeSettings
from .base import BaseApiClient, safe_json

logger = logging.getLogger(__name__)

# Regex patterns to extract YouTube page data
# ytInitialPlayerResponse contains videoDetails with isLive status
PLAYER_RESPONSE_RE = re.compile(
    r"var ytInitialPlayerResponse\s*=\s*(\{.+?\});", re.DOTALL
)
# ytInitialData contains page structure (used as fallback)
INITIAL_DATA_RE = re.compile(r"var ytInitialData\s*=\s*({.+?});</script>", re.DOTALL)


class YouTubeApiClient(BaseApiClient):
    """Client for YouTube using HTML scraping for stream detection.

    Primary method: Scrapes the channel's /live page and parses ytInitialData JSON.
    This is fast (single HTTP request) and lightweight (no subprocess).

    Fallback: Uses yt-dlp subprocess if scraping fails and use_ytdlp_fallback is enabled.

    No API key required for either method.
    """

    # HTTP headers for scraping YouTube pages
    SCRAPE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    def __init__(self, settings: YouTubeSettings, concurrency: int = 10) -> None:
        super().__init__()
        self.settings = settings
        self.concurrency = concurrency
        self._ytdlp_path: str | None = None
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

    def _run_ytdlp(self, url: str, extra_args: list[str] = None) -> dict | None:
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
                first_line = result.stdout.strip().split("\n")[0]
                return json.loads(first_line)
        except subprocess.TimeoutExpired:
            logger.warning(f"yt-dlp timed out for {url}")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse yt-dlp output for {url}: {e}")
        except Exception as e:
            logger.warning(f"yt-dlp error for {url}: {e}")

        return None

    async def _run_ytdlp_async(self, url: str, extra_args: list[str] = None) -> dict | None:
        """Run yt-dlp asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_ytdlp, url, extra_args)

    # -------------------------------------------------------------------------
    # HTML Scraping Methods (Primary - fast and lightweight)
    # -------------------------------------------------------------------------

    def _build_channel_live_url(self, channel_id: str) -> str:
        """Build the /live URL for a channel."""
        if channel_id.startswith("UC"):
            return f"https://www.youtube.com/channel/{channel_id}/live"
        elif channel_id.startswith("@"):
            return f"https://www.youtube.com/{channel_id}/live"
        else:
            return f"https://www.youtube.com/@{channel_id}/live"

    async def _fetch_live_page(self, channel_id: str) -> str | None:
        """Fetch the channel's /live page HTML."""
        url = self._build_channel_live_url(channel_id)
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with self.session.get(
                url, headers=self.SCRAPE_HEADERS, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                else:
                    logger.debug(f"YouTube /live page returned {resp.status} for {channel_id}")
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching YouTube /live page for {channel_id}")
        except aiohttp.ClientError as e:
            logger.debug(f"Error fetching YouTube /live page for {channel_id}: {e}")
        return None

    def _parse_player_response(self, html: str) -> dict | None:
        """Extract ytInitialPlayerResponse JSON from page HTML.

        This contains videoDetails with isLive status.
        """
        # Find the start of ytInitialPlayerResponse
        marker = "var ytInitialPlayerResponse = "
        start_idx = html.find(marker)
        if start_idx == -1:
            return None

        start_idx += len(marker)

        # Find the matching closing brace by counting braces
        brace_count = 0
        in_string = False
        escape_next = False
        end_idx = start_idx

        for i, char in enumerate(html[start_idx:], start_idx):
            if escape_next:
                escape_next = False
                continue

            if char == "\\":
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break

        if brace_count != 0:
            return None

        try:
            json_str = html[start_idx:end_idx]
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse ytInitialPlayerResponse: {e}")
            return None

    def _parse_initial_data(self, html: str) -> dict | None:
        """Extract ytInitialData JSON from page HTML (fallback)."""
        match = INITIAL_DATA_RE.search(html)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _extract_livestream_from_data(self, data: dict, channel: Channel) -> Livestream | None:
        """Parse ytInitialData into a Livestream object.

        Returns None if the data doesn't contain valid livestream info.
        """
        # Try to get videoDetails (available on /live pages that redirect to a stream)
        video_details = data.get("videoDetails", {})

        # Check if this is actually a live stream
        is_live = video_details.get("isLive", False)
        is_live_content = video_details.get("isLiveContent", False)

        # Update channel display name from video details
        author = video_details.get("author")
        if author:
            channel.display_name = author

        if not (is_live and is_live_content):
            # Not live - return offline Livestream
            return Livestream(channel=channel, live=False)

        # Extract start time from microformat
        start_time = None
        try:
            microformat = data.get("microformat", {})
            player_microformat = microformat.get("playerMicroformatRenderer", {})
            broadcast_details = player_microformat.get("liveBroadcastDetails", {})
            start_timestamp = broadcast_details.get("startTimestamp")
            if start_timestamp:
                # Parse ISO format like "2024-01-15T12:30:00+00:00"
                start_time = datetime.fromisoformat(start_timestamp.replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            pass

        # Get viewer count
        viewers = 0
        view_count_str = video_details.get("viewCount", "0")
        try:
            viewers = int(view_count_str)
        except (ValueError, TypeError):
            pass

        # Get title
        title = video_details.get("title", "")

        # Get video ID (needed for live chat)
        video_id = video_details.get("videoId")

        # Get thumbnail
        thumbnail_url = None
        thumbnails = video_details.get("thumbnail", {}).get("thumbnails", [])
        if thumbnails:
            # Get the highest quality thumbnail
            thumbnail_url = thumbnails[-1].get("url")

        return Livestream(
            channel=channel,
            live=True,
            title=title,
            viewers=viewers,
            start_time=start_time,
            video_id=video_id,
            thumbnail_url=thumbnail_url,
            # Note: game/category not directly available in videoDetails
        )

    def _check_live_indicators(self, html: str) -> bool:
        """Quick check for live stream indicators in HTML.

        This is a fast fallback when JSON parsing fails.
        """
        # Check for live thumbnail indicator
        if "hqdefault_live.jpg" in html:
            return True
        # Check for schema.org live broadcast marker
        if '"isLiveBroadcast" content="True"' in html:
            return True
        if '"isLiveBroadcast":true' in html.lower():
            return True
        return False

    async def _get_livestream_scrape(self, channel: Channel) -> Livestream | None:
        """Get livestream status using HTML scraping.

        Returns Livestream if successful, None if scraping failed.
        """
        try:
            html = await self._fetch_live_page(channel.channel_id)
            if not html:
                return None

            # Try ytInitialPlayerResponse first (contains videoDetails with isLive)
            data = self._parse_player_response(html)
            if data:
                return self._extract_livestream_from_data(data, channel)

            # Fallback to ytInitialData
            data = self._parse_initial_data(html)
            if data:
                return self._extract_livestream_from_data(data, channel)

            # Last resort: check for live indicators in HTML
            # This gives us live/not-live but no metadata
            if self._check_live_indicators(html):
                logger.debug(f"Detected live via HTML indicators for {channel.display_name}")
                return Livestream(
                    channel=channel,
                    live=True,
                    title="",  # No metadata available
                )

            # No live indicators found - channel is offline
            return Livestream(channel=channel, live=False)

        except Exception as e:
            logger.debug(f"HTML scraping error for {channel.display_name}: {e}")
            return None

    # -------------------------------------------------------------------------
    # yt-dlp Methods (Fallback - slower but more robust)
    # -------------------------------------------------------------------------

    async def _get_livestream_ytdlp(self, channel: Channel) -> Livestream:
        """Get livestream status using yt-dlp subprocess (fallback method)."""
        if not self._ytdlp_path:
            return Livestream(
                channel=channel,
                live=False,
                error_message="yt-dlp not installed",
            )

        url = self._build_channel_live_url(channel.channel_id)

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
                return Livestream(channel=channel, live=False)

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
                video_id=data.get("id"),
            )

        except Exception as e:
            logger.error(f"yt-dlp error for {channel.display_name}: {e}")
            return Livestream(
                channel=channel,
                live=False,
                error_message=str(e),
            )

    async def _get_last_video_date(self, channel: Channel) -> datetime | None:
        """Get the date of the most recent livestream on the channel.

        Checks the /streams tab to get the last livestream specifically,
        rather than regular video uploads.
        """
        channel_id = channel.channel_id
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/streams"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}/streams"
        else:
            url = f"https://www.youtube.com/@{channel_id}/streams"

        try:
            # Get just the first (most recent) stream with full metadata
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
            logger.debug(f"Could not get last stream date for {channel.display_name}: {e}")

        return None

    async def has_livestream_capability(self, channel: Channel) -> bool:
        """Check if a channel has livestream capability by probing the /streams tab.

        Returns True if the channel has past livestreams in their streams tab.
        This is useful for filtering channels that actually do livestreams.
        """
        if not self._ytdlp_path:
            return True  # Can't check without yt-dlp, assume yes

        channel_id = channel.channel_id
        if channel_id.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_id}/streams"
        elif channel_id.startswith("@"):
            url = f"https://www.youtube.com/{channel_id}/streams"
        else:
            url = f"https://www.youtube.com/@{channel_id}/streams"

        # Check the streams tab - if it has any entries, the channel does livestreams
        # Use --flat-playlist to just get metadata without downloading
        data = await self._run_ytdlp_async(url, ["--flat-playlist", "--playlist-items", "1"])
        return data is not None

    async def filter_channels_by_livestream(
        self,
        channels: list[Channel],
        progress_callback: callable = None,
    ) -> list[Channel]:
        """Filter a list of channels to only those that do livestreams.

        Args:
            channels: List of channels to filter
            progress_callback: Optional callback(checked, total, channel_name) for progress

        Returns:
            List of channels that have livestream capability
        """
        if not channels:
            return []

        # Use semaphore to limit concurrent checks (same as get_livestreams)
        semaphore = asyncio.Semaphore(4)
        results: list[tuple[int, Channel, bool]] = []

        async def check_channel(idx: int, channel: Channel) -> tuple[int, Channel, bool]:
            async with semaphore:
                has_live = await self.has_livestream_capability(channel)
                if progress_callback:
                    progress_callback(idx + 1, len(channels), channel.display_name)
                return (idx, channel, has_live)

        tasks = [check_channel(i, ch) for i, ch in enumerate(channels)]
        results = await asyncio.gather(*tasks)

        # Return channels that have livestream capability, preserving order
        results.sort(key=lambda x: x[0])
        return [ch for _, ch, has_live in results if has_live]

    async def get_channel_info(self, channel_id: str) -> Channel | None:
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
        data = await self._run_ytdlp_async(f"{url}/live", ["--playlist-items", "0"])

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
        """Get livestream status for a channel.

        Primary: Uses fast HTML scraping of the /live page.
        Fallback: Uses yt-dlp subprocess if scraping fails and fallback is enabled.
        """
        # Primary method: Fast HTML scraping
        result = await self._get_livestream_scrape(channel)
        if result is not None:
            return result

        # Fallback: yt-dlp subprocess (if enabled and available)
        if self._ytdlp_path and self.settings.use_ytdlp_fallback:
            logger.debug(f"Falling back to yt-dlp for {channel.display_name}")
            return await self._get_livestream_ytdlp(channel)

        # Neither method worked
        return Livestream(channel=channel, live=False)

    async def get_livestreams(self, channels: list[Channel]) -> list[Livestream]:
        """Get livestream status for multiple channels.

        Uses fast HTML scraping for initial status check, then fetches full
        metadata via yt-dlp for live channels (to get accurate start_time).
        """
        if not channels:
            return []

        # Use semaphore to limit concurrent HTTP requests
        # HTML scraping is I/O-bound, so higher concurrency is fine
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

        # Second pass: fetch full metadata for live channels via yt-dlp
        # This gets accurate start_time which HTML scraping may miss
        if self._ytdlp_path:
            live_indices = [i for i, ls in enumerate(final_results) if ls.live]
            if live_indices:
                logger.debug(f"Fetching full metadata for {len(live_indices)} live YT channels")
                ytdlp_semaphore = asyncio.Semaphore(4)  # Lower concurrency for subprocesses

                async def fetch_ytdlp(idx: int) -> tuple[int, Livestream]:
                    async with ytdlp_semaphore:
                        ls = final_results[idx]
                        full_ls = await self._get_livestream_ytdlp(ls.channel)
                        # Only use yt-dlp result if it's live and has start_time
                        if full_ls.live and full_ls.start_time:
                            return (idx, full_ls)
                        return (idx, ls)  # Keep original if yt-dlp failed

                ytdlp_tasks = [fetch_ytdlp(idx) for idx in live_indices]
                ytdlp_results = await asyncio.gather(*ytdlp_tasks, return_exceptions=True)

                for result in ytdlp_results:
                    if isinstance(result, tuple):
                        idx, ls = result
                        final_results[idx] = ls

        return final_results

    async def search_channels(self, query: str, limit: int = 25) -> list[Channel]:
        """Search for channels - not supported without API key."""
        # yt-dlp doesn't support channel search
        # Users must add channels by URL or handle
        return []

    @staticmethod
    def _parse_cookie_string(cookie_str: str) -> dict[str, str]:
        """Parse a cookie string into a dict."""
        cookies: dict[str, str] = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                name, _, value = part.partition("=")
                name = name.strip()
                if name:
                    cookies[name] = value.strip()
        return cookies

    @staticmethod
    def _generate_sapisidhash(sapisid: str) -> str:
        """Generate SAPISIDHASH authorization header value."""
        timestamp = int(time.time())
        origin = "https://www.youtube.com"
        hash_input = f"{timestamp} {sapisid} {origin}"
        hash_value = hashlib.sha1(hash_input.encode()).hexdigest()
        return f"SAPISIDHASH {timestamp}_{hash_value}"

    async def get_subscriptions(self, cookies: str) -> list[Channel]:
        """Get YouTube subscriptions using InnerTube API with cookie auth.

        Requires valid YouTube cookies (at minimum SAPISID, SID, HSID, SSID).
        Returns a list of Channel objects for subscribed channels.
        """
        cookie_dict = self._parse_cookie_string(cookies)
        sapisid = cookie_dict.get("SAPISID", "")
        if not sapisid:
            raise ValueError("SAPISID cookie not found - cannot authenticate")

        auth_header = self._generate_sapisidhash(sapisid)
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

        headers = {
            "Authorization": auth_header,
            "Cookie": cookie_header,
            "Content-Type": "application/json",
            "Origin": "https://www.youtube.com",
            "Referer": "https://www.youtube.com/",
            "X-Youtube-Client-Name": "1",
            "X-Youtube-Client-Version": "2.20250120.01.00",
            "X-Goog-AuthUser": "0",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        innertube_body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20250120.01.00",
                    "hl": "en",
                }
            },
            "browseId": "FEchannels",
        }

        channels: list[Channel] = []
        url = "https://www.youtube.com/youtubei/v1/browse"

        async with aiohttp.ClientSession() as session:
            while True:
                async with session.post(url, json=innertube_body, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise ValueError(f"YouTube API returned {resp.status}: {text[:200]}")
                    data = await safe_json(resp)
                    if data is None:
                        raise ValueError("YouTube API returned invalid JSON")

                # Check if authentication succeeded
                if not channels:  # Only check on first page
                    if not self._is_logged_in(data):
                        raise ValueError(
                            "YouTube cookies expired or invalid. "
                            "Please re-import cookies from your browser."
                        )

                # Parse channels from response
                new_channels = self._parse_subscriptions_response(data)
                channels.extend(new_channels)

                # Check for continuation
                continuation = self._get_continuation_token(data)
                if not continuation:
                    break

                # Set up continuation request
                innertube_body = {
                    "context": {
                        "client": {
                            "clientName": "WEB",
                            "clientVersion": "2.20250120.01.00",
                            "hl": "en",
                        }
                    },
                    "continuation": continuation,
                }

        logger.info(f"Found {len(channels)} YouTube subscriptions")
        return channels

    def _parse_subscriptions_response(self, data: dict) -> list[Channel]:
        """Parse channel data from InnerTube browse response."""
        channels: list[Channel] = []

        # Navigate the nested response structure
        # Initial response has tabs -> tabRenderer -> content -> sectionListRenderer
        # Continuation has onResponseReceivedActions -> appendContinuationItemsAction
        items = []

        # Try initial response structure
        try:
            browse = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})
            tabs = browse.get("tabs", [])
            for tab in tabs:
                tab_content = tab.get("tabRenderer", {}).get("content", {})
                section_list = tab_content.get("sectionListRenderer", {})
                for section in section_list.get("contents", []):
                    shelf = section.get("itemSectionRenderer", {})
                    for item in shelf.get("contents", []):
                        grid = (
                            item.get("shelfRenderer", {})
                            .get("content", {})
                            .get("expandedShelfContentsRenderer", {})
                        )
                        items.extend(grid.get("items", []))
                        # Also try gridRenderer
                        grid2 = item.get("gridRenderer", {})
                        items.extend(grid2.get("items", []))
        except (AttributeError, TypeError):
            pass

        # Try continuation response structure
        try:
            for action in data.get("onResponseReceivedActions", []):
                continuation_items = action.get("appendContinuationItemsAction", {}).get(
                    "continuationItems", []
                )
                items.extend(continuation_items)
        except (AttributeError, TypeError):
            pass

        # Extract channel info from items
        for item in items:
            renderer = item.get("channelRenderer", {})
            if not renderer:
                # Try gridChannelRenderer
                renderer = item.get("gridChannelRenderer", {})
            if not renderer:
                continue

            channel_id = renderer.get("channelId", "")
            if not channel_id:
                # Try to extract from navigationEndpoint
                nav = renderer.get("navigationEndpoint", {})
                channel_id = nav.get("browseEndpoint", {}).get("browseId", "")

            if not channel_id:
                continue

            # Get display name from title
            title = renderer.get("title", {})
            if isinstance(title, dict):
                runs = title.get("runs", [{}])
                display_name = title.get("simpleText", "") or runs[0].get("text", "")
            else:
                display_name = str(title)

            if not display_name:
                display_name = channel_id

            channels.append(
                Channel(
                    channel_id=channel_id,
                    platform=StreamPlatform.YOUTUBE,
                    display_name=display_name,
                )
            )

        return channels

    @staticmethod
    def _get_continuation_token(data: dict) -> str | None:
        """Extract continuation token from response for pagination."""
        # Check in main content
        try:
            browse = data.get("contents", {}).get("twoColumnBrowseResultsRenderer", {})
            tabs = browse.get("tabs", [])
            for tab in tabs:
                tab_content = tab.get("tabRenderer", {}).get("content", {})
                section_list = tab_content.get("sectionListRenderer", {})
                for section in section_list.get("contents", []):
                    cont = section.get("continuationItemRenderer", {})
                    token = (
                        cont.get("continuationEndpoint", {})
                        .get("continuationCommand", {})
                        .get("token")
                    )
                    if token:
                        return token
                # Also check continuations at section list level
                for cont in section_list.get("continuations", []):
                    token = cont.get("nextContinuationData", {}).get("continuation")
                    if token:
                        return token
        except (AttributeError, TypeError):
            pass

        # Check in continuation response
        try:
            for action in data.get("onResponseReceivedActions", []):
                cont_items = action.get("appendContinuationItemsAction", {}).get(
                    "continuationItems", []
                )
                for item in cont_items:
                    cont = item.get("continuationItemRenderer", {})
                    token = (
                        cont.get("continuationEndpoint", {})
                        .get("continuationCommand", {})
                        .get("token")
                    )
                    if token:
                        return token
        except (AttributeError, TypeError):
            pass

        return None

    @staticmethod
    def _is_logged_in(data: dict) -> bool:
        """Check if the InnerTube response indicates authenticated access."""
        try:
            for stp in data.get("responseContext", {}).get("serviceTrackingParams", []):
                if stp.get("service") == "GUIDED_HELP":
                    for p in stp.get("params", []):
                        if p.get("key") == "logged_in":
                            return p.get("value") == "1"
        except (AttributeError, TypeError):
            pass
        # If we can't determine, check for content presence
        return "contents" in data
