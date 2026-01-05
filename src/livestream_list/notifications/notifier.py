"""Desktop notification handler."""

import asyncio
import logging
import shutil
import subprocess
from typing import Callable, Optional

from ..core.models import Livestream
from ..core.settings import NotificationSettings

logger = logging.getLogger(__name__)


class Notifier:
    """Handles desktop notifications for stream events."""

    def __init__(
        self,
        settings: NotificationSettings,
        on_open_stream: Optional[Callable[[Livestream], None]] = None,
    ) -> None:
        self.settings = settings
        self.on_open_stream = on_open_stream
        self._notifier = None
        self._pending_streams: dict[str, Livestream] = {}
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
        channel_key: Optional[str] = None,
        livestream: Optional[Livestream] = None,
    ) -> bool:
        """Send a notification using the configured backend."""
        if self._backend == "none":
            logger.warning("No notification backend available")
            return False

        if channel_key and livestream:
            self._pending_streams[channel_key] = livestream

        try:
            if self._backend == "desktop-notifier" and self._notifier:
                from desktop_notifier import Urgency, Button, Sound

                buttons = []
                if self.on_open_stream and channel_key:
                    buttons.append(
                        Button(
                            title="Watch",
                            on_pressed=lambda key=channel_key: self._handle_watch(key),
                        )
                    )

                await self._notifier.send(
                    title=title,
                    message=body,
                    urgency=Urgency.Normal,
                    buttons=buttons,  # Pass empty list, not None
                    sound=Sound(name="default") if self.settings.sound_enabled else None,
                )
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
