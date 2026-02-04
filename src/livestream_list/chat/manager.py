"""Chat manager - orchestrates connections, emote loading, and badge fetching."""

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..core.models import Livestream, StreamPlatform
from ..core.settings import Settings
from .connections.base import BaseChatConnection
from .emotes.cache import DOWNLOAD_PRIORITY_HIGH, DOWNLOAD_PRIORITY_LOW, EmoteCache
from .emotes.image import GifTimer, ImageExpirationPool, ImageSet, ImageSpec
from .emotes.matcher import find_third_party_emotes
from .emotes.provider import BTTVProvider, FFZProvider, SevenTVProvider, TwitchProvider
from .models import ChatBadge, ChatEmote, ChatMessage, ChatUser

logger = logging.getLogger(__name__)

# Fallback Twitch client ID (same as api.twitch.DEFAULT_CLIENT_ID)
_DEFAULT_TWITCH_CLIENT_ID = "gnvljs5w28wkpz60vfug0z5rp5d66h"
GLOBAL_EMOTE_TTL = 24 * 60 * 60  # 24 hours
USER_EMOTE_TTL = 30 * 60  # 30 minutes
CHANNEL_EMOTE_TTL = 6 * 60 * 60  # 6 hours
PREFETCH_CONCURRENCY = 1
MAX_RECENT_CHANNELS = 30
MESSAGE_FLUSH_INTERVAL_MS = 50
MAX_MESSAGES_PER_FLUSH = 200
MAX_PENDING_MESSAGES = 5000


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
        """Fetch channel emotes from all providers."""
        all_emotes: list[ChatEmote] = []

        # Fetch native platform emotes first
        if self.platform == "twitch":
            twitch_provider = TwitchProvider(
                oauth_token=self.oauth_token,
                client_id=self.client_id,
            )
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


class SocialsFetchWorker(QThread):
    """Worker thread that fetches channel social links from platform APIs."""

    socials_fetched = Signal(str, dict)  # channel_key, {platform: url}

    def __init__(
        self, channel_key: str, channel_id: str, platform: StreamPlatform, parent=None
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.channel_id = channel_id
        self.platform = platform

    def run(self):
        """Fetch social links based on platform."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if self.platform == StreamPlatform.TWITCH:
                socials = loop.run_until_complete(self._fetch_twitch_socials())
            elif self.platform == StreamPlatform.YOUTUBE:
                socials = loop.run_until_complete(self._fetch_youtube_socials())
            elif self.platform == StreamPlatform.KICK:
                socials = loop.run_until_complete(self._fetch_kick_socials())
            else:
                socials = {}

            if socials:
                self.socials_fetched.emit(self.channel_key, socials)
        except Exception as e:
            logger.debug(f"Failed to fetch socials for {self.channel_id}: {e}")
        finally:
            loop.close()

    async def _fetch_twitch_socials(self) -> dict[str, str]:
        """Fetch socials from Twitch GQL channel socialMedias."""
        import aiohttp

        socials: dict[str, str] = {}

        gql_url = "https://gql.twitch.tv/gql"
        headers = {
            "Client-Id": "kimne78kx3ncx6brgo4mv6wki5h1ko",
            "Content-Type": "application/json",
        }

        query = {
            "query": """
            query UserSocials($login: String!) {
                user(login: $login) {
                    channel {
                        socialMedias {
                            name
                            url
                        }
                    }
                }
            }
            """,
            "variables": {"login": self.channel_id},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    gql_url,
                    json=query,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return socials

                    data = await resp.json()
                    user = data.get("data", {}).get("user")
                    if not user:
                        return socials

                    channel = user.get("channel", {})
                    social_medias = channel.get("socialMedias", []) if channel else []

                    for social in social_medias:
                        name = (social.get("name") or "").lower()
                        url = social.get("url") or ""
                        if url:
                            standard_name = self._normalize_social_name(name)
                            if standard_name not in socials:
                                socials[standard_name] = url

        except Exception as e:
            logger.debug(f"Twitch GQL socials query failed: {e}")

        return socials

    async def _fetch_youtube_socials(self) -> dict[str, str]:
        """Fetch socials from YouTube channel about page."""
        import json
        from urllib.parse import unquote

        import aiohttp

        socials: dict[str, str] = {}

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        # YouTube channel ID might be @handle or UCxxxx format
        # For UC IDs, we need to use /channel/UC.../about format
        channel_path = self.channel_id
        if channel_path.startswith("UC"):
            url = f"https://www.youtube.com/channel/{channel_path}/about"
        elif channel_path.startswith("@"):
            url = f"https://www.youtube.com/{channel_path}/about"
        else:
            url = f"https://www.youtube.com/@{channel_path}/about"

        logger.debug(f"Fetching YouTube socials from: {url}")

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    logger.debug(f"YouTube about page status: {resp.status}")
                    if resp.status != 200:
                        return socials

                    html = await resp.text()
                    logger.debug(f"YouTube HTML length: {len(html)}")

                    # Extract ytInitialData JSON
                    match = re.search(
                        r"var ytInitialData\s*=\s*({.+?});</script>", html, re.DOTALL
                    )
                    if not match:
                        logger.warning("Could not find ytInitialData in YouTube page")
                        return socials

                    data = json.loads(match.group(1))
                    logger.debug(f"ytInitialData keys: {list(data.keys())}")

                    # Navigate to about channel links
                    endpoints = data.get("onResponseReceivedEndpoints", [])
                    logger.debug(f"YouTube onResponseReceivedEndpoints count: {len(endpoints)}")

                    # If no endpoints, try alternative path via tabs
                    if not endpoints:
                        # Try contents.twoColumnBrowseResultsRenderer.tabs
                        tabs = (
                            data.get("contents", {})
                            .get("twoColumnBrowseResultsRenderer", {})
                            .get("tabs", [])
                        )
                        logger.debug(f"Trying tabs path, found {len(tabs)} tabs")
                        for tab in tabs:
                            tab_content = (
                                tab.get("tabRenderer", {})
                                .get("content", {})
                                .get("sectionListRenderer", {})
                                .get("contents", [])
                            )
                            for section in tab_content:
                                about = (
                                    section.get("itemSectionRenderer", {})
                                    .get("contents", [{}])[0]
                                    .get("channelAboutFullMetadataRenderer", {})
                                )
                                if about:
                                    logger.debug("Found channelAboutFullMetadataRenderer")
                                    links = about.get("primaryLinks", [])
                                    for link in links:
                                        title = (
                                            link.get("title", {}).get("simpleText", "")
                                            or link.get("title", {}).get("runs", [{}])[0].get(
                                                "text", ""
                                            )
                                        )
                                        nav = link.get("navigationEndpoint", {})
                                        url_ep = nav.get("urlEndpoint", {})
                                        redirect_url = url_ep.get("url", "")
                                        if redirect_url and "q=" in redirect_url:
                                            actual_url = unquote(redirect_url.split("q=")[-1])
                                            name = self._detect_social_from_url(actual_url)
                                            if not name:
                                                name = self._detect_social_from_title(title)
                                            if name and name not in socials:
                                                socials[name] = actual_url
                                                logger.debug(f"Found social (tabs): {name}")

                    for endpoint in endpoints:
                        panel = (
                            endpoint.get("showEngagementPanelEndpoint", {})
                            .get("engagementPanel", {})
                            .get("engagementPanelSectionListRenderer", {})
                            .get("content", {})
                            .get("sectionListRenderer", {})
                            .get("contents", [])
                        )
                        logger.debug(f"YouTube panel contents count: {len(panel)}")
                        for section in panel:
                            about = (
                                section.get("itemSectionRenderer", {})
                                .get("contents", [{}])[0]
                                .get("aboutChannelRenderer", {})
                                .get("metadata", {})
                                .get("aboutChannelViewModel", {})
                            )
                            links = about.get("links", [])
                            if about:
                                logger.debug(f"Found aboutChannelViewModel with {len(links)} links")
                            for link in links:
                                link_vm = link.get("channelExternalLinkViewModel", {})
                                title = link_vm.get("title", {}).get("content", "")
                                link_data = link_vm.get("link", {})
                                display_url = link_data.get("content", "")

                                # Get actual URL from redirect
                                actual_url = ""
                                runs = link_data.get("commandRuns", [])
                                for run in runs:
                                    innertube = (
                                        run.get("onTap", {}).get("innertubeCommand", {})
                                    )
                                    web_cmd = innertube.get("commandMetadata", {}).get(
                                        "webCommandMetadata", {}
                                    )
                                    redirect_url = web_cmd.get("url", "")
                                    if redirect_url:
                                        # Parse URL to extract q= parameter
                                        from urllib.parse import parse_qs, urlparse

                                        parsed = urlparse(redirect_url)
                                        qs = parse_qs(parsed.query)
                                        if "q" in qs:
                                            # External link with redirect
                                            actual_url = qs["q"][0]
                                        elif parsed.path and not parsed.path.startswith(
                                            "/redirect"
                                        ):
                                            # Internal YouTube link (direct URL)
                                            actual_url = redirect_url
                                        break

                                # Use actual URL if available, else construct from display
                                final_url = actual_url
                                if not final_url and display_url:
                                    if not display_url.startswith("http"):
                                        final_url = f"https://{display_url}"
                                    else:
                                        final_url = display_url

                                if not final_url:
                                    continue

                                logger.debug(
                                    f"YouTube link: {title} -> {final_url}"
                                )

                                # Detect social from URL or title
                                name = self._detect_social_from_url(final_url)
                                if not name:
                                    name = self._detect_social_from_title(title)
                                if name and name not in socials:
                                    socials[name] = final_url

        except Exception as e:
            logger.debug(f"YouTube socials fetch failed: {e}")

        return socials

    def _detect_social_from_title(self, title: str) -> str | None:
        """Detect social media platform from link title."""
        title_lower = title.lower()
        title_map = {
            "twitter": "twitter",
            "x": "twitter",
            "instagram": "instagram",
            "tiktok": "tiktok",
            "discord": "discord",
            "facebook": "facebook",
            "patreon": "patreon",
            "kick": "kick",
            "twitch": "twitch",
            "website": "website",
            "youtube": "youtube",
            "second channel": "youtube2",
            "clips": "youtube_clips",
        }
        for keyword, platform in title_map.items():
            if keyword in title_lower:
                return platform
        return None

    async def _fetch_kick_socials(self) -> dict[str, str]:
        """Fetch socials from Kick channel API."""
        import aiohttp

        socials: dict[str, str] = {}

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        url = f"https://kick.com/api/v2/channels/{self.channel_id}"

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        return socials

                    data = await resp.json()
                    user = data.get("user", {})

                    # Kick stores socials as usernames, need to construct URLs
                    social_fields = {
                        "twitter": "https://twitter.com/{}",
                        "instagram": "https://instagram.com/{}",
                        "youtube": "https://youtube.com/{}",
                        "discord": "https://discord.gg/{}",
                        "tiktok": "https://tiktok.com/@{}",
                        "facebook": "https://facebook.com/{}",
                    }

                    for field, url_template in social_fields.items():
                        value = user.get(field)
                        if value:
                            # Clean up the value (remove trailing slashes, etc.)
                            value = value.strip().rstrip("/")
                            if value:
                                socials[field] = url_template.format(value)

        except Exception as e:
            logger.debug(f"Kick socials fetch failed: {e}")

        return socials

    def _normalize_social_name(self, name: str) -> str:
        """Normalize social media platform name."""
        name_map = {
            "twitter": "twitter",
            "x": "twitter",
            "instagram": "instagram",
            "youtube": "youtube",
            "tiktok": "tiktok",
            "facebook": "facebook",
            "discord": "discord",
            "patreon": "patreon",
        }
        return name_map.get(name.lower(), name.lower())

    def _detect_social_from_url(self, url: str) -> str | None:
        """Detect social media platform from URL."""
        url_lower = url.lower()
        if "twitter.com" in url_lower or "x.com" in url_lower:
            return "twitter"
        elif "instagram.com" in url_lower:
            return "instagram"
        elif "tiktok.com" in url_lower:
            return "tiktok"
        elif "discord.gg" in url_lower or "discord.com" in url_lower:
            return "discord"
        elif "facebook.com" in url_lower:
            return "facebook"
        elif "patreon.com" in url_lower:
            return "patreon"
        elif "twitch.tv" in url_lower:
            return "twitch"
        elif "kick.com" in url_lower:
            return "kick"
        elif "youtube.com" in url_lower:
            return "youtube"
        return None


class GlobalEmoteFetchWorker(QThread):
    """Worker thread that fetches global emotes."""

    emotes_fetched = Signal(dict)  # {"twitch": list[ChatEmote], "common": list[ChatEmote]}

    def __init__(self, providers: list[str], oauth_token: str, client_id: str, parent=None):
        super().__init__(parent)
        self.providers = providers
        self.oauth_token = oauth_token
        self.client_id = client_id

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._fetch_globals())
            if result:
                self.emotes_fetched.emit(result)
        except Exception as e:
            logger.error(f"Global emote fetch error: {e}")
        finally:
            loop.close()

    async def _fetch_globals(self) -> dict:
        twitch_globals: list[ChatEmote] = []
        common_globals: list[ChatEmote] = []

        twitch_provider = TwitchProvider(
            oauth_token=self.oauth_token,
            client_id=self.client_id,
        )
        try:
            twitch_globals = await twitch_provider.get_global_emotes()
            logger.debug(f"Fetched {len(twitch_globals)} Twitch global emotes")
        except Exception as e:
            logger.debug(f"Failed to fetch Twitch global emotes: {e}")

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
                emotes = await provider.get_global_emotes()
                common_globals.extend(emotes)
                logger.debug(f"Fetched {len(emotes)} global emotes from {name}")
            except Exception as e:
                logger.debug(f"Failed to fetch global emotes from {name}: {e}")

        return {"twitch": twitch_globals, "common": common_globals}


class UserEmoteFetchWorker(QThread):
    """Worker thread that fetches Twitch user emotes."""

    user_emotes_fetched = Signal(list)  # list[ChatEmote]

    def __init__(self, oauth_token: str, client_id: str, parent=None):
        super().__init__(parent)
        self.oauth_token = oauth_token
        self.client_id = client_id

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            emotes = loop.run_until_complete(self._fetch_user_emotes())
            if emotes:
                self.user_emotes_fetched.emit(emotes)
        except Exception as e:
            logger.debug(f"User emote fetch error: {e}")
        finally:
            loop.close()

    async def _get_authenticated_user_id(self) -> str | None:
        """Get the user ID of the authenticated user from the OAuth token."""
        import aiohttp

        if not self.oauth_token or not self.client_id:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.oauth_token}",
                "Client-Id": self.client_id,
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
                            user_id = users[0].get("id", "")
                            if user_id:
                                return user_id
        except Exception as e:
            logger.debug(f"Failed to get authenticated user ID: {e}")

        return None

    async def _fetch_user_emotes(self) -> list[ChatEmote]:
        if not self.oauth_token:
            return []

        user_id = await self._get_authenticated_user_id()
        if not user_id:
            return []

        twitch_provider = TwitchProvider(
            oauth_token=self.oauth_token,
            client_id=self.client_id,
        )
        try:
            emotes = await twitch_provider.get_user_emotes(user_id)
            logger.debug(f"Fetched {len(emotes)} user emotes")
            return emotes
        except Exception as e:
            logger.debug(f"Failed to fetch user emotes: {e}")
            return []


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

    def __init__(self, settings: Settings, monitor=None, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._monitor = monitor
        self._workers: dict[str, ChatConnectionWorker] = {}
        self._connections: dict[str, BaseChatConnection] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._emote_fetch_workers: dict[str, EmoteFetchWorker] = {}
        self._socials_fetch_workers: dict[str, SocialsFetchWorker] = {}
        self._global_emote_worker: GlobalEmoteFetchWorker | None = None
        self._user_emote_worker: UserEmoteFetchWorker | None = None

        # Emote cache shared across all widgets
        self._emote_cache = EmoteCache(parent=self)
        self._emote_cache.emote_loaded.connect(self._on_emote_loaded)
        self._emote_cache.set_disk_limit_mb(self.settings.emote_cache_mb)
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

        # Badge URL mapping from Twitch API: "name/version" -> image_url
        self._badge_url_map: dict[str, str] = {}
        self._badge_image_sets: dict[str, ImageSet] = {}
        self._badges_fetched_at: dict[str, float] = {}

        # Track which badge URLs we've already queued for download
        self._queued_badge_urls: set[str] = set()

        # Debounce emote cache updates to reduce UI churn
        self._emote_update_pending = False
        self._emote_update_timer = QTimer(self)
        self._emote_update_timer.setSingleShot(True)
        self._emote_update_timer.timeout.connect(self._emit_emote_cache_update)

        # Prefetch state (favorites/recent/active)
        self._prefetch_queue: deque[str] = deque()
        self._prefetch_inflight: set[str] = set()
        self._prefetch_workers: dict[str, EmoteFetchWorker] = {}
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.timeout.connect(self._process_prefetch_queue)
        self._prefetch_timer.start(500)
        self._last_emote_providers = tuple(self.settings.chat.builtin.emote_providers)

        # Message batching to throttle UI updates
        self._pending_messages: dict[str, list[ChatMessage]] = {}
        self._message_flush_timer = QTimer(self)
        self._message_flush_timer.setInterval(MESSAGE_FLUSH_INTERVAL_MS)
        self._message_flush_timer.timeout.connect(self._flush_pending_messages)

        # Kick off global/user emote fetch in background
        self._ensure_global_emotes()
        self._ensure_user_emotes()

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
            "prefetch_queue": len(self._prefetch_queue),
            "prefetch_inflight": len(self._prefetch_inflight),
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

    def open_chat(self, livestream: Livestream) -> None:
        """Open a chat connection for a livestream."""
        channel_key = livestream.channel.unique_key
        if channel_key in self._connections:
            self.chat_opened.emit(channel_key, livestream)
            return

        self._livestreams[channel_key] = livestream
        self._record_recent_channel(channel_key)
        self._update_prefetch_targets()
        self._start_connection(channel_key, livestream)

        # Start emote fetching for this channel
        self._fetch_emotes_for_channel(channel_key, livestream)

        # Start socials fetching for all platforms
        self._fetch_socials_for_channel(channel_key, livestream)

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
                worker.wait(500)
            self._disconnect_connection_signals(key)

            # Restart with new token
            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self._ensure_user_emotes(force=True)
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
                worker.wait(500)
            self._disconnect_connection_signals(key)

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
                worker.wait(500)
            self._disconnect_connection_signals(key)

            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self.auth_state_changed.emit(bool(self.settings.youtube.cookies))

    def close_chat(self, channel_key: str) -> None:
        """Close a chat connection."""
        worker = self._workers.pop(channel_key, None)
        if worker:
            worker.stop()
            worker.wait(500)

        # Disconnect signals to break reference cycles before removing connection
        self._disconnect_connection_signals(channel_key)

        self._livestreams.pop(channel_key, None)

        # Clean up emote fetch worker
        fetch_worker = self._emote_fetch_workers.pop(channel_key, None)
        if fetch_worker and fetch_worker.isRunning():
            fetch_worker.wait(500)

        self.chat_closed.emit(channel_key)
        self._update_prefetch_targets()

    def on_refresh_complete(self, livestreams: list[Livestream] | None = None) -> None:
        """Handle refresh complete to update prefetch targets."""
        if livestreams is None and self._monitor:
            livestreams = self._monitor.live_streams
        self._update_prefetch_targets(livestreams)
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

    def _update_prefetch_targets(self, livestreams: list[Livestream] | None = None) -> None:
        """Update prefetch queue based on favorites/recent/active channels."""
        if not self.settings.chat.builtin.show_emotes:
            self._prefetch_queue.clear()
            return
        if self.settings.emote_cache_mb <= 0:
            self._prefetch_queue.clear()
            return

        ordered: list[str] = []
        seen: set[str] = set()

        def add_key(key: str) -> None:
            if key and key not in seen:
                seen.add(key)
                ordered.append(key)

        # Active chats (highest priority, but will be skipped later)
        for key in self._connections.keys():
            add_key(key)

        # Recent chats
        for key in self.settings.chat.recent_channels or []:
            add_key(key)

        # Favorites
        if self._monitor:
            for channel in self._monitor.channels:
                if channel.favorite:
                    add_key(channel.unique_key)

        # Filter to known channels only
        known = set()
        if self._monitor:
            known.update(ch.unique_key for ch in self._monitor.channels)
        known.update(self._livestreams.keys())
        ordered = [key for key in ordered if key in known]

        # Rebuild queue, skipping active connections and recent fetches
        now = time.monotonic()
        self._prefetch_queue = deque(
            key
            for key in ordered
            if key not in self._connections
            and key not in self._emote_fetch_workers
            and key not in self._prefetch_inflight
            and (now - self._channel_emotes_fetched_at.get(key, 0)) >= CHANNEL_EMOTE_TTL
        )

    def _process_prefetch_queue(self) -> None:
        """Prefetch emotes for queued channels in the background."""
        if not self._prefetch_queue:
            return
        if len(self._prefetch_inflight) >= PREFETCH_CONCURRENCY:
            return

        channel_key = self._prefetch_queue.popleft()
        if channel_key in self._prefetch_inflight:
            return
        channel = None
        if self._monitor:
            for ch in self._monitor.channels:
                if ch.unique_key == channel_key:
                    channel = ch
                    break
        if not channel and channel_key in self._livestreams:
            channel = self._livestreams[channel_key].channel
        if not channel:
            return

        worker = EmoteFetchWorker(
            channel_key=channel_key,
            platform=channel.platform.value.lower(),
            channel_id=channel.channel_id,
            providers=self.settings.chat.builtin.emote_providers
            if self.settings.chat.builtin.show_emotes
            else [],
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            fetch_badges=False,
            parent=self,
        )
        worker.emotes_fetched.connect(self._on_emotes_fetched)
        self._prefetch_workers[channel_key] = worker
        self._prefetch_inflight.add(channel_key)
        worker.finished.connect(lambda key=channel_key: self._on_prefetch_finished(key))
        worker.start()

    def _on_prefetch_finished(self, channel_key: str) -> None:
        self._prefetch_inflight.discard(channel_key)
        worker = self._prefetch_workers.pop(channel_key, None)
        if worker and worker.isRunning():
            worker.wait(1000)

    def send_message(self, channel_key: str, text: str) -> None:
        """Send a message to a channel's chat."""
        connection = self._connections.get(channel_key)
        if not connection or not connection.is_connected:
            logger.warning(f"Cannot send message: not connected to {channel_key}")
            return

        worker = self._workers.get(channel_key)
        if worker and worker._loop:
            asyncio.run_coroutine_threadsafe(connection.send_message(text), worker._loop)

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
        self._prefetch_timer.stop()
        self._message_flush_timer.stop()

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
        conn = self._connections.get(channel_key)
        return conn.is_connected if conn else False

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

    def _ensure_badge_image_set(self, badge: ChatBadge) -> ImageSet | None:
        """Ensure a badge has an ImageSet bound to the shared cache."""
        if badge.image_set:
            return badge.image_set
        url = self._badge_url_map.get(badge.id) or badge.image_url
        if not url:
            self._queued_badge_urls.add(badge.id)
            return None
        image_set = self._badge_image_sets.get(badge.id)
        if image_set is None:
            specs = {
                scale: ImageSpec(
                    scale=scale,
                    key=f"badge:{badge.id}@{scale}x",
                    url=url,
                )
                for scale in (1, 2, 3)
            }
            image_set = ImageSet(specs).bind(self._emote_cache)
            self._badge_image_sets[badge.id] = image_set
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
                badge_set = self._ensure_badge_image_set(badge)
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

        # Detect @mentions of our username
        our_nick = self._get_our_nick(channel_key)
        if our_nick:
            mention_pattern = f"@{our_nick}".lower()
            for msg in messages:
                if mention_pattern in msg.text.lower():
                    msg.is_mention = True

        return messages

    def _get_our_nick(self, channel_key: str) -> str | None:
        """Get the authenticated user's display name for the given channel's connection."""
        connection = self._connections.get(channel_key)
        if connection:
            nick = getattr(connection, "_nick", None)
            if nick and not nick.startswith("justinfan"):
                return nick
        return None

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

        worker = SocialsFetchWorker(
            channel_key=channel_key,
            channel_id=livestream.channel.channel_id,
            platform=livestream.channel.platform,
            parent=self,
        )
        worker.socials_fetched.connect(self._on_socials_fetched)
        self._socials_fetch_workers[channel_key] = worker
        worker.start()

    def _on_socials_fetched(self, channel_key: str, socials: dict) -> None:
        """Handle fetched social links - emit to UI."""
        logger.info(f"Fetched socials for {channel_key}: {list(socials.keys())}")
        self.socials_fetched.emit(channel_key, socials)

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
            f"Loaded {len(emotes)} emotes for {channel_key}, "
            f"emote map size: {len(resolved_map)}"
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
                f"User emotes changed: +{len(added)} -{len(removed)} "
                f"(total: {len(new_ids)})"
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
        """Handle fetched badge URL data from Twitch API."""
        self._badge_url_map.update(badge_map)
        self._badges_fetched_at[channel_key] = time.monotonic()
        logger.info(f"Badge URL map updated: {len(self._badge_url_map)} entries")

        # Build or update badge image sets
        for badge_id, url in badge_map.items():
            if badge_id not in self._badge_image_sets:
                specs = {
                    scale: ImageSpec(
                        scale=scale,
                        key=f"badge:{badge_id}@{scale}x",
                        url=url,
                    )
                    for scale in (1, 2, 3)
                }
                self._badge_image_sets[badge_id] = ImageSet(specs).bind(self._emote_cache)

        # Re-queue badges that were attempted before the map was ready
        requeue_count = 0
        for badge_id in list(self._queued_badge_urls):
            cleaned_id = badge_id.removeprefix("badge:")
            image_set = self._badge_image_sets.get(cleaned_id)
            if image_set:
                image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_HIGH)
                self._queued_badge_urls.discard(badge_id)
                requeue_count += 1

        if requeue_count:
            logger.debug(f"Re-queued {requeue_count} badges with correct URLs")

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
        if not force and self._global_common_emotes and (
            now - self._global_emotes_fetched_at < GLOBAL_EMOTE_TTL
        ):
            return
        self._global_emote_worker = GlobalEmoteFetchWorker(
            providers=self.settings.chat.builtin.emote_providers,
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            parent=self,
        )
        self._global_emote_worker.emotes_fetched.connect(self._on_global_emotes_fetched)
        self._global_emote_worker.start()

    def _on_global_emotes_fetched(self, payload: dict) -> None:
        """Handle global emote fetch results."""
        twitch_list = payload.get("twitch", [])
        common_list = payload.get("common", [])
        self._global_twitch_emotes = {}
        for emote in twitch_list:
            if not isinstance(emote, ChatEmote):
                continue
            image_set = self._bind_emote_image_set(emote)
            if image_set:
                image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_LOW)
            self._global_twitch_emotes[emote.name] = emote

        self._global_common_emotes = {}
        for emote in common_list:
            if not isinstance(emote, ChatEmote):
                continue
            image_set = self._bind_emote_image_set(emote)
            if image_set:
                image_set.prefetch(scale=2.0, priority=DOWNLOAD_PRIORITY_LOW)
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
        self._user_emote_worker = UserEmoteFetchWorker(
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            parent=self,
        )
        self._user_emote_worker.user_emotes_fetched.connect(self._on_user_emotes_fetched)
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
            except (RuntimeError, TypeError):
                # Signal may already be disconnected
                pass
