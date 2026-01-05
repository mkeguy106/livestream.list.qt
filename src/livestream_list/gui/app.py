"""Main GTK application."""

import asyncio
import logging
import signal
import sys
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

from ..__version__ import __version__
from ..core.settings import Settings
from ..core.monitor import StreamMonitor
from ..core.streamlink import StreamlinkLauncher
from ..notifications.notifier import Notifier
from .main_window import MainWindow
from .tray import TrayIcon, is_tray_available

logger = logging.getLogger(__name__)


class Application(Adw.Application):
    """Main application class."""

    def __init__(self) -> None:
        super().__init__(
            application_id="life.covert.livestreamList",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

        self.settings: Optional[Settings] = None
        self.monitor: Optional[StreamMonitor] = None
        self.notifier: Optional[Notifier] = None
        self.streamlink: Optional[StreamlinkLauncher] = None
        self.main_window: Optional[MainWindow] = None
        self.tray_icon: Optional[TrayIcon] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_notifications: list = []
        self._refresh_timer_id: Optional[int] = None

    def do_startup(self) -> None:
        """Handle application startup."""
        Adw.Application.do_startup(self)

        # Load settings
        self.settings = Settings.load()

        # Initialize components
        self.monitor = StreamMonitor(self.settings)
        self.streamlink = StreamlinkLauncher(self.settings.streamlink)
        self.notifier = Notifier(
            self.settings.notifications,
            on_open_stream=self._on_notification_open_stream,
        )

        # Set up monitor callbacks
        self.monitor.on_stream_online(self._on_stream_online)

        # Create system tray icon if available
        if is_tray_available():
            self.tray_icon = TrayIcon(
                on_open=self._on_tray_open,
                on_quit=self._on_tray_quit,
                get_notifications_enabled=lambda: self.settings.notifications.enabled,
                set_notifications_enabled=self._on_tray_notifications_toggled,
            )

        # Create actions
        self._create_actions()

    def do_activate(self) -> None:
        """Handle application activation."""
        if not self.main_window:
            self.main_window = MainWindow(application=self)

        # Present and try to focus the window
        self.main_window.present()

        # Try to raise/focus the window after a short delay
        def focus_window():
            if self.main_window:
                self.main_window.present()
                # For X11/Wayland, try to grab focus
                surface = self.main_window.get_surface()
                if surface:
                    try:
                        from gi.repository import Gdk
                        display = Gdk.Display.get_default()
                        if display:
                            # Request focus via toplevel
                            toplevel = surface
                            if hasattr(toplevel, 'focus'):
                                toplevel.focus(Gdk.CURRENT_TIME)
                    except Exception:
                        pass
            return False

        GLib.timeout_add(100, focus_window)

        # Start the async event loop integration
        GLib.idle_add(self._start_async_loop)

    def _create_actions(self) -> None:
        """Create application actions."""
        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self._on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        # Refresh action
        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", self._on_refresh)
        self.add_action(refresh_action)
        self.set_accels_for_action("app.refresh", ["<primary>r", "F5"])

        # Add channel action
        add_action = Gio.SimpleAction.new("add-channel", None)
        add_action.connect("activate", self._on_add_channel)
        self.add_action(add_action)
        self.set_accels_for_action("app.add-channel", ["<primary>n"])

        # Settings action
        settings_action = Gio.SimpleAction.new("preferences", None)
        settings_action.connect("activate", self._on_preferences)
        self.add_action(settings_action)
        self.set_accels_for_action("app.preferences", ["<primary>comma"])

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        # Check for updates action
        update_action = Gio.SimpleAction.new("check-for-updates", None)
        update_action.connect("activate", self._on_check_for_updates)
        self.add_action(update_action)

        # Import follows action
        import_action = Gio.SimpleAction.new("import-follows", None)
        import_action.connect("activate", self._on_import_follows)
        self.add_action(import_action)

    def _start_async_loop(self) -> bool:
        """Start the async event loop integration."""
        self._loop = asyncio.new_event_loop()
        self._init_complete = False

        # Set up periodic async processing first
        GLib.timeout_add(100, self._process_async)

        # Run initialization in a background thread
        def init_thread():
            try:
                asyncio.set_event_loop(self._loop)

                async def init():
                    # Load saved channels first
                    await self.monitor._load_channels()
                    channel_count = len(self.monitor.channels)

                    # Mark init complete so we can process async tasks
                    self._init_complete = True

                    # Show channels immediately (as offline) - hide loading, show list
                    def show_channels_now():
                        if self.main_window:
                            self.main_window.set_loading_complete()
                            if channel_count > 0:
                                self.main_window.set_status("Updating stream status...")
                        return False

                    GLib.idle_add(show_channels_now)

                    if channel_count > 0:
                        # Reset sessions for this event loop (Python 3.11 compatibility)
                        for client in self.monitor._clients.values():
                            client._session = None

                        # Authorize API clients in background
                        from ..core.models import StreamPlatform
                        for platform, client in self.monitor._clients.items():
                            if self.monitor._has_channels_for_platform(platform):
                                if not await client.is_authorized():
                                    try:
                                        await client.authorize()
                                    except Exception as e:
                                        logger.error(f"Failed to authorize {client.name}: {e}")

                        # Refresh streams in background
                        await self._refresh_with_progress(channel_count)

                        # Close sessions to avoid warnings when loop closes
                        for client in self.monitor._clients.values():
                            await client.close()
                            client._session = None

                        # Mark initial check complete and update UI
                        def update_after_check():
                            if self.main_window:
                                self.main_window._initial_check_complete = True
                                self.main_window.refresh_stream_list()
                            return False

                        GLib.idle_add(update_after_check)

                    # Mark initial load complete for notifications
                    self.monitor._initial_load_complete = True

                    # Start the refresh timer on main thread
                    GLib.idle_add(self._start_refresh_timer)

                    GLib.idle_add(
                        lambda: self.main_window.set_status("Ready")
                        if self.main_window else None
                    )

                self._loop.run_until_complete(init())
            except Exception as e:
                logger.error(f"Initialization error: {e}")
                import traceback
                traceback.print_exc()
                # Still mark as complete so app doesn't hang
                self._init_complete = True
                GLib.idle_add(
                    lambda: self.main_window.set_loading_complete() if self.main_window else None
                )

        thread = threading.Thread(target=init_thread, daemon=True)
        thread.start()

        return False  # Don't repeat

    async def _refresh_with_progress(self, total_channels: int) -> None:
        """Refresh streams with progress updates."""
        if not self.monitor._channels:
            return

        # Group channels by platform
        from ..core.models import StreamPlatform
        by_platform: dict[StreamPlatform, list] = {}
        for channel in self.monitor._channels.values():
            if channel.platform not in by_platform:
                by_platform[channel.platform] = []
            by_platform[channel.platform].append(channel)

        processed = 0
        for platform, channels in by_platform.items():
            client = self.monitor._clients[platform]

            # Update progress
            GLib.idle_add(
                lambda p=processed, t=total_channels, plat=platform.value:
                self.main_window.set_loading_status(
                    f"Checking {plat.title()}...",
                    f"{p} / {t} channels"
                ) if self.main_window else None
            )

            try:
                livestreams = await client.get_livestreams(channels)
                for livestream in livestreams:
                    key = livestream.channel.unique_key
                    self.monitor._livestreams[key] = livestream
                    processed += 1

                    # Update progress every 5 channels
                    if processed % 5 == 0:
                        GLib.idle_add(
                            lambda p=processed, t=total_channels:
                            self.main_window.set_loading_status(
                                "Checking stream status...",
                                f"{p} / {t} channels"
                            ) if self.main_window else None
                        )
            except Exception as e:
                logger.error(f"Error refreshing {platform}: {e}")
                processed += len(channels)

    def _process_async(self) -> bool:
        """Process pending async tasks."""
        # Only process if initialization is complete (loop is available)
        if self._loop and getattr(self, '_init_complete', False):
            # Process any pending notifications
            if self._pending_notifications and self.notifier:
                pending = self._pending_notifications[:]
                self._pending_notifications.clear()

                async def send_notifications():
                    for livestream in pending:
                        try:
                            await self.notifier.notify_stream_online(livestream)
                        except Exception as e:
                            logger.error(f"Notification error: {e}")

                try:
                    self._loop.run_until_complete(send_notifications())
                except RuntimeError:
                    # Loop might be busy, re-queue notifications
                    self._pending_notifications.extend(pending)

        return True  # Keep repeating

    def _on_stream_online(self, livestream) -> None:
        """Handle stream going online."""
        # Queue notification to be sent asynchronously (avoid nested run_until_complete)
        if self.notifier:
            self._pending_notifications.append(livestream)

        # Update UI
        if self.main_window:
            GLib.idle_add(self.main_window.refresh_stream_list)

    def _on_notification_open_stream(self, livestream) -> None:
        """Handle opening stream from notification."""
        # Use GLib.idle_add since this may be called from notification callback thread
        def launch_from_notification():
            if self.main_window:
                # Use main window's play stream method for full UI feedback
                self.main_window._on_play_stream(livestream)
            elif self.streamlink:
                # Fallback if window not available
                self.streamlink.launch(livestream)
        GLib.idle_add(launch_from_notification)

    def _on_tray_open(self) -> None:
        """Handle Open from tray menu."""
        if self.main_window:
            self.main_window.set_visible(True)
            self.main_window.present()

    def _on_tray_quit(self) -> None:
        """Handle Quit from tray menu."""
        self._cleanup()
        self.quit()

    def _on_tray_notifications_toggled(self, enabled: bool) -> None:
        """Handle notifications toggle from tray menu."""
        if self.settings:
            self.settings.notifications.enabled = enabled
            self.settings.save()

    def _on_quit(self, action, param) -> None:
        """Handle quit action."""
        self._cleanup()
        self.quit()

    def _on_refresh(self, action, param) -> None:
        """Handle refresh action."""
        if self.monitor and self._loop:
            # Show refreshing status
            if self.main_window:
                self.main_window.set_status("Refreshing...")

            # Run refresh in background thread to avoid freezing UI
            import threading

            def refresh_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # Reset sessions for this thread's loop
                    for client in self.monitor._clients.values():
                        client._session = None

                    loop.run_until_complete(self.monitor.refresh())

                    # Close sessions before closing loop to avoid warnings
                    async def close_sessions():
                        for client in self.monitor._clients.values():
                            await client.close()
                    loop.run_until_complete(close_sessions())

                    # Update UI on main thread
                    GLib.idle_add(self._on_refresh_complete)
                except Exception as e:
                    logger.error(f"Refresh error: {e}")
                    GLib.idle_add(lambda: self.main_window.set_status("Refresh failed") if self.main_window else None)
                finally:
                    for client in self.monitor._clients.values():
                        client._session = None
                    loop.close()

            thread = threading.Thread(target=refresh_thread, daemon=True)
            thread.start()

    def _on_refresh_complete(self) -> bool:
        """Called when refresh completes."""
        if self.main_window:
            self.main_window.refresh_stream_list()
            self.main_window.set_status("Ready")
        return False

    def _start_refresh_timer(self) -> bool:
        """Start the automatic refresh timer."""
        if self._refresh_timer_id:
            GLib.source_remove(self._refresh_timer_id)

        # Convert seconds to milliseconds for GLib.timeout_add
        interval_ms = self.settings.refresh_interval * 1000
        self._refresh_timer_id = GLib.timeout_add(interval_ms, self._on_timed_refresh)
        return False  # Don't repeat (this is called via idle_add)

    def _on_timed_refresh(self) -> bool:
        """Handle timed refresh - runs in background thread."""
        logger.info("Timed refresh triggered")
        if not self.monitor:
            return True  # Keep timer running

        def refresh_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # Reset sessions for this thread's loop
                for client in self.monitor._clients.values():
                    client._session = None

                loop.run_until_complete(self.monitor.refresh())

                # Close sessions before closing loop to avoid warnings
                async def close_sessions():
                    for client in self.monitor._clients.values():
                        await client.close()
                loop.run_until_complete(close_sessions())

                # Update UI on main thread
                GLib.idle_add(self._on_refresh_complete)
            except Exception as e:
                logger.error(f"Timed refresh error: {e}")
            finally:
                for client in self.monitor._clients.values():
                    client._session = None
                loop.close()

        thread = threading.Thread(target=refresh_thread, daemon=True)
        thread.start()

        return True  # Keep timer running

    def _on_add_channel(self, action, param) -> None:
        """Handle add channel action."""
        if self.main_window:
            self.main_window.show_add_channel_dialog()

    def _on_preferences(self, action, param) -> None:
        """Handle preferences action."""
        if self.main_window:
            self.main_window.show_preferences_dialog()

    def _on_about(self, action, param) -> None:
        """Handle about action."""
        # Create custom About dialog with Check for Updates button
        about = AboutDialog(self.main_window, self)
        about.present()

    def _on_check_for_updates(self, action, param) -> None:
        """Handle check for updates action."""
        self.check_for_updates()

    def check_for_updates(self) -> None:
        """Check GitHub for updates and show result dialog."""
        import threading
        import urllib.request
        import json
        from urllib.error import HTTPError, URLError

        def check_thread():
            try:
                url = "https://api.github.com/repos/mkeguy106/livestream-list-linux/releases/latest"
                req = urllib.request.Request(url, headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "LivestreamList",
                })
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    latest_version = data.get("tag_name", "").lstrip("v")
                    release_url = data.get("html_url", "")

                    def show_result():
                        if latest_version == __version__:
                            self._show_update_dialog(
                                "Up to Date",
                                f"You are running the latest version (v{__version__}).",
                                None
                            )
                        else:
                            self._show_update_dialog(
                                "Update Available",
                                f"A new version is available: v{latest_version}\n\nYou are running v{__version__}.",
                                release_url
                            )
                        return False

                    GLib.idle_add(show_result)

            except HTTPError as e:
                if e.code == 404:
                    def show_no_releases():
                        self._show_update_dialog(
                            "No Releases Found",
                            f"No releases found yet.\n\nYou are running v{__version__}.",
                            None
                        )
                        return False
                    GLib.idle_add(show_no_releases)
                else:
                    def show_http_error():
                        self._show_update_dialog(
                            "Check Failed",
                            f"Could not check for updates.\n\nHTTP Error: {e.code}",
                            None
                        )
                        return False
                    GLib.idle_add(show_http_error)

            except URLError as e:
                def show_network_error():
                    self._show_update_dialog(
                        "Network Error",
                        f"Could not connect to GitHub.\n\nPlease check your internet connection.",
                        None
                    )
                    return False
                GLib.idle_add(show_network_error)

            except Exception as e:
                error_msg = str(e) if str(e) else type(e).__name__
                logger.error(f"Update check failed: {error_msg}")

                def show_error():
                    self._show_update_dialog(
                        "Check Failed",
                        f"Could not check for updates.\n\nError: {error_msg}",
                        None
                    )
                    return False

                GLib.idle_add(show_error)

        thread = threading.Thread(target=check_thread, daemon=True)
        thread.start()

    def _show_update_dialog(self, title: str, message: str, release_url: str = None) -> None:
        """Show update check result dialog."""
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading=title,
            body=message,
        )

        if release_url:
            dialog.add_response("visit", "Visit Download Page")
            dialog.set_response_appearance("visit", Adw.ResponseAppearance.SUGGESTED)
            dialog.add_response("close", "Close")
            dialog.set_default_response("visit")
        else:
            dialog.add_response("close", "OK")
            dialog.set_default_response("close")

        def on_response(dialog, response):
            if response == "visit" and release_url:
                import webbrowser
                webbrowser.open(release_url)

        dialog.connect("response", on_response)
        dialog.present()

    def _on_import_follows(self, action, param) -> None:
        """Handle import follows action."""
        if self.main_window:
            self.main_window.show_import_follows_dialog()

    def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop refresh timer
        if self._refresh_timer_id:
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = None

        # Clean up tray icon
        if self.tray_icon:
            self.tray_icon.destroy()
            self.tray_icon = None

        if self.settings:
            self.settings.save()

        if self._loop:
            self._loop.close()


class AboutDialog(Adw.Window):
    """Custom About dialog with Check for Updates button."""

    def __init__(self, parent, app: Application) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="About Livestream List",
            default_width=400,
            default_height=500,
        )

        self.app = app

        # Main content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)

        # Header bar
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        content.append(header)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content.append(scrolled)

        # Main box
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        scrolled.set_child(main_box)

        # App icon
        icon = Gtk.Image.new_from_icon_name("life.covert.livestreamList")
        icon.set_pixel_size(128)
        main_box.append(icon)

        # App name
        name_label = Gtk.Label(label="Livestream List")
        name_label.add_css_class("title-1")
        main_box.append(name_label)

        # Version
        version_label = Gtk.Label(label=f"Version {__version__}")
        version_label.add_css_class("dim-label")
        main_box.append(version_label)

        # Description
        desc_label = Gtk.Label(
            label="Monitor your favorite livestreams on Twitch, YouTube, and Kick."
        )
        desc_label.set_wrap(True)
        desc_label.set_justify(Gtk.Justification.CENTER)
        main_box.append(desc_label)

        # Check for Updates button
        update_btn = Gtk.Button(label="Check for Updates")
        update_btn.add_css_class("suggested-action")
        update_btn.add_css_class("pill")
        update_btn.set_halign(Gtk.Align.CENTER)
        update_btn.connect("clicked", self._on_check_updates_clicked)
        main_box.append(update_btn)

        # Links
        links_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        links_box.set_halign(Gtk.Align.CENTER)
        main_box.append(links_box)

        website_btn = Gtk.Button(label="Website")
        website_btn.add_css_class("flat")
        website_btn.connect("clicked", lambda b: self._open_url("https://github.com/mkeguy106/livestream-list-linux"))
        links_box.append(website_btn)

        issues_btn = Gtk.Button(label="Report Issue")
        issues_btn.add_css_class("flat")
        issues_btn.connect("clicked", lambda b: self._open_url("https://github.com/mkeguy106/livestream-list-linux/issues"))
        links_box.append(issues_btn)

        # Credits section
        credits_group = Adw.PreferencesGroup(title="Bundled Dependencies")
        credits_group.add_css_class("boxed-list")

        for dep in ["yt-dlp", "aiohttp", "desktop-notifier"]:
            row = Adw.ActionRow(title=dep)
            credits_group.add(row)

        main_box.append(credits_group)

        # Copyright
        copyright_label = Gtk.Label(label="Copyright © 2024 Livestream List Contributors")
        copyright_label.add_css_class("caption")
        copyright_label.add_css_class("dim-label")
        main_box.append(copyright_label)

        # License
        license_label = Gtk.Label(label="Licensed under GPL-2.0")
        license_label.add_css_class("caption")
        license_label.add_css_class("dim-label")
        main_box.append(license_label)

    def _on_check_updates_clicked(self, button: Gtk.Button) -> None:
        """Handle check for updates button click."""
        self.app.check_for_updates()

    def _open_url(self, url: str) -> None:
        """Open a URL in the browser."""
        import webbrowser
        webbrowser.open(url)


def run() -> int:
    """Run the application."""
    app = Application()
    return app.run(sys.argv)
