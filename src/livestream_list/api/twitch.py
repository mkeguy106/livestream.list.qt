"""Twitch Helix API client."""

import asyncio
import logging
import re
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import aiohttp

from ..core.models import Channel, Livestream, StreamPlatform
from ..core.settings import TwitchSettings
from .base import BaseApiClient
from .oauth_server import OAuthServer

logger = logging.getLogger(__name__)

# Twitch application client ID for OAuth
# Using Streamlink Twitch GUI's registered app (open source, widely used)
# Users can override with their own in settings
DEFAULT_CLIENT_ID = "phiay4sq36lfv9zu7cbqwz2ndnesfd8"

# For GraphQL queries (no auth required)
GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# OAuth settings
OAUTH_SCOPES = "user:read:follows"


class TwitchApiClient(BaseApiClient):
    """Client for Twitch Helix API."""

    BASE_URL = "https://api.twitch.tv/helix"
    AUTH_URL = "https://id.twitch.tv/oauth2"
    GQL_URL = "https://gql.twitch.tv/gql"

    def __init__(self, settings: TwitchSettings) -> None:
        super().__init__()
        self.settings = settings
        self._user_cache: dict[str, dict[str, Any]] = {}
        self._current_user_id: Optional[str] = None

    @property
    def platform(self) -> StreamPlatform:
        return StreamPlatform.TWITCH

    @property
    def name(self) -> str:
        return "Twitch"

    @property
    def client_id(self) -> str:
        """Get the client ID to use."""
        return self.settings.client_id or DEFAULT_CLIENT_ID

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.settings.access_token}",
        }

    def _get_gql_headers(self) -> dict[str, str]:
        """Get headers for GraphQL requests (no auth required for public data)."""
        return {
            "Client-ID": GQL_CLIENT_ID,
            "Content-Type": "application/json",
        }

    async def is_authorized(self) -> bool:
        """Check if we have a valid access token."""
        if not self.settings.access_token:
            return False

        try:
            async with self.session.get(
                f"{self.AUTH_URL}/validate",
                headers={"Authorization": f"OAuth {self.settings.access_token}"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._current_user_id = data.get("user_id")
                    return True
                return False
        except aiohttp.ClientError:
            return False

    async def authorize(self) -> bool:
        """
        Authorize using client credentials flow.
        Requires client_id and client_secret to be set.
        """
        if not self.settings.client_id or not self.settings.client_secret:
            return False

        try:
            async with self.session.post(
                f"{self.AUTH_URL}/token",
                data={
                    "client_id": self.settings.client_id,
                    "client_secret": self.settings.client_secret,
                    "grant_type": "client_credentials",
                },
            ) as resp:
                if resp.status != 200:
                    return False

                data = await resp.json()
                self.settings.access_token = data["access_token"]
                return True
        except (aiohttp.ClientError, KeyError):
            return False

    def get_oauth_url(self, redirect_uri: str) -> str:
        """
        Get the OAuth authorization URL for browser-based login.
        Uses Implicit Grant Flow - token is returned in URL fragment.
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "token",
            "scope": OAUTH_SCOPES,
        }
        return f"{self.AUTH_URL}/authorize?{urlencode(params)}"

    async def oauth_login(self, timeout: float = 300) -> bool:
        """
        Perform OAuth login flow with local callback server.

        Opens browser to Twitch authorization page and waits for the
        callback with the access token.

        Args:
            timeout: Maximum seconds to wait for authorization.

        Returns:
            True if authorization was successful.
        """
        # Start local OAuth server on port 65432 (must match registered redirect URI)
        server = OAuthServer(port=65432)
        server.start()

        try:
            # Build OAuth URL with our redirect URI
            oauth_url = self.get_oauth_url(server.redirect_uri)
            logger.info(f"Opening OAuth URL: {oauth_url}")

            # Open browser
            webbrowser.open(oauth_url)

            # Wait for token
            token = await server.wait_for_token(timeout=timeout)

            if token:
                self.settings.access_token = token
                # Validate the token
                if await self.is_authorized():
                    logger.info("OAuth login successful")
                    return True
                else:
                    logger.error("Token validation failed")
                    self.settings.access_token = ""
                    return False
            else:
                logger.error("OAuth login timed out")
                return False

        finally:
            server.stop()

    def set_access_token(self, token: str) -> None:
        """Set the access token manually."""
        self.settings.access_token = token

    async def get_current_user(self) -> Optional[dict[str, Any]]:
        """Get the currently authenticated user."""
        if not await self.is_authorized():
            return None

        try:
            async with self.session.get(
                f"{self.BASE_URL}/users",
                headers=self._get_headers(),
            ) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                users = data.get("data", [])
                if users:
                    user = users[0]
                    self._current_user_id = user["id"]
                    return user
        except aiohttp.ClientError:
            pass
        return None

    async def _get_user(self, login: str) -> Optional[dict[str, Any]]:
        """Get user info by login name."""
        if login.lower() in self._user_cache:
            return self._user_cache[login.lower()]

        # Try GraphQL first (no auth required)
        user = await self._get_user_gql(login)
        if user:
            return user

        # Fall back to Helix API if authorized
        if not await self.is_authorized():
            return None

        try:
            async with self.session.get(
                f"{self.BASE_URL}/users",
                headers=self._get_headers(),
                params={"login": login},
            ) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                users = data.get("data", [])
                if not users:
                    return None

                user = users[0]
                self._user_cache[login.lower()] = user
                return user
        except aiohttp.ClientError:
            return None

    async def _get_user_gql(self, login: str) -> Optional[dict[str, Any]]:
        """Get user info via GraphQL (no auth required)."""
        query = """
        query GetUser($login: String!) {
            user(login: $login) {
                id
                login
                displayName
            }
        }
        """

        try:
            async with self.session.post(
                self.GQL_URL,
                headers=self._get_gql_headers(),
                json={
                    "query": query,
                    "variables": {"login": login},
                },
            ) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                user_data = data.get("data", {}).get("user")
                if not user_data:
                    return None

                # Convert to Helix format
                user = {
                    "id": user_data["id"],
                    "login": user_data["login"],
                    "display_name": user_data["displayName"],
                }
                self._user_cache[login.lower()] = user
                return user
        except aiohttp.ClientError:
            return None

    async def _get_stream_gql(self, login: str) -> Optional[dict[str, Any]]:
        """Get stream info via GraphQL (no auth required)."""
        query = """
        query GetStream($login: String!) {
            user(login: $login) {
                id
                login
                displayName
                stream {
                    id
                    title
                    viewersCount
                    createdAt
                    game {
                        name
                    }
                }
                lastBroadcast {
                    startedAt
                }
            }
        }
        """

        try:
            async with self.session.post(
                self.GQL_URL,
                headers=self._get_gql_headers(),
                json={
                    "query": query,
                    "variables": {"login": login},
                },
            ) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                return data.get("data", {}).get("user")
        except aiohttp.ClientError:
            return None

    async def _get_streams_gql_batch(self, logins: list[str]) -> dict[str, Optional[dict[str, Any]]]:
        """Get stream info for multiple channels via GraphQL in a single request."""
        if not logins:
            return {}

        # Build query with aliases for each user
        # Example: u0: user(login: "channel1") { ... } u1: user(login: "channel2") { ... }
        user_fragment = """
            id
            login
            displayName
            stream {
                id
                title
                viewersCount
                createdAt
                game {
                    name
                }
            }
            lastBroadcast {
                startedAt
            }
        """

        # Build aliased queries
        queries = []
        for i, login in enumerate(logins):
            # Escape quotes in login name
            escaped_login = login.replace('"', '\\"')
            queries.append(f'u{i}: user(login: "{escaped_login}") {{ {user_fragment} }}')

        query = "query GetStreamsBatch { " + " ".join(queries) + " }"

        try:
            async with self.session.post(
                self.GQL_URL,
                headers=self._get_gql_headers(),
                json={"query": query},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"GraphQL batch query failed with status {resp.status}")
                    return {}

                data = await resp.json()
                result_data = data.get("data", {})

                # Map results back to login names
                result: dict[str, Optional[dict[str, Any]]] = {}
                for i, login in enumerate(logins):
                    result[login.lower()] = result_data.get(f"u{i}")

                return result
        except aiohttp.ClientError as e:
            logger.warning(f"GraphQL batch query error: {e}")
            return {}

    async def _get_users_by_ids(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Get multiple users by their IDs."""
        result: dict[str, dict[str, Any]] = {}

        if not await self.is_authorized():
            return result

        # Twitch allows up to 100 users per request
        for i in range(0, len(user_ids), 100):
            batch = user_ids[i : i + 100]

            try:
                params = "&".join(f"id={uid}" for uid in batch)
                async with self.session.get(
                    f"{self.BASE_URL}/users?{params}",
                    headers=self._get_headers(),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for user in data.get("data", []):
                            result[user["id"]] = user
                            self._user_cache[user["login"].lower()] = user
            except aiohttp.ClientError:
                continue

        return result

    async def get_channel_info(self, channel_id: str) -> Optional[Channel]:
        """Get channel info by username."""
        user = await self._get_user(channel_id)
        if not user:
            return None

        return Channel(
            channel_id=user["login"],
            platform=StreamPlatform.TWITCH,
            display_name=user["display_name"],
        )

    async def get_livestream(self, channel: Channel) -> Livestream:
        """Get livestream status for a single channel."""
        # Try GraphQL first (no auth required)
        user_data = await self._get_stream_gql(channel.channel_id)

        if user_data:
            stream = user_data.get("stream")
            if stream:
                start_time = None
                if stream.get("createdAt"):
                    try:
                        start_time = datetime.fromisoformat(
                            stream["createdAt"].replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                return Livestream(
                    channel=channel,
                    live=True,
                    title=stream.get("title"),
                    game=stream.get("game", {}).get("name") if stream.get("game") else None,
                    viewers=stream.get("viewersCount", 0),
                    start_time=start_time,
                )

            # Get last broadcast time for offline channels
            last_live_time = None
            last_broadcast = user_data.get("lastBroadcast")
            if last_broadcast and last_broadcast.get("startedAt"):
                try:
                    last_live_time = datetime.fromisoformat(
                        last_broadcast["startedAt"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            return Livestream(channel=channel, live=False, last_live_time=last_live_time)

        # Fall back to Helix API
        streams = await self.get_livestreams([channel])
        if streams:
            return streams[0]

        return Livestream(channel=channel, live=False)

    async def get_livestreams(self, channels: list[Channel]) -> list[Livestream]:
        """Get livestream status for multiple channels using batched GraphQL queries."""
        if not channels:
            return []

        result: list[Livestream] = []
        channel_map = {c.channel_id.lower(): c for c in channels}

        # Batch channels into groups of 35 per request
        batch_size = 35
        all_logins = [c.channel_id for c in channels]

        for i in range(0, len(all_logins), batch_size):
            batch_logins = all_logins[i : i + batch_size]
            batch_results = await self._get_streams_gql_batch(batch_logins)

            for login in batch_logins:
                channel = channel_map[login.lower()]
                user_data = batch_results.get(login.lower())

                if user_data:
                    stream = user_data.get("stream")
                    if stream:
                        start_time = None
                        if stream.get("createdAt"):
                            try:
                                start_time = datetime.fromisoformat(
                                    stream["createdAt"].replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass

                        result.append(Livestream(
                            channel=channel,
                            live=True,
                            title=stream.get("title"),
                            game=stream.get("game", {}).get("name") if stream.get("game") else None,
                            viewers=stream.get("viewersCount", 0),
                            start_time=start_time,
                        ))
                    else:
                        # Get last broadcast time for offline channels
                        last_live_time = None
                        last_broadcast = user_data.get("lastBroadcast")
                        if last_broadcast and last_broadcast.get("startedAt"):
                            try:
                                last_live_time = datetime.fromisoformat(
                                    last_broadcast["startedAt"].replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass
                        result.append(Livestream(channel=channel, live=False, last_live_time=last_live_time))
                else:
                    result.append(Livestream(channel=channel, live=False))

        return result

    async def get_followed_channels(self, user_id: Optional[str] = None) -> list[Channel]:
        """Get channels followed by a user. Uses current user if user_id is None."""
        if not await self.is_authorized():
            raise PermissionError("Twitch authorization required to get followed channels")

        # Get user ID
        if user_id:
            user = await self._get_user(user_id)
            if not user:
                return []
            twitch_user_id = user["id"]
        elif self._current_user_id:
            twitch_user_id = self._current_user_id
        else:
            current_user = await self.get_current_user()
            if not current_user:
                return []
            twitch_user_id = current_user["id"]

        channels: list[Channel] = []
        cursor: Optional[str] = None

        while True:
            try:
                params: dict[str, Any] = {
                    "user_id": twitch_user_id,
                    "first": 100,
                }
                if cursor:
                    params["after"] = cursor

                async with self.session.get(
                    f"{self.BASE_URL}/channels/followed",
                    headers=self._get_headers(),
                    params=params,
                ) as resp:
                    if resp.status == 401:
                        raise PermissionError("Twitch authorization required")
                    if resp.status != 200:
                        break

                    data = await resp.json()
                    follows = data.get("data", [])

                    for follow in follows:
                        channels.append(
                            Channel(
                                channel_id=follow["broadcaster_login"],
                                platform=StreamPlatform.TWITCH,
                                display_name=follow["broadcaster_name"],
                                imported_by=user_id or "self",
                            )
                        )

                    # Check for pagination
                    pagination = data.get("pagination", {})
                    cursor = pagination.get("cursor")
                    if not cursor or not follows:
                        break

            except aiohttp.ClientError:
                break

        return channels

    async def get_top_streams(
        self,
        game_id: Optional[str] = None,
        limit: int = 25,
    ) -> list[Livestream]:
        """Get top live streams."""
        streams: list[Livestream] = []

        if not await self.is_authorized():
            return streams

        try:
            params: dict[str, Any] = {"first": min(limit, 100)}
            if game_id:
                params["game_id"] = game_id

            async with self.session.get(
                f"{self.BASE_URL}/streams",
                headers=self._get_headers(),
                params=params,
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for stream_data in data.get("data", []):
                    start_time = None
                    if stream_data.get("started_at"):
                        try:
                            start_time = datetime.fromisoformat(
                                stream_data["started_at"].replace("Z", "+00:00")
                            )
                        except ValueError:
                            pass

                    thumbnail_url = None
                    if stream_data.get("thumbnail_url"):
                        thumbnail_url = stream_data["thumbnail_url"].replace(
                            "{width}", "320"
                        ).replace("{height}", "180")

                    channel = Channel(
                        channel_id=stream_data["user_login"],
                        platform=StreamPlatform.TWITCH,
                        display_name=stream_data["user_name"],
                    )

                    streams.append(
                        Livestream(
                            channel=channel,
                            live=True,
                            title=stream_data.get("title"),
                            game=stream_data.get("game_name"),
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

        if not await self.is_authorized():
            return channels

        try:
            async with self.session.get(
                f"{self.BASE_URL}/search/channels",
                headers=self._get_headers(),
                params={"query": query, "first": min(limit, 100)},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for ch in data.get("data", []):
                    channels.append(
                        Channel(
                            channel_id=ch["broadcaster_login"],
                            platform=StreamPlatform.TWITCH,
                            display_name=ch["display_name"],
                        )
                    )

        except aiohttp.ClientError:
            pass

        return channels

    async def get_games(self, query: str, limit: int = 25) -> list[dict[str, str]]:
        """Search for games/categories."""
        games: list[dict[str, str]] = []

        if not await self.is_authorized():
            return games

        try:
            async with self.session.get(
                f"{self.BASE_URL}/search/categories",
                headers=self._get_headers(),
                params={"query": query, "first": min(limit, 100)},
            ) as resp:
                if resp.status != 200:
                    return []

                data = await resp.json()

                for game in data.get("data", []):
                    games.append(
                        {
                            "id": game["id"],
                            "name": game["name"],
                            "box_art_url": game.get("box_art_url", ""),
                        }
                    )

        except aiohttp.ClientError:
            pass

        return games

    def logout(self) -> None:
        """Clear stored credentials."""
        self.settings.access_token = ""
        self.settings.refresh_token = ""
        self._current_user_id = None
