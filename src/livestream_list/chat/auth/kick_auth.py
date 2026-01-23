"""Kick OAuth authentication flow for chat."""

import logging

logger = logging.getLogger(__name__)


class KickAuthFlow:
    """Kick authentication for sending chat messages.

    Note: Kick's OAuth is not officially documented for third-party apps.
    This is a placeholder for when/if they provide a public OAuth API.
    Currently, Kick chat can only be read without authentication.
    """

    def __init__(self):
        self._token: str = ""

    @property
    def token(self) -> str:
        """Get the current auth token."""
        return self._token

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid token."""
        return bool(self._token)

    async def authenticate(self) -> bool:
        """Start the Kick authentication flow.

        Returns True if authentication was successful.
        """
        # Kick doesn't have a public OAuth API for third-party apps yet.
        # When they do, this would open a browser for OAuth consent,
        # listen on a local callback server, and store the token.
        logger.warning("Kick chat authentication is not yet available")
        return False

    def clear_token(self) -> None:
        """Clear the stored token."""
        self._token = ""
