"""Chat manager - orchestrates connections, emote loading, and badge fetching."""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
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
MAX_EMOTE_DOWNLOAD_RETRIES = 2


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
    user_emotes_fetched = Signal(list)  # list[ChatEmote] - for caching

    def __init__(
        self,
        channel_key: str,
        platform: str,
        channel_id: str,
        providers: list[str],
        oauth_token: str = "",
        client_id: str = "",
        cached_user_emotes: list | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.platform = platform
        self.channel_id = channel_id
        self.providers = providers
        self.oauth_token = oauth_token
        self.client_id = client_id
        self.cached_user_emotes = cached_user_emotes or []

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
                                logger.debug(f"Authenticated user ID: {user_id}")
                                return user_id
        except Exception as e:
            logger.debug(f"Failed to get authenticated user ID: {e}")

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

            # Fetch user's subscribed emotes (requires user:read:emotes scope)
            # Stale-while-revalidate: use cached immediately, fetch fresh in background
            if self.oauth_token:
                # Use cached emotes immediately (stale)
                if self.cached_user_emotes:
                    all_emotes.extend(self.cached_user_emotes)
                    logger.debug(
                        f"Using {len(self.cached_user_emotes)} cached user emotes from twitch"
                    )

                # Always fetch fresh emotes (revalidate)
                try:
                    user_id = await self._get_authenticated_user_id()
                    if user_id:
                        fresh_user_emotes = await twitch_provider.get_user_emotes(user_id)
                        logger.debug(
                            f"Fetched {len(fresh_user_emotes)} fresh user emotes from twitch"
                        )
                        # Emit fresh emotes for caching
                        self.user_emotes_fetched.emit(fresh_user_emotes)

                        # If we didn't have cached emotes, add fresh ones now
                        if not self.cached_user_emotes:
                            all_emotes.extend(fresh_user_emotes)
                except Exception as e:
                    logger.debug(f"Failed to fetch user emotes from twitch: {e}")

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
        import re
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

    def __init__(self, settings: Settings, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self._workers: dict[str, ChatConnectionWorker] = {}
        self._connections: dict[str, BaseChatConnection] = {}
        self._livestreams: dict[str, Livestream] = {}
        self._emote_fetch_workers: dict[str, EmoteFetchWorker] = {}
        self._socials_fetch_workers: dict[str, SocialsFetchWorker] = {}

        # Emote cache shared across all widgets
        self._emote_cache = EmoteCache(parent=self)
        self._emote_cache.emote_loaded.connect(self._on_emote_loaded)

        # Emote loader for downloading images
        self._loader: EmoteLoaderWorker | None = None
        self._download_queue: list[tuple[str, str]] = []  # (cache_key, url)
        self._download_attempts: dict[tuple[str, str], int] = {}
        self._download_fallbacks: dict[tuple[str, str], str] = {}
        self._last_download_url: dict[str, str] = {}
        self._download_blocked_until: dict[tuple[str, str], float] = {}

        # Map of emote name -> ChatEmote for all loaded channels
        self._emote_map: dict[str, ChatEmote] = {}

        # Cached user emotes (subscriber emotes from other channels)
        # Uses stale-while-revalidate: cached emotes used immediately, fresh fetched in background
        self._cached_user_emotes: list[ChatEmote] = []

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
                worker.wait(3000)
            self._disconnect_connection_signals(key)

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
                worker.wait(3000)
            self._disconnect_connection_signals(key)

            livestream = self._livestreams[key]
            self._start_connection(key, livestream)

        self.auth_state_changed.emit(bool(self.settings.youtube.cookies))

    def close_chat(self, channel_key: str) -> None:
        """Close a chat connection."""
        worker = self._workers.pop(channel_key, None)
        if worker:
            worker.stop()
            worker.wait(3000)

        # Disconnect signals to break reference cycles before removing connection
        self._disconnect_connection_signals(channel_key)

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
        self._stop_loader()

        # Clean up global emote/badge state when all chats are closed
        # This prevents unbounded memory growth in long-running sessions
        self._emote_map.clear()
        self._badge_url_map.clear()
        self._queued_badge_urls.clear()
        self._download_queue.clear()
        self._download_attempts.clear()
        self._download_fallbacks.clear()
        self._last_download_url.clear()
        self._download_blocked_until.clear()

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
                    self._queue_download(badge_key, badge_url)

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
                    self._queue_download(emote_key, url)
                elif not self._emote_cache.has_animation_data(emote_key):
                    # Legacy PNG-only cache - re-download for animation detection
                    url = emote.url_template.replace("{size}", "2.0")
                    self._emote_cache.mark_pending(emote_key)
                    self._queue_download(emote_key, url)
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

    def _queue_download(self, key: str, url: str) -> None:
        """Queue a download with retry/fallback metadata."""
        if not url:
            return

        attempt_key = (key, url)
        blocked_until = self._download_blocked_until.get(attempt_key, 0)
        if blocked_until > time.monotonic():
            return
        attempts = self._download_attempts.get(attempt_key, 0)
        if attempts >= MAX_EMOTE_DOWNLOAD_RETRIES:
            logger.debug(f"Skipping download for {key} (attempts exceeded): {url}")
            self._download_blocked_until[attempt_key] = time.monotonic() + 60
            self._emote_cache.clear_pending(key)
            return

        self._last_download_url[key] = url
        fallback_url = self._get_fallback_url(key, url)
        if fallback_url and fallback_url != url:
            self._download_fallbacks[attempt_key] = fallback_url

        self._download_queue.append((key, url))

    def _get_fallback_url(self, key: str, url: str) -> str | None:
        """Return a fallback URL for animated Twitch emotes."""
        if not key.startswith("emote:twitch:"):
            return None
        if "/animated/" in url:
            return url.replace("/animated/", "/static/")
        return None

    def _schedule_retry(self, key: str, url: str, delay_ms: int) -> None:
        """Schedule a retry for a failed download."""
        QTimer.singleShot(delay_ms, lambda: self._retry_download(key, url))

    def _retry_download(self, key: str, url: str) -> None:
        """Retry a download if still under retry limits."""
        attempt_key = (key, url)
        attempts = self._download_attempts.get(attempt_key, 0)
        if attempts >= MAX_EMOTE_DOWNLOAD_RETRIES:
            return
        self._emote_cache.mark_pending(key)
        self._queue_download(key, url)
        if self._download_queue:
            self._start_downloads()

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
        claimed_ranges = [(start, end) for start, end, _ in msg.emote_positions]

        def overlaps(start: int, end: int) -> bool:
            for s, e in claimed_ranges:
                if start < e and end > s:
                    return True
            return False

        # Match words, allowing punctuation boundaries (e.g., "KEKW!" or "(OMEGALUL)")
        new_positions: list[tuple[int, int, ChatEmote]] = []
        punct_strip = ".,!?;:()[]{}<>\"'`~"
        for match in re.finditer(r"\S+", text):
            token = match.group()
            token_start = match.start()
            token_end = match.end()

            # Skip URLs to avoid false matches inside links
            if token.startswith("http://") or token.startswith("https://"):
                continue

            # Prefer exact match (covers emotes with punctuation in the name)
            emote = self._emote_map.get(token)
            if emote:
                if not overlaps(token_start, token_end):
                    new_positions.append((token_start, token_end, emote))
                    claimed_ranges.append((token_start, token_end))
                continue

            # Try trimmed match (strip leading/trailing punctuation)
            left = 0
            right = len(token)
            while left < right and token[left] in punct_strip:
                left += 1
            while right > left and token[right - 1] in punct_strip:
                right -= 1

            if left == 0 and right == len(token):
                continue

            trimmed = token[left:right]
            if not trimmed:
                continue

            emote = self._emote_map.get(trimmed)
            if not emote:
                continue

            start = token_start + left
            end = token_start + right
            if not overlaps(start, end):
                new_positions.append((start, end, emote))
                claimed_ranges.append((start, end))

        if new_positions:
            # Merge with existing positions and sort by start
            msg.emote_positions = sorted(msg.emote_positions + new_positions, key=lambda x: x[0])
            logger.debug(f"Matched {len(new_positions)} 3rd-party emotes in message")

    def backfill_third_party_emotes(self, messages: list[ChatMessage]) -> int:
        """Backfill third-party emotes for existing messages.

        Returns number of messages updated.
        """
        if not self._emote_map or not messages:
            return 0

        updated = 0
        for msg in messages:
            if not isinstance(msg, ChatMessage):
                continue
            before = len(msg.emote_positions)
            self._match_third_party_emotes(msg)
            if len(msg.emote_positions) != before:
                updated += 1

        return updated

    def _fetch_emotes_for_channel(self, channel_key: str, livestream: Livestream) -> None:
        """Kick off async emote/badge fetching for a channel."""
        providers = self.settings.chat.builtin.emote_providers
        platform_name = livestream.channel.platform.value.lower()
        channel_id = livestream.channel.channel_id

        # Pass cached user emotes for stale-while-revalidate (Twitch only)
        cached_user_emotes = self._cached_user_emotes if platform_name == "twitch" else []

        worker = EmoteFetchWorker(
            channel_key=channel_key,
            platform=platform_name,
            channel_id=channel_id,
            providers=providers if self.settings.chat.builtin.show_emotes else [],
            oauth_token=self.settings.twitch.access_token,
            client_id=self.settings.twitch.client_id or _DEFAULT_TWITCH_CLIENT_ID,
            cached_user_emotes=cached_user_emotes,
            parent=self,
        )
        worker.emotes_fetched.connect(self._on_emotes_fetched)
        worker.badges_fetched.connect(self._on_badges_fetched)
        worker.user_emotes_fetched.connect(self._on_user_emotes_fetched)
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
                self._queue_download(emote_key, emote.url_template)
            elif not self._emote_cache.has_animation_data(emote_key):
                # Legacy PNG cache only - re-download for animation detection
                self._emote_cache.mark_pending(emote_key)
                self._queue_download(emote_key, emote.url_template)
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
        self.emote_map_updated.emit(channel_key)

        # Start downloading
        if self._download_queue:
            self._start_downloads()

    def _on_user_emotes_fetched(self, emotes: list) -> None:
        """Handle fresh user emotes from background fetch - update cache.

        Part of stale-while-revalidate: this is called when fresh emotes arrive.
        We update the cache and add any new emotes to the emote map.
        """
        if not emotes:
            return

        # Check if emotes changed (compare by emote IDs)
        old_ids = {e.id for e in self._cached_user_emotes}
        new_ids = {e.id for e in emotes if isinstance(e, ChatEmote)}

        if old_ids != new_ids:
            added = new_ids - old_ids
            removed = old_ids - new_ids
            logger.info(
                f"User emotes changed: +{len(added)} -{len(removed)} "
                f"(total: {len(new_ids)})"
            )

            # Add new emotes to the map and queue downloads
            for emote in emotes:
                if not isinstance(emote, ChatEmote):
                    continue
                if emote.id in added:
                    self._emote_map[emote.name] = emote
                    emote_key = f"emote:{emote.provider}:{emote.id}"
                    if not self._emote_cache.has(emote_key):
                        self._emote_cache.mark_pending(emote_key)
                        self._queue_download(emote_key, emote.url_template)

            if self._download_queue:
                self._start_downloads()

            # Notify widgets to refresh
            self.emote_cache_updated.emit()
            self.emote_map_updated.emit("")

        # Update cache for next time
        self._cached_user_emotes = [e for e in emotes if isinstance(e, ChatEmote)]
        logger.debug(f"User emotes cache updated: {len(self._cached_user_emotes)} emotes")

    def refresh_user_emotes(self) -> None:
        """Manually refresh user emotes by clearing cache and re-fetching.

        Call this when the user wants to force-refresh their emotes
        (e.g., after subscribing to a new channel).
        """
        logger.info("Manual user emotes refresh requested")
        self._cached_user_emotes = []

        # Re-fetch emotes for all open Twitch channels
        for channel_key, livestream in self._livestreams.items():
            if livestream.channel.platform == StreamPlatform.TWITCH:
                self._fetch_emotes_for_channel(channel_key, livestream)

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
                self._queue_download(badge_key, url)
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
        self._loader.emote_failed.connect(self._on_emote_failed)
        self._loader.finished.connect(self._on_loader_finished)

        for key, url in self._download_queue:
            self._loader.enqueue(key, url)
        self._download_queue.clear()

        self._loader.start()

    def _on_emote_downloaded(self, key: str, raw_data: bytes) -> None:
        """Handle a downloaded emote/badge image - create QPixmap on main thread."""
        if not raw_data:
            self._handle_download_failure(key, "empty")
            return

        # Create QPixmap on main thread (GUI thread) as required by Qt
        pixmap = QPixmap()
        if pixmap.loadFromData(raw_data):
            # Scale to standard height
            if pixmap.height() > 0 and pixmap.height() != DEFAULT_EMOTE_HEIGHT:
                pixmap = pixmap.scaledToHeight(
                    DEFAULT_EMOTE_HEIGHT,
                    mode=Qt.TransformationMode.SmoothTransformation,
                )
            self._emote_cache.put(key, pixmap)
            # Save raw bytes for emotes so has_animation_data() returns True,
            # preventing re-downloads on future launches.
            if key.startswith("emote:"):
                self._emote_cache._put_disk_raw(key, raw_data)
            self._clear_download_attempts(key)
        else:
            self._handle_download_failure(key, "decode")

    def _on_animated_emote_downloaded(self, key: str, raw_data: bytes) -> None:
        """Handle a downloaded animated emote - extract frames on GUI thread."""
        result = _extract_frames(raw_data)
        if result:
            frames, delays = result
            self._emote_cache.put_animated(key, frames, delays)
            self._emote_cache._put_disk_raw(key, raw_data)
            self._clear_download_attempts(key)
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
                self._clear_download_attempts(key)
            else:
                self._handle_download_failure(key, "decode")

    def _on_emote_failed(self, key: str, url: str, status: int, reason: str) -> None:
        """Handle download failures from the loader."""
        self._handle_download_failure(key, reason, url=url, status=status)

    def _handle_download_failure(
        self, key: str, reason: str, url: str | None = None, status: int = 0
    ) -> None:
        """Clear pending state and schedule retries/fallbacks for failed downloads."""
        self._emote_cache.clear_pending(key)

        if not url:
            url = self._last_download_url.get(key, "")
        if not url:
            return

        attempt_key = (key, url)
        attempts = self._download_attempts.get(attempt_key, 0) + 1
        self._download_attempts[attempt_key] = attempts

        fallback_url = self._download_fallbacks.get(attempt_key)
        if fallback_url and (status == 404 or reason in {"decode", "empty"}):
            logger.debug(f"Animated emote missing, falling back to static: {key}")
            self._schedule_retry(key, fallback_url, delay_ms=150)
            return

        if attempts < MAX_EMOTE_DOWNLOAD_RETRIES:
            delay_ms = 200 + attempts * 300
            logger.debug(
                f"Retrying emote download ({attempts}/{MAX_EMOTE_DOWNLOAD_RETRIES}) "
                f"{key} due to {reason}"
            )
            self._schedule_retry(key, url, delay_ms=delay_ms)

    def _clear_download_attempts(self, key: str) -> None:
        """Clear attempt counters once a key succeeds."""
        for attempt_key in list(self._download_attempts.keys()):
            if attempt_key[0] == key:
                self._download_attempts.pop(attempt_key, None)
                self._download_fallbacks.pop(attempt_key, None)
                self._download_blocked_until.pop(attempt_key, None)
        self._last_download_url.pop(key, None)

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
