"""Base API client interface."""

from abc import ABC, abstractmethod
from typing import Optional

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform


class BaseApiClient(ABC):
    """Abstract base class for streaming platform API clients."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    @abstractmethod
    def platform(self) -> StreamPlatform:
        """Get the platform this client handles."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Get the display name for this platform."""
        ...

    @property
    def session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session."""
        if self._session is None or self._session.closed:
            # Use explicit timeout to avoid Python 3.11 compatibility issues
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(limit=10)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    @abstractmethod
    async def is_authorized(self) -> bool:
        """Check if the client is authorized."""
        ...

    @abstractmethod
    async def authorize(self) -> bool:
        """Authorize the client. Returns True on success."""
        ...

    @abstractmethod
    async def get_channel_info(self, channel_id: str) -> Optional[Channel]:
        """
        Get information about a channel.
        Returns None if the channel doesn't exist.
        """
        ...

    @abstractmethod
    async def get_livestream(self, channel: Channel) -> Livestream:
        """
        Get the current livestream status for a channel.
        Always returns a Livestream object, with live=False if not streaming.
        """
        ...

    @abstractmethod
    async def get_livestreams(self, channels: list[Channel]) -> list[Livestream]:
        """
        Get livestream status for multiple channels.
        More efficient than calling get_livestream multiple times.
        """
        ...

    async def get_followed_channels(self, user_id: str) -> list[Channel]:
        """
        Get channels followed by a user.
        Not all platforms support this.
        """
        raise NotImplementedError(f"{self.name} does not support importing followed channels")

    async def get_top_streams(
        self,
        game_id: Optional[str] = None,
        limit: int = 25,
    ) -> list[Livestream]:
        """
        Get top streams, optionally filtered by game.
        Not all platforms support this.
        """
        raise NotImplementedError(f"{self.name} does not support top streams discovery")

    async def search_channels(self, query: str, limit: int = 25) -> list[Channel]:
        """
        Search for channels by name.
        Not all platforms support this.
        """
        raise NotImplementedError(f"{self.name} does not support channel search")
