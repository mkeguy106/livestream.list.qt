"""Credential store using system keyring for secure secret storage.

Stores sensitive data (cookies, OAuth tokens) in the system keyring
(GNOME Keyring, KWallet, etc.) instead of plaintext settings.json.
Falls back to settings.json storage if keyring is unavailable.
"""

import logging
import os
import stat

logger = logging.getLogger(__name__)

SERVICE_NAME = "livestream-list-qt"

# Keys for stored secrets
KEY_TWITCH_ACCESS_TOKEN = "twitch_access_token"
KEY_TWITCH_REFRESH_TOKEN = "twitch_refresh_token"
KEY_TWITCH_BROWSER_AUTH_TOKEN = "twitch_browser_auth_token"
KEY_YOUTUBE_COOKIES = "youtube_cookies"
KEY_KICK_ACCESS_TOKEN = "kick_access_token"
KEY_KICK_REFRESH_TOKEN = "kick_refresh_token"

_keyring_available: bool | None = None


def _check_keyring() -> bool:
    """Check if keyring is available and functional."""
    global _keyring_available
    if _keyring_available is not None:
        return _keyring_available

    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        backend = keyring.get_keyring()
        if isinstance(backend, FailKeyring):
            logger.info("Keyring backend is FailKeyring - keyring unavailable")
            _keyring_available = False
            return False

        # Test with a probe write/read/delete
        keyring.set_password(SERVICE_NAME, "_probe", "test")
        result = keyring.get_password(SERVICE_NAME, "_probe")
        keyring.delete_password(SERVICE_NAME, "_probe")
        _keyring_available = result == "test"
        if _keyring_available:
            logger.info(f"Keyring available: {type(backend).__name__}")
        else:
            logger.info("Keyring probe failed")
    except Exception as e:
        logger.info(f"Keyring unavailable: {e}")
        _keyring_available = False

    return _keyring_available


def store_secret(key: str, value: str) -> bool:
    """Store a secret in the system keyring.

    Returns True if stored in keyring, False if caller should use fallback.
    """
    if not value:
        delete_secret(key)
        return True

    if not _check_keyring():
        return False

    try:
        import keyring

        keyring.set_password(SERVICE_NAME, key, value)
        return True
    except Exception as e:
        logger.warning(f"Failed to store secret '{key}' in keyring: {e}")
        return False


def get_secret(key: str) -> str | None:
    """Retrieve a secret from the system keyring.

    Returns the secret value, or None if not found or keyring unavailable.
    """
    if not _check_keyring():
        return None

    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, key)
    except Exception as e:
        logger.warning(f"Failed to get secret '{key}' from keyring: {e}")
        return None


def delete_secret(key: str) -> None:
    """Delete a secret from the system keyring."""
    if not _check_keyring():
        return

    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, key)
    except Exception:
        pass  # Not found or unavailable


def is_available() -> bool:
    """Check if the keyring is available for use."""
    return _check_keyring()


def secure_file_permissions(filepath: str) -> None:
    """Set file permissions to owner-only (chmod 600) as fallback protection."""
    try:
        os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:
        logger.debug(f"Could not set permissions on {filepath}: {e}")
