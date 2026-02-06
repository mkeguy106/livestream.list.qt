"""Kick OAuth 2.1 authentication flow with PKCE for chat."""

import base64
import hashlib
import logging
import webbrowser
from urllib.parse import urlencode

import aiohttp

from ...api.oauth_server import OAuthServer
from ...core.settings import KickSettings

logger = logging.getLogger(__name__)

KICK_OAUTH_BASE = "https://id.kick.com"
KICK_API_BASE = "https://api.kick.com/public/v1"
KICK_SCOPES = "chat:write user:read"

# Registered Kick developer app for this project.
# NOTE: Client secret in source code is acceptable here because:
# 1. We use PKCE (Proof Key for Code Exchange) which provides security even if the
#    client secret is known - the code_verifier/challenge proves the client's identity
# 2. Desktop/native apps are "public clients" that cannot keep secrets anyway
# 3. OAuth 2.1 with PKCE is designed for this exact scenario
DEFAULT_KICK_CLIENT_ID = "01KE2K1TM3ZZ4S3824V79RG2FJ"
DEFAULT_KICK_CLIENT_SECRET = "bc2e8d615c40624929fe3f22a3b7ec468d58aaaab52e383c3c1d6c49ea546668"


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    import secrets

    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


class KickAuthFlow:
    """Kick OAuth 2.1 authentication with PKCE for sending chat messages."""

    def __init__(self, settings: KickSettings):
        self._settings = settings

    @property
    def token(self) -> str:
        """Get the current access token."""
        return self._settings.access_token

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid token."""
        return bool(self._settings.access_token)

    @property
    def _client_id(self) -> str:
        return self._settings.client_id or DEFAULT_KICK_CLIENT_ID

    @property
    def _client_secret(self) -> str:
        return self._settings.client_secret or DEFAULT_KICK_CLIENT_SECRET

    async def authenticate(self, timeout: float = 120) -> bool:
        """Start the Kick OAuth 2.1 + PKCE flow.

        Opens browser to Kick authorization page, waits for code callback,
        then exchanges the code for access + refresh tokens.

        Returns True if authentication was successful.
        """
        # Start local OAuth server on port 65432 (must match registered redirect URI)
        server = OAuthServer(port=65432)

        try:
            # Generate PKCE values
            code_verifier, code_challenge = _generate_pkce()

            # Generate state via server for CSRF protection (validated on callback)
            state = server.generate_state()

            # Start server after generating state so it's available for validation
            server.start()

            # Build authorization URL
            params = {
                "client_id": self._client_id,
                "redirect_uri": server.redirect_uri,
                "response_type": "code",
                "scope": KICK_SCOPES,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
            auth_url = f"{KICK_OAUTH_BASE}/oauth/authorize?{urlencode(params)}"
            logger.debug(f"Opening Kick OAuth URL (state={state[:8]}...)")

            # Open browser for user authorization
            webbrowser.open(auth_url)

            # Wait for authorization code
            code = await server.wait_for_code(timeout=timeout)
            if not code:
                logger.error("Kick OAuth: no authorization code received")
                return False

            # Exchange code for tokens
            success = await self._exchange_code(code, code_verifier, server.redirect_uri)
            if success:
                await self._fetch_login_name()
                logger.info("Kick OAuth login successful")
            return success

        finally:
            server.stop()

    async def _exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> bool:
        """Exchange authorization code for access + refresh tokens."""
        data = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KICK_OAUTH_BASE}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Kick token exchange failed ({resp.status}): {body}")
                    return False

                token_data = await resp.json()
                self._settings.access_token = token_data.get("access_token", "")
                self._settings.refresh_token = token_data.get("refresh_token", "")
                return bool(self._settings.access_token)

    async def _fetch_login_name(self) -> None:
        """Fetch the authenticated user's username from Kick API."""
        if not self._settings.access_token:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.kick.com/public/v1/users",
                    headers={"Authorization": f"Bearer {self._settings.access_token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        users = data.get("data", [])
                        if users:
                            name = users[0].get("name", "")
                            if name:
                                self._settings.login_name = name
                                logger.info(f"Kick authenticated as: {name}")
        except Exception as e:
            logger.warning(f"Failed to fetch Kick username: {e}")

    async def refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        if not self._settings.refresh_token:
            return False

        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._settings.refresh_token,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KICK_OAUTH_BASE}/oauth/token",
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Kick token refresh failed ({resp.status})")
                    self._settings.access_token = ""
                    self._settings.refresh_token = ""
                    return False

                token_data = await resp.json()
                self._settings.access_token = token_data.get("access_token", "")
                self._settings.refresh_token = token_data.get(
                    "refresh_token", self._settings.refresh_token
                )
                return bool(self._settings.access_token)

    def clear_token(self) -> None:
        """Clear stored tokens."""
        self._settings.access_token = ""
        self._settings.refresh_token = ""
