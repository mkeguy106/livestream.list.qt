"""Chat launcher for opening stream chat in browser."""

import logging
import os
import shutil
import subprocess
import webbrowser

from .models import StreamPlatform
from .settings import ChatSettings

logger = logging.getLogger(__name__)


def is_flatpak() -> bool:
    """Check if running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


def host_command(cmd: list[str]) -> list[str]:
    """Wrap command to run on host if inside Flatpak."""
    if is_flatpak():
        return ["flatpak-spawn", "--host"] + cmd
    return cmd

# Chat URL templates by platform
TWITCH_CHAT_URLS = {
    0: "https://www.twitch.tv/popout/{channel}/chat",      # Popout (recommended)
    1: "https://www.twitch.tv/embed/{channel}/chat",       # Embedded
    2: "https://www.twitch.tv/{channel}/chat",             # Default (legacy)
}

KICK_CHAT_URL = "https://kick.com/{channel}/chatroom"
YOUTUBE_CHAT_URL = "https://www.youtube.com/live_chat?v={video_id}"

# Browser executable names by platform
BROWSER_COMMANDS = {
    "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
    "chromium": ["chromium", "chromium-browser"],
    "edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
    "firefox": ["firefox"],
}


class ChatLauncher:
    """Launcher for opening stream chat in browser."""

    def __init__(self, settings: ChatSettings) -> None:
        self.settings = settings

    def get_chat_url(self, channel: str, platform: StreamPlatform = StreamPlatform.TWITCH, video_id: str | None = None) -> str | None:
        """Get the chat URL for a channel based on platform and settings."""
        if platform == StreamPlatform.TWITCH:
            url_template = TWITCH_CHAT_URLS.get(self.settings.url_type, TWITCH_CHAT_URLS[0])
            return url_template.format(channel=channel.lower())
        elif platform == StreamPlatform.KICK:
            return KICK_CHAT_URL.format(channel=channel.lower())
        elif platform == StreamPlatform.YOUTUBE:
            if not video_id:
                logger.warning(f"Cannot open YouTube chat without video ID for {channel}")
                return None
            return YOUTUBE_CHAT_URL.format(video_id=video_id)
        else:
            # Fallback to Twitch
            url_template = TWITCH_CHAT_URLS.get(self.settings.url_type, TWITCH_CHAT_URLS[0])
            return url_template.format(channel=channel.lower())

    def _find_browser_executable(self, browser: str) -> str | None:
        """Find the executable path for a browser."""
        if browser == "default":
            return None  # Use system default

        commands = BROWSER_COMMANDS.get(browser, [])
        for cmd in commands:
            if is_flatpak():
                # Check on host system via flatpak-spawn
                try:
                    result = subprocess.run(
                        ["flatpak-spawn", "--host", "which", cmd],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return cmd  # Return command name, not full path
                except Exception:
                    pass
            else:
                path = shutil.which(cmd)
                if path:
                    return path

        logger.warning(f"Browser '{browser}' not found, falling back to default")
        return None

    def _open_url_in_browser(self, url: str, description: str) -> bool:
        """
        Open a URL in the configured browser.

        Args:
            url: The URL to open.
            description: Description for logging (e.g., "chat for channelname").

        Returns:
            True if successful, False otherwise.
        """
        browser = self.settings.browser

        try:
            # For Flatpak with new_window, we need to call browser directly via host
            if is_flatpak() and self.settings.new_window:
                # Try to find a browser to use with --new-window
                browser_to_use = None
                if browser != "default":
                    browser_to_use = self._find_browser_executable(browser)

                # If no specific browser set or not found, try common browsers
                if not browser_to_use:
                    for browser_name in ["firefox", "chrome", "chromium", "edge"]:
                        browser_to_use = self._find_browser_executable(browser_name)
                        if browser_to_use:
                            break

                if browser_to_use:
                    cmd = host_command([browser_to_use, "--new-window", url])
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"Opened {description} in {browser_to_use} (new window)")
                    return True
                else:
                    # Fallback to xdg-open (won't be new window)
                    cmd = host_command(["xdg-open", url])
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"Opened {description} via xdg-open (new window not supported)")
                    return True

            elif browser == "default":
                # Use system default browser (non-Flatpak or new_window=False)
                if self.settings.new_window:
                    webbrowser.open_new(url)
                else:
                    webbrowser.open(url)
                logger.info(f"Opened {description} in default browser")
                return True

            else:
                # Find browser executable
                executable = self._find_browser_executable(browser)
                if executable:
                    # Launch browser with URL
                    cmd = [executable]
                    if self.settings.new_window:
                        cmd.append("--new-window")
                    cmd.append(url)
                    cmd = host_command(cmd) if is_flatpak() else cmd
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"Opened {description} in {browser}")
                    return True
                else:
                    # Fallback to default browser
                    if self.settings.new_window:
                        webbrowser.open_new(url)
                    else:
                        webbrowser.open(url)
                    logger.info(f"Opened {description} in default browser (fallback)")
                    return True

        except Exception as e:
            logger.error(f"Failed to open {description}: {e}")
            return False

    def open_chat(self, channel: str, platform: StreamPlatform = StreamPlatform.TWITCH, video_id: str | None = None) -> bool:
        """
        Open chat for a channel in the configured browser.

        Args:
            channel: The channel name to open chat for.
            platform: The streaming platform.
            video_id: YouTube video ID (required for YouTube chat).

        Returns:
            True if chat was opened successfully, False otherwise.
        """
        if not self.settings.enabled:
            logger.debug("Chat is disabled in settings")
            return False

        url = self.get_chat_url(channel, platform, video_id)
        if not url:
            return False

        return self._open_url_in_browser(url, f"chat for {channel}")

    def get_channel_url(self, channel: str, platform: StreamPlatform = StreamPlatform.TWITCH) -> str:
        """Get the channel URL for a channel based on platform."""
        if platform == StreamPlatform.TWITCH:
            return f"https://twitch.tv/{channel.lower()}"
        elif platform == StreamPlatform.KICK:
            return f"https://kick.com/{channel.lower()}"
        elif platform == StreamPlatform.YOUTUBE:
            # Open the live stream page (redirects to channel if not live)
            if channel.startswith("@"):
                return f"https://youtube.com/{channel}/live"
            elif channel.startswith("UC"):
                return f"https://youtube.com/channel/{channel}/live"
            else:
                return f"https://youtube.com/@{channel}/live"
        else:
            return f"https://twitch.tv/{channel.lower()}"

    def open_channel(self, channel: str, platform: StreamPlatform = StreamPlatform.TWITCH) -> bool:
        """
        Open channel page in the configured browser.

        Args:
            channel: The channel name to open.
            platform: The streaming platform.

        Returns:
            True if page was opened successfully, False otherwise.
        """
        url = self.get_channel_url(channel, platform)
        return self._open_url_in_browser(url, f"channel {channel}")

    def open_chat_app_mode(self, channel: str, platform: StreamPlatform = StreamPlatform.TWITCH, video_id: str | None = None) -> bool:
        """
        Open chat in app mode (Chrome/Chromium/Edge only).

        This opens the chat without browser UI elements (address bar, etc.),
        similar to Twitch's popout window.

        Args:
            channel: The channel name to open chat for.
            platform: The streaming platform.
            video_id: YouTube video ID (required for YouTube chat).

        Returns:
            True if chat was opened successfully, False otherwise.
        """
        if not self.settings.enabled:
            return False

        url = self.get_chat_url(channel, platform, video_id)
        if not url:
            return False
        browser = self.settings.browser

        # App mode only works with Chromium-based browsers
        if browser in ("chrome", "chromium", "edge"):
            executable = self._find_browser_executable(browser)
            if executable:
                try:
                    cmd = host_command([executable, f"--app={url}"])
                    subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    logger.info(f"Opened chat for {channel} in {browser} app mode")
                    return True
                except Exception as e:
                    logger.error(f"Failed to open chat in app mode: {e}")

        # Fall back to regular open
        return self.open_chat(channel, platform, video_id)
