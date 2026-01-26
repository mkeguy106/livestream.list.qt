"""Emote providers for Twitch, Kick, 7TV, BTTV, and FFZ."""

import logging
from abc import ABC, abstractmethod

import aiohttp

from ..models import ChatEmote

logger = logging.getLogger(__name__)

# Default Twitch client ID for unauthenticated requests
_DEFAULT_TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"


class BaseEmoteProvider(ABC):
    """Base class for emote providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""

    @abstractmethod
    async def get_global_emotes(self) -> list[ChatEmote]:
        """Fetch global emotes for this provider."""

    @abstractmethod
    async def get_channel_emotes(self, platform: str, channel_id: str) -> list[ChatEmote]:
        """Fetch channel-specific emotes.

        Args:
            platform: Platform name ("twitch", "kick", "youtube")
            channel_id: The channel/user ID on the platform.
        """


class TwitchProvider(BaseEmoteProvider):
    """Native Twitch emote provider using Helix API."""

    BASE_URL = "https://api.twitch.tv/helix"

    def __init__(self, oauth_token: str = "", client_id: str = ""):
        self.oauth_token = oauth_token
        self.client_id = client_id or _DEFAULT_TWITCH_CLIENT_ID

    @property
    def name(self) -> str:
        return "twitch"

    def _get_headers(self) -> dict:
        """Get headers for Twitch API requests."""
        headers = {"Client-Id": self.client_id}
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        return headers

    async def get_global_emotes(self) -> list[ChatEmote]:
        """Fetch Twitch global emotes."""
        emotes: list[ChatEmote] = []
        try:
            async with aiohttp.ClientSession(headers=self._get_headers()) as session:
                async with session.get(
                    f"{self.BASE_URL}/chat/emotes/global",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"Twitch global emotes failed: {resp.status}")
                        return emotes
                    data = await resp.json()

                    for emote_data in data.get("data", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"Twitch global emotes error: {e}")

        return emotes

    async def get_channel_emotes(self, platform: str, channel_id: str) -> list[ChatEmote]:
        """Fetch Twitch channel emotes (subscriber emotes)."""
        emotes: list[ChatEmote] = []
        if platform != "twitch":
            return emotes

        try:
            async with aiohttp.ClientSession(headers=self._get_headers()) as session:
                async with session.get(
                    f"{self.BASE_URL}/chat/emotes",
                    params={"broadcaster_id": channel_id},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"Twitch channel emotes failed: {resp.status}")
                        return emotes
                    data = await resp.json()

                    for emote_data in data.get("data", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"Twitch channel emotes error for {channel_id}: {e}")

        return emotes

    async def get_user_emotes(self, user_id: str) -> list[ChatEmote]:
        """Fetch all emotes the authenticated user has access to.

        This includes subscriber emotes from channels they're subscribed to,
        follower emotes, and bits-tier emotes. Requires user:read:emotes scope.
        """
        emotes: list[ChatEmote] = []
        if not self.oauth_token:
            return emotes

        try:
            async with aiohttp.ClientSession(headers=self._get_headers()) as session:
                cursor: str | None = ""
                while cursor is not None:
                    params: dict[str, str] = {"user_id": user_id}
                    if cursor:
                        params["after"] = cursor

                    async with session.get(
                        f"{self.BASE_URL}/chat/emotes/user",
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(f"Twitch user emotes failed: {resp.status}")
                            return emotes
                        data = await resp.json()

                        for emote_data in data.get("data", []):
                            emote = self._parse_emote(emote_data)
                            if emote:
                                emotes.append(emote)

                        # Handle pagination
                        cursor = data.get("pagination", {}).get("cursor")
        except Exception as e:
            logger.debug(f"Twitch user emotes error: {e}")

        return emotes

    def _parse_emote(self, data: dict) -> ChatEmote | None:
        """Parse a Twitch emote from Helix API data."""
        emote_id = data.get("id", "")
        name = data.get("name", "")

        if not emote_id or not name:
            return None

        # Twitch CDN URL - use static format for 2x size
        # Format options: static/animated, light/dark, 1.0/2.0/3.0
        images = data.get("images", {})
        url = images.get("url_2x") or images.get("url_1x", "")

        if not url:
            # Fallback to template format
            url = f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/static/light/2.0"

        return ChatEmote(
            id=emote_id,
            name=name,
            url_template=url,
            provider="twitch",
        )


class SevenTVProvider(BaseEmoteProvider):
    """7TV emote provider."""

    BASE_URL = "https://7tv.io/v3"

    @property
    def name(self) -> str:
        return "7tv"

    async def get_global_emotes(self) -> list[ChatEmote]:
        """Fetch 7TV global emotes."""
        emotes: list[ChatEmote] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/emote-sets/global",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    for emote_data in data.get("emotes", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"7TV global emotes error: {e}")

        return emotes

    async def get_channel_emotes(self, platform: str, channel_id: str) -> list[ChatEmote]:
        """Fetch 7TV channel emotes."""
        emotes: list[ChatEmote] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/users/{platform}/{channel_id}",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    emote_set = data.get("emote_set", {})
                    for emote_data in emote_set.get("emotes", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"7TV channel emotes error for {channel_id}: {e}")

        return emotes

    def _parse_emote(self, data: dict) -> ChatEmote | None:
        """Parse a 7TV emote from API data."""
        emote_data = data.get("data", data)
        emote_id = emote_data.get("id", data.get("id", ""))
        name = data.get("name", emote_data.get("name", ""))

        if not emote_id or not name:
            return None

        # 7TV CDN URL format
        host = emote_data.get("host", {})
        base_url = host.get("url", f"//cdn.7tv.app/emote/{emote_id}")
        if base_url.startswith("//"):
            base_url = "https:" + base_url

        # Use 2x size for good quality at 28px
        url_template = f"{base_url}/2x.webp"

        flags = emote_data.get("flags", 0)
        zero_width = bool(flags & 1)  # ZeroWidth flag

        return ChatEmote(
            id=emote_id,
            name=name,
            url_template=url_template,
            provider="7tv",
            zero_width=zero_width,
        )


class BTTVProvider(BaseEmoteProvider):
    """BetterTTV emote provider."""

    BASE_URL = "https://api.betterttv.net/3"

    @property
    def name(self) -> str:
        return "bttv"

    async def get_global_emotes(self) -> list[ChatEmote]:
        """Fetch BTTV global emotes."""
        emotes: list[ChatEmote] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/cached/emotes/global",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    for emote_data in data:
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"BTTV global emotes error: {e}")

        return emotes

    async def get_channel_emotes(self, platform: str, channel_id: str) -> list[ChatEmote]:
        """Fetch BTTV channel emotes."""
        emotes: list[ChatEmote] = []
        # BTTV uses Twitch user IDs for channel lookup
        if platform != "twitch":
            return emotes

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/cached/users/twitch/{channel_id}",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    # Channel emotes
                    for emote_data in data.get("channelEmotes", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)

                    # Shared emotes
                    for emote_data in data.get("sharedEmotes", []):
                        emote = self._parse_emote(emote_data)
                        if emote:
                            emotes.append(emote)
        except Exception as e:
            logger.debug(f"BTTV channel emotes error for {channel_id}: {e}")

        return emotes

    def _parse_emote(self, data: dict) -> ChatEmote | None:
        """Parse a BTTV emote from API data."""
        emote_id = data.get("id", "")
        code = data.get("code", "")

        if not emote_id or not code:
            return None

        # BTTV CDN: https://cdn.betterttv.net/emote/{id}/{size}x
        url_template = f"https://cdn.betterttv.net/emote/{emote_id}/2x"

        return ChatEmote(
            id=emote_id,
            name=code,
            url_template=url_template,
            provider="bttv",
        )


class FFZProvider(BaseEmoteProvider):
    """FrankerFaceZ emote provider."""

    BASE_URL = "https://api.frankerfacez.com/v1"

    @property
    def name(self) -> str:
        return "ffz"

    async def get_global_emotes(self) -> list[ChatEmote]:
        """Fetch FFZ global emotes."""
        emotes: list[ChatEmote] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/set/global",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    for set_id in data.get("default_sets", []):
                        emote_set = data.get("sets", {}).get(str(set_id), {})
                        for emote_data in emote_set.get("emoticons", []):
                            emote = self._parse_emote(emote_data)
                            if emote:
                                emotes.append(emote)
        except Exception as e:
            logger.debug(f"FFZ global emotes error: {e}")

        return emotes

    async def get_channel_emotes(self, platform: str, channel_id: str) -> list[ChatEmote]:
        """Fetch FFZ channel emotes."""
        emotes: list[ChatEmote] = []
        # FFZ uses Twitch user IDs
        if platform != "twitch":
            return emotes

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/room/id/{channel_id}",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return emotes
                    data = await resp.json()

                    for set_data in data.get("sets", {}).values():
                        for emote_data in set_data.get("emoticons", []):
                            emote = self._parse_emote(emote_data)
                            if emote:
                                emotes.append(emote)
        except Exception as e:
            logger.debug(f"FFZ channel emotes error for {channel_id}: {e}")

        return emotes

    def _parse_emote(self, data: dict) -> ChatEmote | None:
        """Parse an FFZ emote from API data."""
        emote_id = str(data.get("id", ""))
        name = data.get("name", "")

        if not emote_id or not name:
            return None

        # FFZ URLs: pick best available size
        urls = data.get("urls", {})
        url = urls.get("2") or urls.get("1") or ""
        if url and url.startswith("//"):
            url = "https:" + url

        if not url:
            return None

        return ChatEmote(
            id=emote_id,
            name=name,
            url_template=url,
            provider="ffz",
        )
