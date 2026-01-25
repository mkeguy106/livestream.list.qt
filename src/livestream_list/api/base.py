"""Base API client interface."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeVar

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform

logger = logging.getLogger(__name__)


async def safe_json(resp: aiohttp.ClientResponse) -> dict | list | None:
    """Safely parse JSON from response, returning None on error.

    This handles common error cases:
    - HTML error pages (ContentTypeError)
    - Malformed JSON (JSONDecodeError)
    - Empty responses

    Args:
        resp: aiohttp response object

    Returns:
        Parsed JSON data or None if parsing failed
    """
    try:
        return await resp.json()
    except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to parse JSON response: {e}")
        return None

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 10.0  # seconds

T = TypeVar("T")


class BaseApiClient(ABC):
    """Abstract base class for streaming platform API clients."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

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
            connector = aiohttp.TCPConnector(limit=50)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            try:
                await self._session.close()
                # Allow time for underlying connections to fully close
                # This prevents "Unclosed connector" warnings from aiohttp
                await asyncio.sleep(0.1)
            except RuntimeError as e:
                # Session may be attached to a different event loop
                # This can happen when sessions are created in worker threads
                # Just log and continue - the session will be garbage collected
                if "attached to a different loop" in str(e):
                    logger.debug(f"Session attached to different loop, skipping close: {e}")
                else:
                    raise
            finally:
                self._session = None

    def reset_session(self) -> None:
        """Reset the HTTP session.

        This should be called before running async operations in a new event loop
        to avoid 'attached to a different event loop' errors with aiohttp.
        The session will be lazily recreated on next access.

        Note: Call close() first if the session's event loop is still running.
        This method is intended for use after the loop has been closed.
        """
        self._session = None

    async def _retry_with_backoff(
        self,
        operation: Callable[[], T],  # type: ignore[type-arg]
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        retryable_exceptions: tuple[type[Exception], ...] = (
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ),
    ) -> T:
        """Execute an operation with exponential backoff retry.

        Args:
            operation: Async callable to execute.
            max_retries: Maximum number of retry attempts.
            base_delay: Initial delay between retries in seconds.
            max_delay: Maximum delay between retries in seconds.
            retryable_exceptions: Tuple of exceptions to retry on.

        Returns:
            The result of the operation.

        Raises:
            The last exception if all retries fail.
        """
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                return await operation()
            except retryable_exceptions as e:
                last_exception = e

                if attempt < max_retries:
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2**attempt), max_delay)
                    logger.warning(
                        f"{self.name}: Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"{self.name}: All {max_retries + 1} attempts failed. Last error: {e}"
                    )

        # All retries exhausted
        raise last_exception  # type: ignore

    def _is_retryable_status(self, status: int) -> bool:
        """Check if an HTTP status code is retryable.

        Args:
            status: HTTP status code.

        Returns:
            True if the status indicates a transient error worth retrying.
        """
        # Retry on server errors (5xx) and rate limiting (429)
        return status >= 500 or status == 429

    def _parse_retry_after(self, headers: dict, default: float = 1.0) -> float:
        """Parse Retry-After header value.

        Args:
            headers: Response headers.
            default: Default delay if header is missing or unparseable.

        Returns:
            Delay in seconds to wait before retrying.
        """
        retry_after = headers.get("Retry-After")
        if not retry_after:
            return default

        try:
            # Retry-After can be seconds or HTTP-date
            # Most APIs use seconds
            return float(retry_after)
        except ValueError:
            # Try parsing as HTTP-date (RFC 7231)
            from email.utils import parsedate_to_datetime

            try:
                retry_dt = parsedate_to_datetime(retry_after)
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc)
                delta = (retry_dt - now).total_seconds()
                return max(delta, 0.0)
            except (TypeError, ValueError):
                return default

    @abstractmethod
    async def is_authorized(self) -> bool:
        """Check if the client is authorized."""
        ...

    @abstractmethod
    async def authorize(self) -> bool:
        """Authorize the client. Returns True on success."""
        ...

    @abstractmethod
    async def get_channel_info(self, channel_id: str) -> Channel | None:
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
        game_id: str | None = None,
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
