"""YouTube cookie-based authentication for chat sending."""

import logging

from ...core.settings import YouTubeSettings
from ..connections.youtube import validate_cookies

logger = logging.getLogger(__name__)


class YouTubeAuthFlow:
    """Cookie-based authentication for YouTube chat sending.

    YouTube chat sending uses InnerTube's private API which authenticates
    via browser cookies (SAPISID hash). No OAuth flow is needed - the user
    pastes their browser cookies in preferences.
    """

    def __init__(self, youtube_settings: YouTubeSettings | None = None):
        self._youtube_settings = youtube_settings

    @property
    def is_authenticated(self) -> bool:
        """Check if valid cookies are configured."""
        if not self._youtube_settings or not self._youtube_settings.cookies:
            return False
        return validate_cookies(self._youtube_settings.cookies)

    async def authenticate(self) -> bool:
        """No-op - cookies are configured via preferences UI."""
        logger.info("YouTube auth is cookie-based. Configure cookies in Preferences > Accounts.")
        return self.is_authenticated

    async def refresh_access_token(self) -> bool:
        """No-op - cookies don't need refreshing (they last ~2 years)."""
        return self.is_authenticated

    def clear_tokens(self) -> None:
        """Clear stored cookies."""
        if self._youtube_settings:
            self._youtube_settings.cookies = ""
