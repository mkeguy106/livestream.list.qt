"""Streamlink integration for launching streams."""

import asyncio
import logging
import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable

from .models import LaunchMethod, Livestream, StreamPlatform, StreamQuality
from .settings import StreamlinkSettings

logger = logging.getLogger(__name__)


def is_flatpak() -> bool:
    """Check if running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


def host_command(cmd: list[str]) -> list[str]:
    """Wrap command to run on host if inside Flatpak."""
    if is_flatpak():
        return ["flatpak-spawn", "--host"] + cmd
    return cmd


def _validate_additional_args(args_str: str) -> list[str]:
    """Validate and parse additional arguments safely.

    Uses shlex.split for proper parsing and validates that all args
    start with - or -- to prevent command injection.
    """
    if not args_str:
        return []

    try:
        args = shlex.split(args_str)
    except ValueError as e:
        logger.warning(f"Invalid additional_args syntax: {e}")
        return []

    validated = []
    for arg in args:
        # Allow args starting with - or --
        # Also allow = in the middle of an arg (e.g., --player-args=foo)
        if arg.startswith("-"):
            validated.append(arg)
        elif "=" in arg and not arg.startswith("="):
            # This could be a value from a previous arg, skip validation
            validated.append(arg)
        else:
            logger.warning(f"Skipping invalid argument (must start with -): {arg}")
    return validated


class StreamlinkLauncher:
    """Launches streams using streamlink."""

    def __init__(
        self,
        settings: StreamlinkSettings,
        twitch_token: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self._twitch_token = twitch_token
        # Track active streams: {channel_key: (process, livestream)}
        self._active_streams: dict[str, tuple[subprocess.Popen, Livestream]] = {}
        # Callbacks for when a stream stops
        self._on_stream_stopped: list[Callable[[str], None]] = []
        # Callbacks for when a turbo-authenticated launch fails
        self._on_turbo_auth_failed: list[Callable[[Livestream], None]] = []
        # Lock to protect _active_streams from concurrent access
        self._lock = threading.Lock()

    def on_stream_stopped(self, callback: Callable[[str], None]) -> None:
        """Register callback for when a stream stops playing."""
        self._on_stream_stopped.append(callback)

    def on_turbo_auth_failed(self, callback: Callable[[Livestream], None]) -> None:
        """Register callback for when a turbo-authenticated launch fails quickly."""
        self._on_turbo_auth_failed.append(callback)

    def is_playing(self, channel_key: str) -> bool:
        """Check if a stream is currently playing."""
        with self._lock:
            if channel_key not in self._active_streams:
                return False
            process, _ = self._active_streams[channel_key]
            # Check if process is still running
            if process.poll() is not None:
                # Process has exited, clean up
                del self._active_streams[channel_key]
                return False
            return True

    def get_playing_streams(self) -> list[str]:
        """Get list of channel keys that are currently playing."""
        # Clean up dead processes first
        self.cleanup_dead_processes()
        with self._lock:
            return list(self._active_streams.keys())

    def cleanup_dead_processes(self) -> list[str]:
        """Remove dead processes from tracking. Returns list of stopped channel keys."""
        stopped = []
        # Collect stopped keys while holding lock
        with self._lock:
            for key in list(self._active_streams.keys()):
                process, _ = self._active_streams[key]
                if process.poll() is not None:
                    del self._active_streams[key]
                    stopped.append(key)

        # Fire callbacks outside of lock to avoid deadlocks
        for key in stopped:
            for callback in self._on_stream_stopped:
                try:
                    callback(key)
                except Exception as e:
                    logger.error(f"Stream stopped callback error: {e}")
        return stopped

    def stop_stream(self, channel_key: str) -> bool:
        """Stop a playing stream."""
        with self._lock:
            if channel_key not in self._active_streams:
                return False
            process, _ = self._active_streams[channel_key]
            try:
                process.terminate()
                # Give it a moment to terminate gracefully
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                del self._active_streams[channel_key]
            except Exception as e:
                logger.error(f"Failed to stop stream: {e}")
                return False

        # Fire callbacks outside of lock to avoid deadlocks
        for callback in self._on_stream_stopped:
            try:
                callback(channel_key)
            except Exception as e:
                logger.error(f"Stream stopped callback error: {e}")
        return True

    def stop_all_streams(self) -> None:
        """Stop all playing streams."""
        with self._lock:
            keys = list(self._active_streams.keys())
        for key in keys:
            self.stop_stream(key)

    def is_available(self) -> bool:
        """Check if streamlink is installed and accessible."""
        if is_flatpak():
            # Check on host system via flatpak-spawn
            try:
                result = subprocess.run(
                    ["flatpak-spawn", "--host", "which", self.settings.path],
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0
            except Exception:
                return False
        path = shutil.which(self.settings.path)
        return path is not None

    def get_version(self) -> str | None:
        """Get the installed streamlink version."""
        try:
            cmd = host_command([self.settings.path, "--version"])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse version from output like "streamlink 6.5.0"
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def build_command(
        self,
        livestream: Livestream,
        quality: StreamQuality | None = None,
    ) -> list[str]:
        """Build the streamlink command for a stream."""
        cmd = [self.settings.path]

        # Player
        if self.settings.player:
            cmd.extend(["--player", self.settings.player])

        # Player arguments
        if self.settings.player_args:
            cmd.extend(["--player-args", self.settings.player_args])

        # Additional arguments (validated to prevent command injection)
        if self.settings.additional_args:
            cmd.extend(_validate_additional_args(self.settings.additional_args))

        # Twitch Turbo: pass OAuth token for ad-free viewing
        if (
            self.settings.twitch_turbo
            and livestream.channel.platform == StreamPlatform.TWITCH
            and self._twitch_token
        ):
            token = self._twitch_token()
            if token:
                cmd.append(f"--twitch-api-header=Authorization=Bearer {token}")

        # Stream URL
        cmd.append(livestream.stream_url)

        # Quality
        quality = quality or self.settings.default_quality
        cmd.append(quality.value)

        return cmd

    def _build_ytdlp_command(self, livestream: Livestream) -> list[str]:
        """Build command to launch stream directly with player (using yt-dlp backend).

        Launches the player directly and lets it use yt-dlp internally to handle the stream.
        """
        cmd = [self.settings.player or "mpv"]

        # Player arguments (validated to prevent command injection)
        if self.settings.player_args:
            cmd.extend(_validate_additional_args(self.settings.player_args))

        # Stream URL - player (mpv) will use yt-dlp to handle the stream
        cmd.append(livestream.stream_url)

        return cmd

    def _monitor_turbo_launch(self, process: subprocess.Popen, livestream: Livestream) -> None:
        """Monitor a turbo-enabled launch for early failure (runs in daemon thread)."""
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return  # Still running after 3s — stream is working
        # Process exited almost immediately — likely auth rejection
        logger.warning("Turbo-authenticated streamlink exited within 3s, likely auth failure")
        for callback in self._on_turbo_auth_failed:
            try:
                callback(livestream)
            except Exception as e:
                logger.error(f"Turbo auth failed callback error: {e}")

    def _get_launch_method(self, platform: StreamPlatform) -> LaunchMethod:
        """Get the configured launch method for a platform."""
        if platform == StreamPlatform.TWITCH:
            return self.settings.twitch_launch_method
        elif platform == StreamPlatform.YOUTUBE:
            return self.settings.youtube_launch_method
        elif platform == StreamPlatform.KICK:
            return self.settings.kick_launch_method
        # Default to streamlink for unknown platforms
        return LaunchMethod.STREAMLINK

    def launch(
        self,
        livestream: Livestream,
        quality: StreamQuality | None = None,
    ) -> subprocess.Popen | None:
        """Launch a stream using the configured method for the platform."""
        if not self.settings.enabled:
            logger.warning("Streamlink is disabled")
            return None

        channel_key = livestream.channel.unique_key

        # Stop existing stream for this channel if any (is_playing/stop_stream use lock)
        if self.is_playing(channel_key):
            self.stop_stream(channel_key)

        # Get the launch method for this platform
        launch_method = self._get_launch_method(livestream.channel.platform)

        if launch_method == LaunchMethod.YT_DLP:
            cmd = host_command(self._build_ytdlp_command(livestream))
            logger.info(f"Launching via yt-dlp: {' '.join(cmd)}")
        else:
            # LaunchMethod.STREAMLINK
            if not self.is_available():
                logger.error("Streamlink is not available")
                return None
            cmd = host_command(self.build_command(livestream, quality))
            logger.info(f"Launching via streamlink: {' '.join(cmd)}")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Track this stream
            with self._lock:
                self._active_streams[channel_key] = (process, livestream)

            # Monitor turbo-enabled Twitch launches for early auth failure
            if (
                self.settings.twitch_turbo
                and livestream.channel.platform == StreamPlatform.TWITCH
                and launch_method == LaunchMethod.STREAMLINK
            ):
                threading.Thread(
                    target=self._monitor_turbo_launch,
                    args=(process, livestream),
                    daemon=True,
                ).start()

            return process
        except Exception as e:
            logger.error(f"Failed to launch stream: {e}")
            return None

    async def launch_async(
        self,
        livestream: Livestream,
        quality: StreamQuality | None = None,
    ) -> asyncio.subprocess.Process | None:
        """Launch streamlink asynchronously."""
        if not self.settings.enabled:
            logger.warning("Streamlink is disabled")
            return None

        if not self.is_available():
            logger.error("Streamlink is not available")
            return None

        cmd = host_command(self.build_command(livestream, quality))
        logger.info(f"Launching: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            return process
        except Exception as e:
            logger.error(f"Failed to launch streamlink: {e}")
            return None


def open_in_browser(livestream: Livestream) -> bool:
    """Open the stream URL in the default browser."""
    import webbrowser

    try:
        webbrowser.open(livestream.stream_url)
        return True
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")
        return False


def open_chat_in_browser(livestream: Livestream) -> bool:
    """Open the chat URL in the default browser."""
    import webbrowser

    chat_url = livestream.chat_url
    if not chat_url:
        return False

    try:
        webbrowser.open(chat_url)
        return True
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")
        return False
