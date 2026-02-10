"""Desktop notification handler."""

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone

from ..core.models import Livestream
from ..core.settings import NotificationSettings

logger = logging.getLogger(__name__)


class Notifier:
    """Handles desktop notifications for stream events."""

    def __init__(
        self,
        settings: NotificationSettings,
        on_open_stream: Callable[[Livestream], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_open_stream = on_open_stream
        self._notifier = None
        self._pending_streams: dict[str, Livestream] = {}
        self._notification_log: list[dict] = []
        self._init_backend()

    def _init_backend(self) -> None:
        """Initialize the notification backend based on settings."""
        backend = self.settings.backend

        if backend == "auto":
            # Try desktop-notifier first (handles most cases)
            try:
                from desktop_notifier import DesktopNotifier

                self._notifier = DesktopNotifier(
                    app_name="Livestream List",
                    app_icon=None,
                )
                self._backend = "desktop-notifier"
                logger.info("Using desktop-notifier backend")
                return
            except Exception as e:
                logger.warning(f"desktop-notifier failed: {e}, trying fallback")

            # Fallback to notify-send
            if shutil.which("notify-send"):
                self._backend = "notify-send"
                logger.info("Using notify-send backend")
                return

            logger.error("No notification backend available")
            self._backend = "none"

        elif backend == "dbus":
            try:
                from desktop_notifier import DesktopNotifier

                self._notifier = DesktopNotifier(
                    app_name="Livestream List",
                    app_icon=None,
                )
                self._backend = "desktop-notifier"
                logger.info("Using D-Bus (desktop-notifier) backend")
            except Exception as e:
                logger.error(f"D-Bus backend failed: {e}")
                self._backend = "none"

        elif backend == "notify-send":
            if shutil.which("notify-send"):
                self._backend = "notify-send"
                logger.info("Using notify-send backend")
            else:
                logger.error("notify-send not found")
                self._backend = "none"

        else:
            logger.warning(f"Unknown backend: {backend}, using auto")
            self.settings.backend = "auto"
            self._init_backend()

    def _is_quiet_hours(self) -> bool:
        """Check if current time falls within quiet hours."""
        if not self.settings.quiet_hours_enabled:
            return False
        try:
            now = datetime.now().strftime("%H:%M")
            start = self.settings.quiet_hours_start
            end = self.settings.quiet_hours_end
            if start <= end:
                # Same-day range (e.g., 09:00 to 17:00)
                return start <= now < end
            else:
                # Crosses midnight (e.g., 22:00 to 08:00)
                return now >= start or now < end
        except Exception:
            return False

    def _play_sound(self, sound_path: str | None = None) -> None:
        """Play a notification sound file.

        Args:
            sound_path: Path to a sound file, or None to play the system default.
        """
        try:
            if sound_path and os.path.isfile(sound_path):
                # Custom sound file — play directly
                for player_cmd in [
                    ["paplay", sound_path],
                    ["aplay", sound_path],
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", sound_path],
                ]:
                    if shutil.which(player_cmd[0]):
                        subprocess.Popen(
                            player_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return
            else:
                # Default sound — play freedesktop sound file directly via paplay
                # (canberra-gtk-play returns success but produces no audio from Qt)
                for path in [
                    "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
                    "/usr/share/sounds/freedesktop/stereo/message.oga",
                ]:
                    if os.path.isfile(path) and shutil.which("paplay"):
                        subprocess.Popen(
                            ["paplay", path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        return
        except Exception as e:
            logger.warning(f"Failed to play notification sound: {e}")

    def update_settings(self, settings: NotificationSettings) -> None:
        """Update settings and reinitialize backend if needed."""
        old_backend = self.settings.backend
        self.settings = settings
        if settings.backend != old_backend:
            self._init_backend()

    async def notify_stream_online(self, livestream: Livestream) -> None:
        """Send a notification that a stream is now live."""
        if not self.settings.enabled:
            return

        # Check if channel is excluded
        channel_key = livestream.channel.unique_key
        if channel_key in self.settings.excluded_channels:
            return

        # Check platform filter
        platform_str = livestream.channel.platform.value
        if platform_str not in self.settings.platform_filter:
            return

        # Check quiet hours
        if self._is_quiet_hours():
            return

        # Build notification content
        title = f"{livestream.display_name} is live!"

        body_parts = []
        if self.settings.show_game and livestream.game:
            body_parts.append(f"Playing: {livestream.game}")
        if self.settings.show_title and livestream.title:
            body_parts.append(livestream.title)

        body = "\n".join(body_parts) if body_parts else "Stream is now live"

        await self._send_notification(title, body, channel_key, livestream)

    async def send_test_notification(self) -> bool:
        """Send a test notification. Returns True if successful."""
        title = "Livestream List"
        body = "Test notification - notifications are working!"
        return await self._send_notification(title, body)

    async def _send_notification(
        self,
        title: str,
        body: str,
        channel_key: str | None = None,
        livestream: Livestream | None = None,
    ) -> bool:
        """Send a notification using the configured backend."""
        if self._backend == "none":
            logger.warning("No notification backend available")
            return False

        if channel_key and livestream:
            self._pending_streams[channel_key] = livestream

        try:
            if self._backend == "desktop-notifier" and self._notifier:
                from desktop_notifier import Button, Sound, Urgency

                buttons = []
                if self.on_open_stream and channel_key:
                    buttons.append(
                        Button(
                            title="Watch",
                            on_pressed=lambda key=channel_key: self._handle_watch(key),
                        )
                    )

                # Map urgency setting
                urgency_map = {
                    "low": Urgency.Low,
                    "normal": Urgency.Normal,
                    "critical": Urgency.Critical,
                }
                urgency = urgency_map.get(self.settings.urgency, Urgency.Normal)

                await self._notifier.send(
                    title=title,
                    message=body,
                    urgency=urgency,
                    buttons=buttons,  # Pass empty list, not None
                    sound=Sound(name="default") if self.settings.sound_enabled else None,
                )

                # Play custom sound if set
                if self.settings.custom_sound_path:
                    self._play_sound(self.settings.custom_sound_path)

                return True

            elif self._backend == "notify-send":
                cmd = ["notify-send", title, body]
                if self.settings.sound_enabled:
                    cmd.extend(["--hint", "int:sound-file:default"])
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            return False

        return False

    def _handle_watch(self, channel_key: str) -> None:
        """Handle watch button click."""
        if self.on_open_stream and channel_key in self._pending_streams:
            livestream = self._pending_streams[channel_key]
            try:
                self.on_open_stream(livestream)
            except Exception as e:
                logger.error(f"Error handling watch callback: {e}")

    def send_notification_sync(self, livestream: Livestream, is_test: bool = False) -> None:
        """Send notification synchronously using notify-send.

        This method is thread-safe and can be called from background threads.
        It uses subprocess-based notification to avoid event loop issues.

        Args:
            livestream: The livestream to notify about.
            is_test: If True, bypasses enabled check and exclusion list.
        """
        # Skip enabled check for test notifications
        if not is_test and not self.settings.enabled:
            return

        # Check if channel is excluded (skip for test)
        channel_key = livestream.channel.unique_key
        if not is_test and channel_key in self.settings.excluded_channels:
            return

        # Check platform filter (skip for test)
        if not is_test:
            platform_str = livestream.channel.platform.value
            if platform_str not in self.settings.platform_filter:
                return

        # Check quiet hours (skip for test)
        if not is_test and self._is_quiet_hours():
            return

        # Store for Watch button callback
        self._pending_streams[channel_key] = livestream

        # Build notification content
        title = f"{livestream.display_name} is live!"

        body_parts = []
        if self.settings.show_game and livestream.game:
            body_parts.append(f"Playing: {livestream.game}")
        if self.settings.show_title and livestream.title:
            body_parts.append(livestream.title)

        body = "\n".join(body_parts) if body_parts else "Stream is now live"

        # Check if running in flatpak
        is_flatpak = os.path.exists("/.flatpak-info")

        # Use notify-send (via flatpak-spawn if in sandbox)
        if is_flatpak:
            cmd = [
                "flatpak-spawn",
                "--host",
                "notify-send",
                title,
                body,
                "--app-name=Livestream List (Qt)",
            ]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", title, body, "--app-name=Livestream List (Qt)"]
        else:
            return

        # Urgency (only add when non-default)
        if self.settings.urgency in ("low", "critical"):
            cmd.extend(["--urgency", self.settings.urgency])

        # Timeout (notify-send uses milliseconds)
        if self.settings.timeout_seconds > 0:
            cmd.extend(["--expire-time", str(self.settings.timeout_seconds * 1000)])

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Play sound directly (notify-send hints don't work on all DEs)
        if self.settings.sound_enabled or is_test:
            self._play_sound(self.settings.custom_sound_path or None)

        if not is_test:
            self._log_notification(livestream)

    def send_raid_notification_sync(
        self, channel_name: str, raider_name: str, viewer_count: int
    ) -> None:
        """Send a raid notification synchronously.

        Args:
            channel_name: Display name of the channel being raided.
            raider_name: Display name of the raider.
            viewer_count: Number of viewers in the raid.
        """
        if not self.settings.raid_notifications_enabled:
            return

        if self._is_quiet_hours():
            return

        title = f"Raid on {channel_name}!"
        body = f"{raider_name} raided with {viewer_count:,} viewers"

        is_flatpak = os.path.exists("/.flatpak-info")
        if is_flatpak:
            cmd = [
                "flatpak-spawn",
                "--host",
                "notify-send",
                title,
                body,
                "--app-name=Livestream List (Qt)",
            ]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", title, body, "--app-name=Livestream List (Qt)"]
        else:
            return

        if self.settings.urgency in ("low", "critical"):
            cmd.extend(["--urgency", self.settings.urgency])

        if self.settings.timeout_seconds > 0:
            cmd.extend(["--expire-time", str(self.settings.timeout_seconds * 1000)])

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if self.settings.sound_enabled:
            self._play_sound(self.settings.custom_sound_path or None)

    def _play_mention_sound(self) -> None:
        """Play a distinct sound for @mention notifications.

        Uses bell.oga (different from stream-live's message-new-instant.oga),
        or the user's custom mention sound path if configured.
        """
        custom = self.settings.mention_custom_sound_path
        if custom and os.path.isfile(custom):
            self._play_sound(custom)
            return
        # Default: freedesktop bell sound
        for path in [
            "/usr/share/sounds/freedesktop/stereo/bell.oga",
            "/usr/share/sounds/freedesktop/stereo/complete.oga",
        ]:
            if os.path.isfile(path) and shutil.which("paplay"):
                subprocess.Popen(
                    ["paplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return

    def send_mention_notification_sync(
        self, channel_name: str, sender_name: str, text_preview: str
    ) -> None:
        """Send a desktop notification for an @mention.

        Args:
            channel_name: Display name of the channel.
            sender_name: Display name of the person who mentioned us.
            text_preview: Preview of the message text (truncated).
        """
        if not self.settings.mention_notifications_enabled:
            return

        if self._is_quiet_hours():
            return

        title = f"@mention in {channel_name}"
        body = f"{sender_name}: {text_preview[:100]}"

        is_flatpak = os.path.exists("/.flatpak-info")
        if is_flatpak:
            cmd = [
                "flatpak-spawn",
                "--host",
                "notify-send",
                title,
                body,
                "--app-name=Livestream List (Qt)",
            ]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", title, body, "--app-name=Livestream List (Qt)"]
        else:
            return

        if self.settings.urgency in ("low", "critical"):
            cmd.extend(["--urgency", self.settings.urgency])

        if self.settings.timeout_seconds > 0:
            cmd.extend(["--expire-time", str(self.settings.timeout_seconds * 1000)])

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self._play_mention_sound()

    def test_mention_notification_sync(self) -> None:
        """Send a test @mention notification (bypasses enabled check)."""
        title = "@mention in Test Channel"
        body = "TestUser: hey @you check this out!"

        is_flatpak = os.path.exists("/.flatpak-info")
        if is_flatpak:
            cmd = [
                "flatpak-spawn",
                "--host",
                "notify-send",
                title,
                body,
                "--app-name=Livestream List (Qt)",
            ]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", title, body, "--app-name=Livestream List (Qt)"]
        else:
            # No notify-send, still play the sound
            self._play_mention_sound()
            return

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self._play_mention_sound()

    def _log_notification(self, livestream: Livestream) -> None:
        """Record a notification in the in-memory log (max 50 entries)."""
        entry = {
            "channel_key": livestream.channel.unique_key,
            "display_name": livestream.display_name,
            "platform": livestream.channel.platform.value,
            "title": livestream.title or "",
            "game": livestream.game or "",
            "timestamp": datetime.now(timezone.utc),
        }
        self._notification_log.append(entry)
        if len(self._notification_log) > 50:
            self._notification_log = self._notification_log[-50:]

    @property
    def notification_log(self) -> list[dict]:
        """Read-only access to the notification log (most recent last)."""
        return list(self._notification_log)

    async def clear_all(self) -> None:
        """Clear all pending notifications."""
        try:
            if self._backend == "desktop-notifier" and self._notifier:
                await self._notifier.clear_all()
            self._pending_streams.clear()
        except Exception as e:
            logger.error(f"Failed to clear notifications: {e}")

    @property
    def backend_name(self) -> str:
        """Get a human-readable name for the current backend."""
        if self._backend == "desktop-notifier":
            return "D-Bus (desktop-notifier)"
        elif self._backend == "notify-send":
            return "notify-send"
        return "None"
