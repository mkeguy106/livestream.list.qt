"""Main application window."""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, GObject, Pango

from ..core.models import Livestream, StreamPlatform
from ..__version__ import __version__
from ..core.streamlink import open_in_browser, open_chat_in_browser
from ..core.chat import ChatLauncher

if TYPE_CHECKING:
    from .app import Application

# Export schema version - increment when export format changes
EXPORT_SCHEMA_VERSION = 1


def parse_channel_url(text: str) -> Optional[tuple[str, StreamPlatform]]:
    """
    Parse a channel URL and return (channel_name, platform).

    Supports:
    - https://twitch.tv/username
    - https://www.twitch.tv/username
    - https://kick.com/username
    - https://www.kick.com/username
    - https://youtube.com/@username
    - https://www.youtube.com/@username
    - https://youtube.com/channel/CHANNEL_ID
    - https://youtube.com/c/username

    Returns None if the text is not a recognized URL.
    """
    text = text.strip()

    # Twitch URL patterns
    twitch_patterns = [
        r'^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)/?$',
        r'^(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)/.*$',
    ]
    for pattern in twitch_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return (match.group(1), StreamPlatform.TWITCH)

    # Kick URL patterns
    kick_patterns = [
        r'^(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/?$',
        r'^(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/.*$',
    ]
    for pattern in kick_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return (match.group(1), StreamPlatform.KICK)

    # YouTube URL patterns
    youtube_patterns = [
        r'^(?:https?://)?(?:www\.)?youtube\.com/@([a-zA-Z0-9_-]+)/?$',
        r'^(?:https?://)?(?:www\.)?youtube\.com/c/([a-zA-Z0-9_-]+)/?$',
        r'^(?:https?://)?(?:www\.)?youtube\.com/channel/([a-zA-Z0-9_-]+)/?$',
        r'^(?:https?://)?(?:www\.)?youtube\.com/user/([a-zA-Z0-9_-]+)/?$',
        # Handle youtube.com/username (without @, /c/, /user/, /channel/)
        r'^(?:https?://)?(?:www\.)?youtube\.com/([a-zA-Z0-9_-]+)/?$',
    ]
    for pattern in youtube_patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            channel = match.group(1)
            # For plain username URLs, prefix with @ for consistency
            if not channel.startswith("@") and not channel.startswith("UC"):
                channel = f"@{channel}"
            return (channel, StreamPlatform.YOUTUBE)

    return None


class StreamRow(Gtk.Box):
    """A row displaying a livestream."""

    # Platform colors
    PLATFORM_COLORS = {
        StreamPlatform.TWITCH: "#9146FF",   # Twitch purple
        StreamPlatform.KICK: "#53FC18",     # Kick green
        StreamPlatform.YOUTUBE: "#FF0000",  # YouTube red
    }

    def __init__(self, livestream: Livestream, show_checkbox: bool = False, is_playing: bool = False, on_stop: callable = None, on_favorite: callable = None, on_chat: callable = None, on_channel: callable = None, on_play: callable = None, ui_style: int = 0, platform_colors: bool = True, show_platform: bool = True, show_play: bool = True, show_favorite: bool = True, show_chat: bool = True, show_browser: bool = True, show_live_duration: bool = True, show_viewers: bool = True) -> None:
        # Style settings: 0=Default, 1=Compact 1, 2=Compact 2, 3=Compact 3
        if ui_style == 3:
            margin_v, margin_h, spacing = 1, 4, 2
        elif ui_style == 2:
            margin_v, margin_h, spacing = 2, 6, 4
        elif ui_style == 1:
            margin_v, margin_h, spacing = 4, 12, 8
        else:
            margin_v, margin_h, spacing = 4, 12, 10
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=spacing,
            margin_top=margin_v,
            margin_bottom=margin_v,
            margin_start=margin_h,
            margin_end=margin_h,
        )
        self._ui_style = ui_style
        self._platform_colors = platform_colors
        self._show_play = show_play
        self._show_live_duration = show_live_duration
        self._show_viewers = show_viewers

        self.livestream = livestream
        self._is_playing = is_playing
        self._on_stop = on_stop
        self._on_favorite = on_favorite
        self._on_chat = on_chat
        self._on_channel = on_channel
        self._on_play = on_play

        # Selection checkbox
        self.checkbox = Gtk.CheckButton()
        self.checkbox.set_visible(show_checkbox)
        self.append(self.checkbox)

        # Live indicator
        self.live_indicator = Gtk.Label()
        self.live_indicator.set_width_chars(3)
        self.live_indicator.add_css_class("caption")
        self.append(self.live_indicator)

        # Platform icon with optional color
        self.platform_label = Gtk.Label()
        self.platform_label.set_width_chars(2)
        self.platform_label.set_tooltip_text(livestream.channel.platform.value.title())
        self.platform_label.set_visible(show_platform)
        self._update_platform_label()
        self.append(self.platform_label)

        # Main content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.set_hexpand(True)

        # Channel name row
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.name_label = Gtk.Label()
        self.name_label.set_halign(Gtk.Align.START)
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.name_label.add_css_class("heading")
        self._update_name_label()
        name_row.append(self.name_label)

        # Last seen label (shown to right of name for offline channels)
        self.last_seen_label = Gtk.Label()
        self.last_seen_label.set_halign(Gtk.Align.START)
        self.last_seen_label.add_css_class("caption")
        self.last_seen_label.add_css_class("dim-label")
        self.last_seen_label.set_visible(False)
        name_row.append(self.last_seen_label)

        # Live duration label (shown to right of name for live channels)
        self.live_duration_label = Gtk.Label()
        self.live_duration_label.set_halign(Gtk.Align.START)
        self.live_duration_label.add_css_class("caption")
        self.live_duration_label.add_css_class("dim-label")
        self.live_duration_label.set_visible(False)
        name_row.append(self.live_duration_label)

        # Playing indicator
        self.playing_label = Gtk.Label(label="▶ Playing")
        self.playing_label.add_css_class("caption")
        self.playing_label.set_visible(is_playing)
        name_row.append(self.playing_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        name_row.append(spacer)

        self.viewers_label = Gtk.Label()
        self.viewers_label.set_halign(Gtk.Align.END)
        self.viewers_label.add_css_class("caption")
        self.viewers_label.add_css_class("dim-label")
        name_row.append(self.viewers_label)

        # Channel browser button (rightmost = added first in new order)
        self.channel_button = Gtk.Button()
        self.channel_button.add_css_class("flat")
        self.channel_button.set_valign(Gtk.Align.CENTER)
        self.channel_button.set_tooltip_text("Open channel in browser")
        self.channel_button.set_visible(show_browser)
        self.channel_button.connect("clicked", self._on_channel_clicked)
        # Scale button size for compact modes
        if ui_style == 3:
            channel_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
            channel_icon.set_pixel_size(10)
            self.channel_button.set_child(channel_icon)
        elif ui_style == 2:
            channel_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
            channel_icon.set_pixel_size(12)
            self.channel_button.set_child(channel_icon)
        elif ui_style == 1:
            channel_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
            channel_icon.set_pixel_size(14)
            self.channel_button.set_child(channel_icon)
        else:
            self.channel_button.set_icon_name("web-browser-symbolic")
        name_row.append(self.channel_button)

        # Chat button
        self.chat_button = Gtk.Button()
        self.chat_button.add_css_class("flat")
        self.chat_button.set_valign(Gtk.Align.CENTER)
        self.chat_button.set_tooltip_text("Open chat")
        self.chat_button.set_visible(show_chat)
        self.chat_button.connect("clicked", self._on_chat_clicked)
        # Scale button size for compact modes
        if ui_style == 3:
            chat_icon = Gtk.Image.new_from_icon_name("user-available-symbolic")
            chat_icon.set_pixel_size(10)
            self.chat_button.set_child(chat_icon)
        elif ui_style == 2:
            chat_icon = Gtk.Image.new_from_icon_name("user-available-symbolic")
            chat_icon.set_pixel_size(12)
            self.chat_button.set_child(chat_icon)
        elif ui_style == 1:
            chat_icon = Gtk.Image.new_from_icon_name("user-available-symbolic")
            chat_icon.set_pixel_size(14)
            self.chat_button.set_child(chat_icon)
        else:
            self.chat_button.set_icon_name("user-available-symbolic")
        name_row.append(self.chat_button)

        # Favorite button (star)
        self.favorite_button = Gtk.Button()
        self.favorite_button.add_css_class("flat")
        self.favorite_button.set_valign(Gtk.Align.CENTER)
        self.favorite_button.set_visible(show_favorite)
        self.favorite_button.connect("clicked", self._on_favorite_clicked)
        self._update_favorite_icon()
        # Scale button size for compact modes
        if ui_style == 3:
            fav_icon = Gtk.Image.new_from_icon_name(self._get_favorite_icon_name())
            fav_icon.set_pixel_size(10)
            self.favorite_button.set_child(fav_icon)
        elif ui_style == 2:
            fav_icon = Gtk.Image.new_from_icon_name(self._get_favorite_icon_name())
            fav_icon.set_pixel_size(12)
            self.favorite_button.set_child(fav_icon)
        elif ui_style == 1:
            fav_icon = Gtk.Image.new_from_icon_name(self._get_favorite_icon_name())
            fav_icon.set_pixel_size(14)
            self.favorite_button.set_child(fav_icon)
        else:
            self.favorite_button.set_icon_name(self._get_favorite_icon_name())
        name_row.append(self.favorite_button)

        # Play button (launches stream)
        self.play_button = Gtk.Button()
        self.play_button.add_css_class("flat")
        self.play_button.set_valign(Gtk.Align.CENTER)
        self.play_button.set_tooltip_text("Play stream")
        self.play_button.set_visible(show_play and not is_playing)
        self.play_button.connect("clicked", self._on_play_clicked)
        # Scale button size for compact modes
        if ui_style == 3:
            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            play_icon.set_pixel_size(10)
            self.play_button.set_child(play_icon)
        elif ui_style == 2:
            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            play_icon.set_pixel_size(12)
            self.play_button.set_child(play_icon)
        elif ui_style == 1:
            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            play_icon.set_pixel_size(14)
            self.play_button.set_child(play_icon)
        else:
            self.play_button.set_icon_name("media-playback-start-symbolic")
        name_row.append(self.play_button)

        # Stop button (stops playback, replaces play button when playing)
        self.stop_button = Gtk.Button()
        self.stop_button.set_tooltip_text("Stop playback")
        self.stop_button.add_css_class("flat")
        self.stop_button.set_valign(Gtk.Align.CENTER)
        self.stop_button.set_visible(is_playing)
        self.stop_button.connect("clicked", self._on_stop_clicked)
        # Scale button size for compact modes
        if ui_style == 3:
            stop_icon = Gtk.Image.new_from_icon_name("media-playback-stop-symbolic")
            stop_icon.set_pixel_size(10)
            self.stop_button.set_child(stop_icon)
        elif ui_style == 2:
            stop_icon = Gtk.Image.new_from_icon_name("media-playback-stop-symbolic")
            stop_icon.set_pixel_size(12)
            self.stop_button.set_child(stop_icon)
        elif ui_style == 1:
            stop_icon = Gtk.Image.new_from_icon_name("media-playback-stop-symbolic")
            stop_icon.set_pixel_size(14)
            self.stop_button.set_child(stop_icon)
        else:
            self.stop_button.set_icon_name("media-playback-stop-symbolic")
        name_row.append(self.stop_button)

        content_box.append(name_row)

        # Title/Game row (hidden in compact modes)
        self.title_label = Gtk.Label()
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.add_css_class("caption")
        self.title_label.add_css_class("dim-label")
        if ui_style == 0:  # Only show in default mode
            content_box.append(self.title_label)

        self.append(content_box)

        # Update display
        self.update(livestream, is_playing)

    def _get_platform_icon(self) -> str:
        """Get an icon/emoji for the platform."""
        icons = {
            StreamPlatform.TWITCH: "T",
            StreamPlatform.YOUTUBE: "Y",
            StreamPlatform.KICK: "K",
        }
        return icons.get(self.livestream.channel.platform, "?")

    def _update_platform_label(self) -> None:
        """Update platform label with optional color."""
        icon = self._get_platform_icon()
        if self._platform_colors:
            color = self.PLATFORM_COLORS.get(self.livestream.channel.platform, "")
            if color:
                self.platform_label.set_markup(f'<span foreground="{color}" weight="bold">{icon}</span>')
            else:
                self.platform_label.set_label(icon)
        else:
            self.platform_label.set_label(icon)

    def _update_name_label(self) -> None:
        """Update channel name label with optional color."""
        name = GLib.markup_escape_text(self.livestream.display_name)
        if self._platform_colors:
            color = self.PLATFORM_COLORS.get(self.livestream.channel.platform, "")
            if color:
                self.name_label.set_markup(f'<span foreground="{color}">{name}</span>')
            else:
                self.name_label.set_label(self.livestream.display_name)
        else:
            self.name_label.set_label(self.livestream.display_name)

    def _on_stop_clicked(self, button: Gtk.Button) -> None:
        """Handle stop button click."""
        if self._on_stop:
            self._on_stop(self.livestream.channel.unique_key)

    def _get_favorite_icon_name(self) -> str:
        """Get the appropriate star icon based on favorite state."""
        if self.livestream.channel.favorite:
            return "starred-symbolic"
        return "non-starred-symbolic"

    def _update_favorite_icon(self) -> None:
        """Update the favorite button icon and tooltip."""
        icon_name = self._get_favorite_icon_name()
        if self._ui_style == 3:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(10)
            self.favorite_button.set_child(icon)
        elif self._ui_style == 2:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(12)
            self.favorite_button.set_child(icon)
        elif self._ui_style == 1:
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(14)
            self.favorite_button.set_child(icon)
        else:
            self.favorite_button.set_icon_name(icon_name)

        if self.livestream.channel.favorite:
            self.favorite_button.set_tooltip_text("Remove from favorites")
        else:
            self.favorite_button.set_tooltip_text("Add to favorites")

    def _on_favorite_clicked(self, button: Gtk.Button) -> None:
        """Handle favorite button click."""
        if self._on_favorite:
            self._on_favorite(self.livestream.channel.unique_key)

    def _on_chat_clicked(self, button: Gtk.Button) -> None:
        """Handle chat button click."""
        if self._on_chat:
            self._on_chat(
                self.livestream.channel.channel_id,
                self.livestream.channel.platform,
                self.livestream.video_id,
            )

    def _on_channel_clicked(self, button: Gtk.Button) -> None:
        """Handle channel browser button click."""
        if self._on_channel:
            self._on_channel(
                self.livestream.channel.channel_id,
                self.livestream.channel.platform,
            )

    def _on_play_clicked(self, button: Gtk.Button) -> None:
        """Handle play button click."""
        if self._on_play:
            self._on_play(self.livestream)

    def set_favorite(self, is_favorite: bool) -> None:
        """Update the favorite state."""
        self.livestream.channel.favorite = is_favorite
        self._update_favorite_icon()

    def set_playing(self, is_playing: bool) -> None:
        """Update the playing state."""
        self._is_playing = is_playing
        self.playing_label.set_visible(is_playing)
        self.stop_button.set_visible(is_playing)
        # Show play button when not playing (if show_play is enabled)
        self.play_button.set_visible(self._show_play and not is_playing)

    def update(self, livestream: Livestream, is_playing: bool = None) -> None:
        """Update the row with new data."""
        self.livestream = livestream

        if is_playing is not None:
            self.set_playing(is_playing)

        if livestream.live:
            self.live_indicator.set_label("🟢")
            self.live_indicator.set_tooltip_text(f"Live for {livestream.uptime_str}")
            self.viewers_label.set_label(f"{livestream.viewers_str} viewers")
            self.viewers_label.set_visible(self._show_viewers)
            self.last_seen_label.set_visible(False)  # Hide for live channels
            # Show live duration for live channels
            live_duration = livestream.live_duration_str
            if live_duration and self._show_live_duration:
                self.live_duration_label.set_label(live_duration)
                self.live_duration_label.set_visible(True)
            else:
                self.live_duration_label.set_visible(False)

            title_parts = []
            if livestream.game:
                title_parts.append(livestream.game)
            if livestream.title:
                title_parts.append(livestream.title)
            self.title_label.set_label(" - ".join(title_parts) if title_parts else "")
            self.title_label.set_visible(bool(title_parts))
        else:
            self.live_indicator.set_label("⚫")
            self.live_indicator.set_tooltip_text("Offline")
            # Show "last seen" to left of username for offline streams
            last_seen = livestream.last_seen_str
            if last_seen:
                self.last_seen_label.set_label(last_seen)
                self.last_seen_label.set_visible(True)
            else:
                self.last_seen_label.set_visible(False)
            self.live_duration_label.set_visible(False)  # Hide for offline channels
            self.viewers_label.set_visible(False)
            self.title_label.set_label("Offline")
            self.title_label.set_visible(True)


class MainWindow(Adw.ApplicationWindow):
    """Main application window."""

    # Sort options
    SORT_NAME = 0
    SORT_VIEWERS = 1
    SORT_PLAYING = 2
    SORT_LAST_SEEN = 3
    SORT_TIME_LIVE = 4

    def __init__(self, application: "Application") -> None:
        super().__init__(application=application)

        self.app = application
        self._stream_rows: dict[str, StreamRow] = {}
        # Load preferences from settings
        self._hide_offline = application.settings.hide_offline
        self._favorites_only = application.settings.favorites_only
        self._sort_mode = application.settings.sort_mode
        self._selection_mode = False
        self._initial_check_complete = False  # Track if first status check is done
        self._name_filter = ""  # Filter text for channel names
        self._platform_filter = None  # None = All, or StreamPlatform enum

        # Chat launcher
        self._chat_launcher = ChatLauncher(application.settings.chat)

        self.set_title("Livestream List")
        self.set_default_size(
            application.settings.window.width,
            application.settings.window.height,
        )

        # Main layout
        self._build_ui()

        # Setup window actions (for menu)
        self._setup_actions()

        # Apply saved preferences to UI
        self._hide_offline_check.set_active(self._hide_offline)
        self._favorites_check.set_active(self._favorites_only)
        self._sort_dropdown.set_selected(self._sort_mode)

        # Connect signals
        self.connect("close-request", self._on_close_request)

        # Start periodic check for playing streams
        GLib.timeout_add(2000, self._check_playing_streams)

    def _build_ui(self) -> None:
        """Build the UI."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        self._header_bar = Adw.HeaderBar()

        # Menu button
        self._menu_button = Gtk.MenuButton()
        self._menu_button.set_icon_name("open-menu-symbolic")
        self._menu_button.set_menu_model(self._create_menu())
        self._header_bar.pack_end(self._menu_button)

        # Add channel button
        self._add_button = Gtk.Button(icon_name="list-add-symbolic")
        self._add_button.set_tooltip_text("Add Channel")
        self._add_button.connect("clicked", lambda b: self.show_add_channel_dialog())
        self._header_bar.pack_start(self._add_button)

        # Refresh button
        self._refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text("Refresh")
        self._refresh_button.set_action_name("app.refresh")
        self._header_bar.pack_start(self._refresh_button)

        # Selection mode toggle button (for deleting channels)
        self._select_button = Gtk.ToggleButton(icon_name="edit-delete-symbolic")
        self._select_button.set_tooltip_text("Delete Channels")
        self._select_button.connect("toggled", self._on_selection_mode_toggled)
        self._header_bar.pack_start(self._select_button)

        main_box.append(self._header_bar)

        # Selection action bar (hidden by default)
        self._selection_bar = Gtk.ActionBar()
        self._selection_bar.set_visible(False)

        select_all_btn = Gtk.Button(label="Select All")
        select_all_btn.connect("clicked", self._on_select_all)
        self._selection_bar.pack_start(select_all_btn)

        deselect_all_btn = Gtk.Button(label="Deselect All")
        deselect_all_btn.connect("clicked", self._on_deselect_all)
        self._selection_bar.pack_start(deselect_all_btn)

        self._delete_btn = Gtk.Button(label="Delete Selected")
        self._delete_btn.add_css_class("destructive-action")
        self._delete_btn.connect("clicked", self._on_delete_selected)
        self._selection_bar.pack_end(self._delete_btn)

        self._selection_label = Gtk.Label(label="0 selected")
        self._selection_bar.set_center_widget(self._selection_label)

        main_box.append(self._selection_bar)

        # Filter/Sort toolbar
        self._filter_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=12,
            margin_end=12,
            margin_top=8,
            margin_bottom=4,
        )

        # Hide offline toggle (before filter)
        self._hide_offline_check = Gtk.CheckButton(label="Hide Offline")
        self._hide_offline_check.connect("toggled", self._on_hide_offline_toggled)
        self._filter_bar.append(self._hide_offline_check)

        # Favorites filter
        self._favorites_check = Gtk.CheckButton(label="Favorites")
        self._favorites_check.connect("toggled", self._on_favorites_toggled)
        self._filter_bar.append(self._favorites_check)

        # Search/filter entry
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Filter by name...")
        self._search_entry.set_hexpand(True)
        self._search_entry.set_max_width_chars(30)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._filter_bar.append(self._search_entry)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self._filter_bar.append(spacer)

        # Platform filter dropdown
        self._platform_label = Gtk.Label(label="Platform:")
        self._platform_label.add_css_class("dim-label")
        self._filter_bar.append(self._platform_label)

        self._platform_dropdown = Gtk.DropDown.new_from_strings(["All", "Twitch", "YouTube", "Kick"])
        self._platform_dropdown.set_selected(0)  # Default to All
        self._platform_dropdown.connect("notify::selected", self._on_platform_filter_changed)
        self._filter_bar.append(self._platform_dropdown)

        # Sort dropdown
        self._sort_label = Gtk.Label(label="Sort:")
        self._sort_label.add_css_class("dim-label")
        self._filter_bar.append(self._sort_label)

        self._sort_dropdown = Gtk.DropDown.new_from_strings(["Name", "Viewers", "Playing", "Last Seen", "Time Live"])
        self._sort_dropdown.set_selected(1)  # Default to Viewers
        self._sort_dropdown.connect("notify::selected", self._on_sort_changed)
        self._filter_bar.append(self._sort_dropdown)

        main_box.append(self._filter_bar)

        # Loading overlay (shown during initialization)
        self._loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        self._loading_box.set_vexpand(True)

        loading_spinner = Gtk.Spinner()
        loading_spinner.set_spinning(True)
        loading_spinner.set_size_request(48, 48)
        self._loading_box.append(loading_spinner)

        self._loading_label = Gtk.Label(label="Loading channels...")
        self._loading_label.add_css_class("title-2")
        self._loading_box.append(self._loading_label)

        self._loading_progress = Gtk.Label(label="")
        self._loading_progress.add_css_class("dim-label")
        self._loading_box.append(self._loading_progress)

        # Content
        content = Adw.ToastOverlay()
        self._toast_overlay = content

        # Scrolled window for stream list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Stream list
        self.stream_list = Gtk.ListBox()
        self.stream_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.stream_list.set_activate_on_single_click(False)
        self.stream_list.connect("row-activated", self._on_row_activated)
        self.stream_list.add_css_class("boxed-list")

        # Empty state
        self._empty_label = Gtk.Label(
            label="No channels added yet.\nClick the + button to add channels."
        )
        self._empty_label.set_justify(Gtk.Justification.CENTER)
        self._empty_label.add_css_class("dim-label")
        self._empty_label.set_vexpand(True)
        self._empty_label.set_valign(Gtk.Align.CENTER)

        # Hidden offline state (when hide offline is on and all are offline)
        self._all_offline_label = Gtk.Label(
            label="All channels are offline.\nUncheck 'Hide Offline' to see them."
        )
        self._all_offline_label.set_justify(Gtk.Justification.CENTER)
        self._all_offline_label.add_css_class("dim-label")
        self._all_offline_label.set_vexpand(True)
        self._all_offline_label.set_valign(Gtk.Align.CENTER)

        # Stack for loading/empty/list states
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._loading_box, "loading")
        self._stack.add_named(self._empty_label, "empty")
        self._stack.add_named(self._all_offline_label, "all_offline")

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.set_margin_top(12)
        list_box.set_margin_bottom(12)
        list_box.set_margin_start(12)
        list_box.set_margin_end(12)
        list_box.append(self.stream_list)
        scrolled.set_child(list_box)
        self._stack.add_named(scrolled, "list")

        # Start with loading view
        self._stack.set_visible_child_name("loading")

        content.set_child(self._stack)
        main_box.append(content)

        # Status bar
        self._status_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_start=12,
            margin_end=12,
            margin_top=6,
            margin_bottom=6,
        )

        self._status_label = Gtk.Label(label="Ready")
        self._status_label.set_halign(Gtk.Align.START)
        self._status_label.set_hexpand(True)
        self._status_label.add_css_class("caption")
        self._status_bar.append(self._status_label)

        self._live_count_label = Gtk.Label()
        self._live_count_label.add_css_class("caption")
        self._status_bar.append(self._live_count_label)

        main_box.append(self._status_bar)

    def _create_menu(self) -> Gio.Menu:
        """Create the application menu."""
        menu = Gio.Menu()

        # Data section
        data_section = Gio.Menu()
        data_section.append("Export...", "win.export")
        data_section.append("Import...", "win.import")
        menu.append_section(None, data_section)

        # App section
        app_section = Gio.Menu()
        app_section.append("Preferences", "app.preferences")
        app_section.append("About", "app.about")
        app_section.append("Quit", "app.quit")
        menu.append_section(None, app_section)

        return menu

    def _setup_actions(self) -> None:
        """Setup window actions."""
        # Export action
        export_action = Gio.SimpleAction.new("export", None)
        export_action.connect("activate", lambda a, p: self._show_export_dialog())
        self.add_action(export_action)

        # Import action
        import_action = Gio.SimpleAction.new("import", None)
        import_action.connect("activate", lambda a, p: self._show_import_dialog())
        self.add_action(import_action)

    def _show_export_dialog(self) -> None:
        """Show the export dialog."""
        dialog = ExportDialog(self)
        dialog.present()

    def _show_import_dialog(self) -> None:
        """Show the import file chooser."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Import Channels")

        # Set up file filter for JSON files
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Livestream List Export (*.json)")
        json_filter.add_pattern("*.json")
        filters.append(json_filter)

        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)

        dialog.set_filters(filters)
        dialog.set_default_filter(json_filter)

        dialog.open(self, None, self._on_import_file_selected)

    def _on_import_file_selected(self, dialog: Gtk.FileDialog, result) -> None:
        """Handle import file selection."""
        try:
            file = dialog.open_finish(result)
            if file:
                file_path = file.get_path()
                self._do_import(file_path)
        except GLib.Error as e:
            if e.code != Gtk.DialogError.DISMISSED:
                self.show_toast(f"Error selecting file: {e.message}")

    def _do_import(self, file_path: str) -> None:
        """Import data from a file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Validate structure
            if not isinstance(data, dict):
                self.show_toast("Invalid export file format")
                return

            meta = data.get("meta", {})
            channels_data = data.get("channels", [])
            settings_data = data.get("settings")

            schema_version = meta.get("schema_version", 1)
            app_version = meta.get("app_version", "unknown")
            export_date = meta.get("export_date", "unknown")

            # If settings are included, ask user what to do
            if settings_data:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Import Options",
                    body=f"This export from v{app_version} ({export_date}) includes:\n"
                         f"• {len(channels_data)} channels\n"
                         f"• Application settings\n\n"
                         f"What would you like to import?",
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("channels", "Channels Only")
                dialog.add_response("both", "Channels + Settings")
                dialog.set_response_appearance("both", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("both")
                dialog.connect("response", self._on_import_response, data)
                dialog.present()
            else:
                # Just import channels
                self._import_channels(channels_data)

        except json.JSONDecodeError:
            self.show_toast("Invalid JSON file")
        except Exception as e:
            self.show_toast(f"Import error: {e}")

    def _on_import_response(self, dialog: Adw.MessageDialog, response: str, data: dict) -> None:
        """Handle import dialog response."""
        if response == "cancel":
            return

        channels_data = data.get("channels", [])
        settings_data = data.get("settings")

        # Import channels
        added = self._import_channels(channels_data)

        # Import settings if requested
        if response == "both" and settings_data:
            self._import_settings(settings_data)
            self.show_toast(f"Imported {added} channels and settings")
        else:
            self.show_toast(f"Imported {added} channels")

    def _import_channels(self, channels_data: list) -> int:
        """Import channels from data. Returns number of channels added."""
        if not self.app.monitor:
            return 0

        added = 0
        for ch_data in channels_data:
            try:
                platform_str = ch_data.get("platform", "twitch")
                platform = StreamPlatform(platform_str)
                channel_id = ch_data.get("channel_id", "")
                display_name = ch_data.get("display_name", channel_id)
                favorite = ch_data.get("favorite", False)
                last_live_time_str = ch_data.get("last_live_time")

                if not channel_id:
                    continue

                from ..core.models import Channel
                channel = Channel(
                    channel_id=channel_id,
                    platform=platform,
                    display_name=display_name,
                    favorite=favorite,
                )

                # Parse last_live_time if present
                last_live_time = None
                if last_live_time_str:
                    try:
                        last_live_time = datetime.fromisoformat(last_live_time_str.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        pass

                key = channel.unique_key
                if key not in {c.unique_key for c in self.app.monitor.channels}:
                    self.app.monitor._channels[key] = channel
                    from ..core.models import Livestream
                    self.app.monitor._livestreams[key] = Livestream(
                        channel=channel,
                        last_live_time=last_live_time,
                    )
                    added += 1

            except (KeyError, ValueError) as e:
                continue

        if added > 0:
            # Save and refresh
            if self.app._loop:
                self.app._loop.run_until_complete(self.app.monitor._save_channels())
            self.refresh_stream_list()

        return added

    def _import_settings(self, settings_data: dict) -> None:
        """Import settings from data."""
        try:
            # Load current settings, then update with imported values
            settings = self.app.settings

            # General settings
            if "refresh_interval" in settings_data:
                settings.refresh_interval = settings_data["refresh_interval"]
            if "ui_style" in settings_data:
                settings.ui_style = settings_data["ui_style"]
            if "platform_colors" in settings_data:
                settings.platform_colors = settings_data["platform_colors"]

            # Streamlink settings
            if "streamlink" in settings_data:
                sl = settings_data["streamlink"]
                settings.streamlink.enabled = sl.get("enabled", settings.streamlink.enabled)
                settings.streamlink.path = sl.get("path", settings.streamlink.path)
                settings.streamlink.player = sl.get("player", settings.streamlink.player)
                settings.streamlink.player_args = sl.get("player_args", settings.streamlink.player_args)
                settings.streamlink.additional_args = sl.get("additional_args", settings.streamlink.additional_args)

            # Notification settings
            if "notifications" in settings_data:
                n = settings_data["notifications"]
                settings.notifications.enabled = n.get("enabled", settings.notifications.enabled)
                settings.notifications.sound_enabled = n.get("sound_enabled", settings.notifications.sound_enabled)

            # Chat settings
            if "chat" in settings_data:
                c = settings_data["chat"]
                settings.chat.enabled = c.get("enabled", settings.chat.enabled)
                settings.chat.browser = c.get("browser", settings.chat.browser)
                settings.chat.url_type = c.get("url_type", settings.chat.url_type)
                settings.chat.auto_open = c.get("auto_open", settings.chat.auto_open)

            # Window settings
            if "window" in settings_data:
                w = settings_data["window"]
                settings.window.width = w.get("width", settings.window.width)
                settings.window.height = w.get("height", settings.window.height)

            settings.save()

            # Apply style changes
            self.apply_style()

        except Exception as e:
            self.show_toast(f"Settings import error: {e}")

    def refresh_stream_list(self) -> None:
        """Refresh the stream list display."""
        if not self.app.monitor:
            return

        livestreams = list(self.app.monitor.livestreams)

        # Get playing streams
        playing_keys = set()
        if self.app.streamlink:
            playing_keys = set(self.app.streamlink.get_playing_streams())

        # Apply filters
        if self._hide_offline:
            livestreams = [s for s in livestreams if s.live]

        if self._favorites_only:
            livestreams = [s for s in livestreams if s.channel.favorite]

        if self._name_filter:
            livestreams = [s for s in livestreams if self._matches_filter(s.display_name)]

        if self._platform_filter:
            livestreams = [s for s in livestreams if s.channel.platform == self._platform_filter]

        # Helper to check if playing
        def is_playing(s):
            return s.channel.unique_key in playing_keys

        # Apply sort (always put live streams first, then sort within each group)
        if self._sort_mode == self.SORT_NAME:
            livestreams.sort(key=lambda s: (not s.live, s.display_name.lower()))
        elif self._sort_mode == self.SORT_VIEWERS:
            livestreams.sort(key=lambda s: (not s.live, -s.viewers if s.live else 0, s.display_name.lower()))
        elif self._sort_mode == self.SORT_PLAYING:
            # Playing first, then live, then offline
            livestreams.sort(key=lambda s: (not is_playing(s), not s.live, -s.viewers if s.live else 0, s.display_name.lower()))
        elif self._sort_mode == self.SORT_LAST_SEEN:
            # Live first, then offline sorted by most recently seen
            from datetime import datetime, timezone
            def last_seen_key(s):
                if s.live:
                    return (0, 0, s.display_name.lower())  # Live streams first
                elif s.last_live_time:
                    # More recent = smaller number (negative timestamp)
                    return (1, -s.last_live_time.timestamp(), s.display_name.lower())
                else:
                    # No last seen time = sort to end
                    return (2, 0, s.display_name.lower())
            livestreams.sort(key=last_seen_key)
        elif self._sort_mode == self.SORT_TIME_LIVE:
            # Sort by how long they've been live (longest first), offline at end
            def time_live_key(s):
                if s.live and s.start_time:
                    # Longer stream time = smaller number (negative uptime)
                    return (0, -s.uptime.total_seconds(), s.display_name.lower())
                elif s.live:
                    # Live but no start time = after those with start time
                    return (1, 0, s.display_name.lower())
                else:
                    # Offline at end
                    return (2, 0, s.display_name.lower())
            livestreams.sort(key=time_live_key)

        # Clear existing rows
        while True:
            row = self.stream_list.get_row_at_index(0)
            if row is None:
                break
            self.stream_list.remove(row)
        self._stream_rows.clear()

        # Create rows
        ui_style = self.app.settings.ui_style
        platform_colors = self.app.settings.platform_colors
        channel_info = self.app.settings.channel_info
        channel_icons = self.app.settings.channel_icons
        for livestream in livestreams:
            key = livestream.channel.unique_key
            row = StreamRow(
                livestream,
                show_checkbox=self._selection_mode,
                is_playing=key in playing_keys,
                on_stop=self._on_stop_stream,
                on_favorite=self._on_toggle_favorite,
                on_chat=self._on_open_chat,
                on_channel=self._on_open_channel,
                on_play=self._on_play_stream,
                ui_style=ui_style,
                platform_colors=platform_colors,
                show_platform=channel_icons.show_platform,
                show_play=channel_icons.show_play,
                show_favorite=channel_icons.show_favorite,
                show_chat=channel_icons.show_chat,
                show_browser=channel_icons.show_browser,
                show_live_duration=channel_info.show_live_duration,
                show_viewers=channel_info.show_viewers,
            )
            row.checkbox.connect("toggled", self._on_checkbox_toggled)
            list_row = Gtk.ListBoxRow()
            list_row.set_child(row)
            list_row.livestream = livestream
            self.stream_list.append(list_row)
            self._stream_rows[key] = row

        # Update stack visibility
        all_streams = list(self.app.monitor.livestreams)
        if livestreams:
            self._stack.set_visible_child_name("list")
        elif all_streams and (self._hide_offline or self._favorites_only or self._name_filter or self._platform_filter):
            # Have channels but all are hidden due to filters
            if self._name_filter:
                self._all_offline_label.set_label(
                    f"No channels match '{self._name_filter}'.\nTry a different search."
                )
            elif self._platform_filter:
                platform_name = self._platform_filter.value.title()
                self._all_offline_label.set_label(
                    f"No {platform_name} channels.\nSelect 'All' to see other platforms."
                )
            elif not self._initial_check_complete:
                # Still checking status - show loading message
                self._all_offline_label.set_label(
                    "Checking stream status..."
                )
            elif self._favorites_only and self._hide_offline:
                self._all_offline_label.set_label(
                    "No live favorites to show.\nStar some channels or adjust filters."
                )
            elif self._favorites_only:
                self._all_offline_label.set_label(
                    "No favorite channels.\nStar some channels to see them here."
                )
            else:
                # Hide offline is on and all are offline
                self._all_offline_label.set_label(
                    "All channels are offline.\nUncheck 'Hide Offline' to see them."
                )
            self._stack.set_visible_child_name("all_offline")
        else:
            # No channels at all
            self._stack.set_visible_child_name("empty")

        # Update status
        all_streams = self.app.monitor.livestreams
        live_count = sum(1 for s in all_streams if s.live)
        total_count = len(all_streams)
        shown_count = len(livestreams)
        if self._hide_offline:
            self._live_count_label.set_label(f"{shown_count} shown / {live_count} live / {total_count} total")
        else:
            self._live_count_label.set_label(f"{live_count} live / {total_count} channels")
        self._status_label.set_label("Last updated just now")

        # Update selection count
        self._update_selection_count()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Handle search/filter text change."""
        self._name_filter = entry.get_text().strip().lower()
        self.refresh_stream_list()

    def _matches_filter(self, name: str) -> bool:
        """Check if a name matches the current filter (supports wildcards)."""
        if not self._name_filter:
            return True

        name_lower = name.lower()
        filter_text = self._name_filter

        # Support * as wildcard
        if '*' in filter_text:
            import fnmatch
            return fnmatch.fnmatch(name_lower, filter_text)

        # Otherwise do substring match (most user-friendly)
        return filter_text in name_lower

    def _on_hide_offline_toggled(self, check: Gtk.CheckButton) -> None:
        """Handle hide offline toggle."""
        self._hide_offline = check.get_active()
        # Save preference
        self.app.settings.hide_offline = self._hide_offline
        self.app.settings.save()
        self.refresh_stream_list()

    def _on_favorites_toggled(self, check: Gtk.CheckButton) -> None:
        """Handle favorites filter toggle."""
        self._favorites_only = check.get_active()
        # Save preference
        self.app.settings.favorites_only = self._favorites_only
        self.app.settings.save()
        self.refresh_stream_list()

    def _on_sort_changed(self, dropdown: Gtk.DropDown, param) -> None:
        """Handle sort dropdown change."""
        self._sort_mode = dropdown.get_selected()
        # Save preference
        self.app.settings.sort_mode = self._sort_mode
        self.app.settings.save()
        self.refresh_stream_list()

    def _on_platform_filter_changed(self, dropdown: Gtk.DropDown, param) -> None:
        """Handle platform filter dropdown change."""
        selected = dropdown.get_selected()
        # 0 = All, 1 = Twitch, 2 = YouTube, 3 = Kick
        if selected == 0:
            self._platform_filter = None
        elif selected == 1:
            self._platform_filter = StreamPlatform.TWITCH
        elif selected == 2:
            self._platform_filter = StreamPlatform.YOUTUBE
        elif selected == 3:
            self._platform_filter = StreamPlatform.KICK
        self.refresh_stream_list()

    def _on_selection_mode_toggled(self, button: Gtk.ToggleButton) -> None:
        """Handle selection mode toggle."""
        self._selection_mode = button.get_active()
        self._selection_bar.set_visible(self._selection_mode)
        self.refresh_stream_list()

    def _on_checkbox_toggled(self, checkbox: Gtk.CheckButton) -> None:
        """Handle checkbox toggle."""
        self._update_selection_count()

    def _update_selection_count(self) -> None:
        """Update the selection count label."""
        count = sum(1 for row in self._stream_rows.values() if row.checkbox.get_active())
        self._selection_label.set_label(f"{count} selected")
        self._delete_btn.set_sensitive(count > 0)

    def _on_select_all(self, button: Gtk.Button) -> None:
        """Select all visible rows."""
        for row in self._stream_rows.values():
            row.checkbox.set_active(True)
        self._update_selection_count()

    def _on_deselect_all(self, button: Gtk.Button) -> None:
        """Deselect all rows."""
        for row in self._stream_rows.values():
            row.checkbox.set_active(False)
        self._update_selection_count()

    def _on_delete_selected(self, button: Gtk.Button) -> None:
        """Delete selected channels."""
        selected_keys = [
            key for key, row in self._stream_rows.items()
            if row.checkbox.get_active()
        ]

        if not selected_keys:
            return

        # Confirm deletion
        count = len(selected_keys)
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Delete {count} channel{'s' if count > 1 else ''}?",
            body="This will remove the selected channels from your list.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_delete_confirm, selected_keys)
        dialog.present()

    def _on_delete_confirm(self, dialog: Adw.MessageDialog, response: str, selected_keys: list) -> None:
        """Handle delete confirmation."""
        if response != "delete":
            return

        app = self.app
        if app.monitor and app._loop:
            async def delete_channels():
                for key in selected_keys:
                    if key in app.monitor._channels:
                        channel = app.monitor._channels[key]
                        await app.monitor.remove_channel(channel)

            app._loop.run_until_complete(delete_channels())
            self.refresh_stream_list()
            self.show_toast(f"Deleted {len(selected_keys)} channel{'s' if len(selected_keys) > 1 else ''}")

    def _on_row_activated(self, list_box: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        """Handle row double-click."""
        if hasattr(row, "livestream") and row.livestream.live:
            stream = row.livestream
            stream_name = stream.display_name

            if self.app.streamlink and self.app.streamlink.is_available():
                # Show launching feedback
                self.set_status(f"Launching {stream_name}...")
                self.show_toast(f"Opening {stream_name} in player...")

                # Launch in background thread and monitor process
                import threading
                import time

                def launch_stream():
                    channel_key = stream.channel.unique_key
                    channel_id = stream.channel.channel_id
                    try:
                        process = self.app.streamlink.launch(stream)
                        if process is None:
                            GLib.idle_add(lambda: self.show_toast(f"Failed to launch streamlink"))
                            GLib.idle_add(lambda: self.set_status("Launch failed"))
                            return

                        # Auto-open chat immediately (in parallel with stream loading)
                        if self.app.settings.chat.auto_open:
                            platform = stream.channel.platform
                            vid_id = stream.video_id
                            # Note: Must return None/False, not the return value of open_chat()
                            # GLib.idle_add reschedules callbacks that return True
                            GLib.idle_add(lambda: (self._chat_launcher.open_chat(channel_id, platform, vid_id), None)[1])

                        # Update status - streamlink is starting
                        GLib.idle_add(lambda: self.set_status(f"Starting {stream_name}..."))

                        # Wait for player to actually start (check if process is still running)
                        # Streamlink spawns the player, so we monitor for a few seconds
                        for i in range(10):  # Check for up to 10 seconds
                            time.sleep(1)
                            poll = process.poll()
                            if poll is not None:
                                # Process exited - check if it was an error
                                if poll != 0:
                                    GLib.idle_add(lambda: self.show_toast(f"Streamlink exited with error"))
                                    GLib.idle_add(lambda: self.set_status("Launch failed"))
                                    return
                                break
                            GLib.idle_add(lambda sec=i+1: self.set_status(f"Loading {stream_name}... ({sec}s)"))

                        # Success - player should be running
                        def update_playing():
                            self.set_status(f"Playing {stream_name}")
                            self.show_toast(f"Now playing {stream_name}")
                            # Update the row's playing indicator
                            if channel_key in self._stream_rows:
                                self._stream_rows[channel_key].set_playing(True)
                            # Refresh list if sorted by Playing to bring this stream to top
                            if self._sort_mode == self.SORT_PLAYING:
                                self.refresh_stream_list()

                        GLib.idle_add(update_playing)

                    except Exception as e:
                        GLib.idle_add(lambda: self.show_toast(f"Failed to launch: {e}"))
                        GLib.idle_add(lambda: self.set_status("Launch failed"))

                thread = threading.Thread(target=launch_stream, daemon=True)
                thread.start()
            else:
                self.show_toast(f"Opening {stream_name} in browser...")
                open_in_browser(stream)

    def _on_close_request(self, window: Gtk.Window) -> bool:
        """Handle window close."""
        # Save window state
        if self.app.settings:
            width, height = self.get_default_size()
            self.app.settings.window.width = width
            self.app.settings.window.height = height
            self.app.settings.save()

        # If user hasn't been asked about close behavior yet
        if not self.app.settings.close_to_tray_asked:
            self._show_close_behavior_dialog()
            return True  # Prevent close, dialog will handle it

        # If run in background is enabled, hide instead of close
        if self.app.settings.close_to_tray:
            self.set_visible(False)
            return True  # Prevent actual close

        # Quit the application
        self.app.quit()
        return False  # Allow close

    def _show_close_behavior_dialog(self) -> None:
        """Show dialog asking user about close behavior."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Close Behavior",
            body="Would you like to keep the application running in the background when closing, or quit completely?\n\nTo restore the window, simply launch the app again.",
        )

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("quit", "Quit")
        dialog.add_response("background", "Run in Background")
        dialog.set_response_appearance("background", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("background")
        dialog.set_close_response("cancel")

        # Add checkbox for "Don't ask again"
        check = Gtk.CheckButton(label="Remember my choice")
        check.set_active(True)
        dialog.set_extra_child(check)

        def on_response(dialog, response):
            if response == "cancel":
                return  # Just close dialog, keep app running with window visible

            remember = check.get_active()

            if response == "background":
                self.app.settings.close_to_tray = True
                if remember:
                    self.app.settings.close_to_tray_asked = True
                self.app.settings.save()
                self.set_visible(False)
            else:  # quit
                self.app.settings.close_to_tray = False
                if remember:
                    self.app.settings.close_to_tray_asked = True
                self.app.settings.save()
                self.app.quit()

        dialog.connect("response", on_response)
        dialog.present()

    def show_add_channel_dialog(self) -> None:
        """Show the add channel dialog."""
        dialog = AddChannelDialog(self)
        dialog.present()

    def show_preferences_dialog(self) -> None:
        """Show the preferences dialog."""
        dialog = PreferencesDialog(self)
        dialog.present()

    def show_toast(self, message: str) -> None:
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        self._toast_overlay.add_toast(toast)

    def set_loading_status(self, message: str, progress: str = "") -> None:
        """Update the loading status display."""
        self._loading_label.set_label(message)
        self._loading_progress.set_label(progress)

    def set_loading_complete(self) -> None:
        """Hide the loading view and show content."""
        self.refresh_stream_list()

    def set_status(self, message: str) -> None:
        """Update the status bar message."""
        self._status_label.set_label(message)

    def apply_style(self) -> None:
        """Apply the current UI style (refreshes the list with new style)."""
        ui_style = self.app.settings.ui_style

        # Helper to set header button icon size
        def set_button_icon_size(button: Gtk.Button, icon_name: str, size: int) -> None:
            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(size)
            button.set_child(image)

        # Adjust filter bar for compact modes
        if ui_style == 3:
            self._filter_bar.set_spacing(2)
            self._filter_bar.set_margin_start(4)
            self._filter_bar.set_margin_end(4)
            self._filter_bar.set_margin_top(2)
            self._filter_bar.set_margin_bottom(2)
            self._hide_offline_check.add_css_class("caption")
            self._favorites_check.add_css_class("caption")
            self._platform_label.add_css_class("caption")
            self._sort_label.add_css_class("caption")
            self._search_entry.add_css_class("caption")
            self._platform_dropdown.add_css_class("caption")
            self._sort_dropdown.add_css_class("caption")
            # Scale header bar
            self._header_bar.add_css_class("flat")
            set_button_icon_size(self._add_button, "list-add-symbolic", 14)
            set_button_icon_size(self._refresh_button, "view-refresh-symbolic", 14)
            set_button_icon_size(self._select_button, "edit-delete-symbolic", 14)
        elif ui_style == 2:
            self._filter_bar.set_spacing(4)
            self._filter_bar.set_margin_start(6)
            self._filter_bar.set_margin_end(6)
            self._filter_bar.set_margin_top(4)
            self._filter_bar.set_margin_bottom(2)
            self._hide_offline_check.add_css_class("caption")
            self._favorites_check.add_css_class("caption")
            self._platform_label.add_css_class("caption")
            self._sort_label.add_css_class("caption")
            self._search_entry.add_css_class("caption")
            self._platform_dropdown.add_css_class("caption")
            self._sort_dropdown.add_css_class("caption")
            # Scale header bar
            self._header_bar.add_css_class("flat")
            set_button_icon_size(self._add_button, "list-add-symbolic", 14)
            set_button_icon_size(self._refresh_button, "view-refresh-symbolic", 14)
            set_button_icon_size(self._select_button, "edit-delete-symbolic", 14)
        elif ui_style == 1:
            self._filter_bar.set_spacing(6)
            self._filter_bar.set_margin_start(8)
            self._filter_bar.set_margin_end(8)
            self._filter_bar.set_margin_top(6)
            self._filter_bar.set_margin_bottom(3)
            self._hide_offline_check.remove_css_class("caption")
            self._favorites_check.remove_css_class("caption")
            self._platform_label.remove_css_class("caption")
            self._sort_label.remove_css_class("caption")
            self._search_entry.remove_css_class("caption")
            self._platform_dropdown.remove_css_class("caption")
            self._sort_dropdown.remove_css_class("caption")
            # Scale header bar
            self._header_bar.add_css_class("flat")
            set_button_icon_size(self._add_button, "list-add-symbolic", 16)
            set_button_icon_size(self._refresh_button, "view-refresh-symbolic", 16)
            set_button_icon_size(self._select_button, "edit-delete-symbolic", 16)
        else:
            self._filter_bar.set_spacing(8)
            self._filter_bar.set_margin_start(12)
            self._filter_bar.set_margin_end(12)
            self._filter_bar.set_margin_top(8)
            self._filter_bar.set_margin_bottom(4)
            self._hide_offline_check.remove_css_class("caption")
            self._favorites_check.remove_css_class("caption")
            self._platform_label.remove_css_class("caption")
            self._sort_label.remove_css_class("caption")
            self._search_entry.remove_css_class("caption")
            self._platform_dropdown.remove_css_class("caption")
            self._sort_dropdown.remove_css_class("caption")
            # Reset header bar to default
            self._header_bar.remove_css_class("flat")
            self._add_button.set_icon_name("list-add-symbolic")
            self._refresh_button.set_icon_name("view-refresh-symbolic")
            self._select_button.set_icon_name("edit-delete-symbolic")

        self.refresh_stream_list()

    def _check_playing_streams(self) -> bool:
        """Periodically check for stopped streams and update UI."""
        if self.app.streamlink:
            stopped = self.app.streamlink.cleanup_dead_processes()
            if stopped:
                # Update playing indicators for stopped streams
                for key in stopped:
                    if key in self._stream_rows:
                        self._stream_rows[key].set_playing(False)
        return True  # Keep repeating

    def _on_stop_stream(self, channel_key: str) -> None:
        """Handle stop stream button click."""
        if self.app.streamlink:
            if self.app.streamlink.stop_stream(channel_key):
                self.show_toast("Playback stopped")
                # Update the row
                if channel_key in self._stream_rows:
                    self._stream_rows[channel_key].set_playing(False)
                self.set_status("Ready")

    def _on_toggle_favorite(self, channel_key: str) -> None:
        """Handle favorite button click."""
        if self.app.monitor:
            # Get the channel
            if channel_key in self.app.monitor._channels:
                channel = self.app.monitor._channels[channel_key]
                new_state = not channel.favorite
                self.app.monitor.set_favorite(channel, new_state)

                # Update the row
                if channel_key in self._stream_rows:
                    self._stream_rows[channel_key].set_favorite(new_state)

                # Save channels
                import threading
                import asyncio

                def save_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self.app.monitor.save_channels())
                    finally:
                        loop.close()

                thread = threading.Thread(target=save_async, daemon=True)
                thread.start()

                # If favorites filter is on and we unfavorited, refresh list
                if self._favorites_only and not new_state:
                    self.refresh_stream_list()

    def _on_open_chat(self, channel_id: str, platform: StreamPlatform = StreamPlatform.TWITCH, video_id: str = None) -> None:
        """Handle chat button click."""
        if self._chat_launcher.open_chat(channel_id, platform, video_id):
            self.set_status(f"Opened chat for {channel_id}")
        else:
            if platform == StreamPlatform.YOUTUBE and not video_id:
                self.set_status(f"Cannot open YouTube chat - stream not live")
            else:
                self.set_status(f"Failed to open chat for {channel_id}")

    def _on_open_channel(self, channel_id: str, platform: StreamPlatform = StreamPlatform.TWITCH) -> None:
        """Handle channel browser button click."""
        if self._chat_launcher.open_channel(channel_id, platform):
            self.set_status(f"Opened {channel_id} in browser")
        else:
            self.set_status(f"Failed to open {channel_id}")

    def _on_play_stream(self, livestream: Livestream) -> None:
        """Handle play button click - launch stream in player."""
        if not livestream.live:
            self.show_toast(f"{livestream.display_name} is offline")
            return

        stream = livestream
        stream_name = stream.display_name

        if self.app.streamlink and self.app.streamlink.is_available():
            # Show launching feedback
            self.set_status(f"Launching {stream_name}...")
            self.show_toast(f"Opening {stream_name} in player...")

            # Launch in background thread and monitor process
            import threading
            import time

            def launch_stream():
                channel_key = stream.channel.unique_key
                channel_id = stream.channel.channel_id
                try:
                    process = self.app.streamlink.launch(stream)
                    if process is None:
                        GLib.idle_add(lambda: self.show_toast(f"Failed to launch streamlink"))
                        GLib.idle_add(lambda: self.set_status("Launch failed"))
                        return

                    # Auto-open chat immediately (in parallel with stream loading)
                    if self.app.settings.chat.auto_open:
                        platform = stream.channel.platform
                        vid_id = stream.video_id
                        GLib.idle_add(lambda: (self._chat_launcher.open_chat(channel_id, platform, vid_id), None)[1])

                    # Update status - streamlink is starting
                    GLib.idle_add(lambda: self.set_status(f"Starting {stream_name}..."))

                    # Wait for player to actually start
                    for i in range(10):
                        time.sleep(1)
                        poll = process.poll()
                        if poll is not None:
                            if poll != 0:
                                GLib.idle_add(lambda: self.show_toast(f"Streamlink exited with error"))
                                GLib.idle_add(lambda: self.set_status("Launch failed"))
                                return
                            break
                        GLib.idle_add(lambda sec=i+1: self.set_status(f"Loading {stream_name}... ({sec}s)"))

                    # Success - player should be running
                    def update_playing():
                        self.set_status(f"Playing {stream_name}")
                        self.show_toast(f"Now playing {stream_name}")
                        if channel_key in self._stream_rows:
                            self._stream_rows[channel_key].set_playing(True)
                        if self._sort_mode == self.SORT_PLAYING:
                            self.refresh_stream_list()

                    GLib.idle_add(update_playing)

                except Exception as e:
                    GLib.idle_add(lambda: self.show_toast(f"Failed to launch: {e}"))
                    GLib.idle_add(lambda: self.set_status("Launch failed"))

            thread = threading.Thread(target=launch_stream, daemon=True)
            thread.start()
        else:
            self.show_toast(f"Opening {stream_name} in browser...")
            open_in_browser(stream)

    def show_import_follows_dialog(self, platform: StreamPlatform = StreamPlatform.TWITCH) -> None:
        """Show the import follows dialog."""
        dialog = ImportFollowsDialog(self, platform)
        dialog.present()


class ImportFollowsDialog(Adw.Window):
    """Dialog for importing followed channels from Twitch or Kick."""

    def __init__(self, parent: MainWindow, platform: StreamPlatform = StreamPlatform.TWITCH) -> None:
        self._platform = platform
        self._platform_name = "Twitch" if platform == StreamPlatform.TWITCH else "Kick"
        platform_name = self._platform_name

        super().__init__(
            transient_for=parent,
            modal=True,
            title=f"Import {platform_name} Follows",
            default_width=450,
            default_height=300,
        )

        self.parent_window = parent

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)

        # Header
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: self.close())
        header.pack_start(close_btn)

        content.append(header)

        # Main content stack
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        # Login view
        login_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        login_box.set_valign(Gtk.Align.CENTER)

        login_label = Gtk.Label(
            label=f"Log in to {platform_name} to import your followed channels.\n\n"
                  "This will open your browser for authorization."
        )
        login_label.set_wrap(True)
        login_label.set_justify(Gtk.Justification.CENTER)
        login_box.append(login_label)

        self._login_btn = Gtk.Button(label=f"Login with {platform_name}")
        self._login_btn.add_css_class("suggested-action")
        self._login_btn.add_css_class("pill")
        self._login_btn.set_halign(Gtk.Align.CENTER)
        self._login_btn.connect("clicked", self._on_login_clicked)
        login_box.append(self._login_btn)

        self._stack.add_named(login_box, "login")

        # Waiting view (while OAuth is in progress)
        waiting_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        waiting_box.set_valign(Gtk.Align.CENTER)

        waiting_label = Gtk.Label(
            label="Waiting for authorization...\n\nPlease complete the login in your browser."
        )
        waiting_label.set_wrap(True)
        waiting_label.set_justify(Gtk.Justification.CENTER)
        waiting_box.append(waiting_label)

        spinner = Gtk.Spinner()
        spinner.set_spinning(True)
        spinner.set_halign(Gtk.Align.CENTER)
        waiting_box.append(spinner)

        self._stack.add_named(waiting_box, "waiting")

        # Import view
        import_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        import_box.set_valign(Gtk.Align.CENTER)

        self._import_label = Gtk.Label(label="Ready to import follows")
        self._import_label.set_wrap(True)
        self._import_label.set_justify(Gtk.Justification.CENTER)
        import_box.append(self._import_label)

        self._import_btn = Gtk.Button(label="Import Followed Channels")
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.add_css_class("pill")
        self._import_btn.set_halign(Gtk.Align.CENTER)
        self._import_btn.connect("clicked", self._on_import_clicked)
        import_box.append(self._import_btn)

        # Logout button
        logout_btn = Gtk.Button(label="Logout")
        logout_btn.add_css_class("destructive-action")
        logout_btn.set_halign(Gtk.Align.CENTER)
        logout_btn.connect("clicked", self._on_logout_clicked)
        import_box.append(logout_btn)

        self._stack.add_named(import_box, "import")

        # Importing view (progress)
        importing_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_start=24,
            margin_end=24,
            margin_top=24,
            margin_bottom=24,
        )
        importing_box.set_valign(Gtk.Align.CENTER)

        self._progress_label = Gtk.Label(label="Fetching followed channels...")
        self._progress_label.set_wrap(True)
        self._progress_label.set_justify(Gtk.Justification.CENTER)
        importing_box.append(self._progress_label)

        self._progress_spinner = Gtk.Spinner()
        self._progress_spinner.set_spinning(True)
        self._progress_spinner.set_halign(Gtk.Align.CENTER)
        importing_box.append(self._progress_spinner)

        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        self._progress_bar.set_fraction(0)
        importing_box.append(self._progress_bar)

        self._stack.add_named(importing_box, "importing")

        content.append(self._stack)

        # Check if already authorized
        self._check_auth_status()

    def _check_auth_status(self) -> None:
        """Check if already authorized."""
        app = self.parent_window.app
        if app.monitor and app._loop:
            client = app.monitor.get_client(self._platform)

            async def check():
                # For Kick, check user authorization (has token)
                if self._platform == StreamPlatform.KICK:
                    return await client.is_user_authorized()
                return await client.is_authorized()

            is_auth = app._loop.run_until_complete(check())
            if is_auth:
                self._stack.set_visible_child_name("import")
                self._import_label.set_label(f"You're logged in to {self._platform_name}!\n\nReady to import your followed channels.")
            else:
                self._stack.set_visible_child_name("login")

    def _on_login_clicked(self, button: Gtk.Button) -> None:
        """Start the OAuth login flow."""
        app = self.parent_window.app

        if app.monitor and app._loop:
            client = app.monitor.get_client(self._platform)

            # Show waiting view
            self._stack.set_visible_child_name("waiting")

            # Run OAuth flow
            async def do_oauth():
                return await client.oauth_login(timeout=300)

            success = app._loop.run_until_complete(do_oauth())

            if success:
                # Save settings
                app.settings.save()
                self._stack.set_visible_child_name("import")
                self._import_label.set_label(
                    "Login successful!\n\nReady to import your followed channels."
                )
                self.parent_window.show_toast(f"{self._platform_name} authorization successful!")
            else:
                self._stack.set_visible_child_name("login")
                self.parent_window.show_toast(f"{self._platform_name} login failed or timed out")

    def _on_logout_clicked(self, button: Gtk.Button) -> None:
        """Log out of the platform."""
        app = self.parent_window.app

        if app.monitor:
            client = app.monitor.get_client(self._platform)
            client.logout()
            app.settings.save()

            self._stack.set_visible_child_name("login")
            self.parent_window.show_toast(f"Logged out of {self._platform_name}")

    def _on_import_clicked(self, button: Gtk.Button) -> None:
        """Import followed channels."""
        import threading

        app = self.parent_window.app
        platform = self._platform

        if app.monitor and app._loop:
            # Show importing progress view
            self._stack.set_visible_child_name("importing")
            self._progress_label.set_label("Fetching followed channels...")
            self._progress_bar.set_fraction(0)
            self._progress_bar.set_text("Connecting...")

            # Update main window status
            self.parent_window._status_label.set_label("Importing follows...")

            client = app.monitor.get_client(platform)

            def import_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    # Clear existing session so new one is created on this loop
                    client._session = None

                    # Fetch follows - wrap in a task for proper timeout support
                    async def fetch():
                        return await client.get_followed_channels()

                    task = loop.create_task(fetch())
                    result = loop.run_until_complete(task)

                    if isinstance(result, str):
                        GLib.idle_add(lambda: self._import_error(result))
                        return

                    if not result:
                        GLib.idle_add(lambda: self._import_error("No followed channels found"))
                        return

                    total = len(result)
                    GLib.idle_add(lambda: self._update_import_progress(
                        f"Found {total} channels", 0.2, f"0 / {total}"
                    ))

                    # Add channels
                    added = 0
                    for i, channel in enumerate(result):
                        key = channel.unique_key
                        if key not in {c.unique_key for c in app.monitor.channels}:
                            app.monitor._channels[key] = channel
                            from ..core.models import Livestream
                            app.monitor._livestreams[key] = Livestream(channel=channel)
                            added += 1

                            # Update status with channel name
                            name = channel.display_name or channel.channel_id
                            GLib.idle_add(lambda n=name, a=added, t=total: self._update_import_status(n, a, t))

                    if added > 0:
                        # Save
                        GLib.idle_add(lambda: self._update_import_progress(
                            "Saving channels...", 0.7, f"{added} added"
                        ))
                        loop.run_until_complete(loop.create_task(app.monitor._save_channels()))

                        # Refresh
                        GLib.idle_add(lambda: self._update_import_progress(
                            "Checking stream status...", 0.85, "Almost done..."
                        ))
                        loop.run_until_complete(loop.create_task(app.monitor.refresh()))

                    # Done
                    GLib.idle_add(lambda: self._import_complete(added, total))

                except Exception as e:
                    error_msg = str(e)
                    import traceback
                    traceback.print_exc()
                    GLib.idle_add(lambda msg=error_msg: self._import_error(msg))
                finally:
                    # Clear session so main loop can create a new one
                    client._session = None
                    loop.close()

            thread = threading.Thread(target=import_thread, daemon=True)
            thread.start()

    def _update_import_progress(self, label: str, fraction: float, text: str) -> None:
        """Update import progress UI."""
        self._progress_label.set_label(label)
        self._progress_bar.set_fraction(fraction)
        self._progress_bar.set_text(text)

    def _update_import_status(self, channel_name: str, added: int, total: int) -> None:
        """Update import status with current channel."""
        progress = 0.2 + (0.5 * added / total) if total > 0 else 0.5
        self._progress_bar.set_fraction(progress)
        self._progress_bar.set_text(f"{added} / {total}")
        self._progress_label.set_label(f"Adding: {channel_name}")
        # Also update main window status bar
        self.parent_window._status_label.set_label(f"Importing: {channel_name}")

    def _import_error(self, error: str) -> None:
        """Handle import error."""
        self._stack.set_visible_child_name("import")
        self.parent_window._status_label.set_label("Import failed")
        self.parent_window.show_toast(f"Error: {error}")

    def _import_complete(self, added: int, total: int) -> None:
        """Handle import completion."""
        self.parent_window.refresh_stream_list()
        self.parent_window._status_label.set_label("Import complete")
        self.parent_window.show_toast(f"Imported {added} new channels ({total} total follows)")
        self.close()


class AddChannelDialog(Adw.Window):
    """Dialog for adding a new channel."""

    def __init__(self, parent: MainWindow) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Add Channel",
            default_width=400,
            default_height=280,
        )

        self.parent_window = parent
        self._url_detected = False  # Track if we auto-detected from URL

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)

        # Header
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._on_add)
        header.pack_end(add_btn)

        content.append(header)

        # Form
        form = Adw.PreferencesGroup()
        form.set_margin_start(12)
        form.set_margin_end(12)
        form.set_margin_top(12)
        form.set_margin_bottom(12)

        # Channel name/URL entry
        self.channel_entry = Adw.EntryRow(title="Channel Name or URL")
        self.channel_entry.connect("entry-activated", lambda e: self._on_add(None))
        self.channel_entry.connect("notify::text", self._on_entry_changed)
        # Check clipboard when entry gets focus (if empty)
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_entry_focus)
        self.channel_entry.add_controller(focus_controller)
        form.add(self.channel_entry)

        # URL hint
        self._url_hint = Gtk.Label(
            label="Paste a URL like twitch.tv/username or kick.com/username"
        )
        self._url_hint.set_halign(Gtk.Align.START)
        self._url_hint.set_margin_start(16)
        self._url_hint.set_margin_bottom(8)
        self._url_hint.add_css_class("caption")
        self._url_hint.add_css_class("dim-label")
        form.add(self._url_hint)

        # Platform selector
        self.platform_row = Adw.ComboRow(title="Platform")
        platforms = Gtk.StringList.new(["Twitch", "YouTube", "Kick"])
        self.platform_row.set_model(platforms)
        form.add(self.platform_row)

        content.append(form)

        # Try to read clipboard for a URL when dialog opens
        GLib.idle_add(self._check_clipboard_for_url)

    def _check_clipboard_for_url(self) -> bool:
        """Check clipboard for a stream URL and pre-populate if found."""
        try:
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if not display:
                return False

            clipboard = display.get_clipboard()

            def on_clipboard_read(clipboard, result):
                try:
                    text = clipboard.read_text_finish(result)
                    if text:
                        text = text.strip()
                        # Only pre-populate if it looks like a stream URL
                        parsed = parse_channel_url(text)
                        if parsed:
                            self.channel_entry.set_text(text)
                except Exception:
                    pass  # Ignore clipboard errors

            clipboard.read_text_async(None, on_clipboard_read)

        except Exception:
            pass  # Ignore errors

        return False  # Don't repeat

    def _on_entry_focus(self, controller: Gtk.EventControllerFocus) -> None:
        """Handle entry focus - check clipboard if entry is empty."""
        # Only check clipboard if the entry is empty
        if not self.channel_entry.get_text().strip():
            self._check_clipboard_for_url()

    def _on_entry_changed(self, entry: Adw.EntryRow, param) -> None:
        """Handle text entry changes - detect URLs and auto-set platform."""
        text = entry.get_text().strip()
        if not text:
            self._url_detected = False
            self._url_hint.set_label("Paste a URL like twitch.tv/username or kick.com/username")
            self._url_hint.remove_css_class("success")
            return

        parsed = parse_channel_url(text)
        if parsed:
            channel_name, platform = parsed
            # Auto-select platform
            platform_idx = {
                StreamPlatform.TWITCH: 0,
                StreamPlatform.YOUTUBE: 1,
                StreamPlatform.KICK: 2,
            }.get(platform, 0)
            self.platform_row.set_selected(platform_idx)
            self._url_detected = True
            self._url_hint.set_label(f"Detected: {platform.value.title()} / {channel_name}")
            self._url_hint.add_css_class("success")
        else:
            self._url_detected = False
            self._url_hint.set_label("Paste a URL like twitch.tv/username or kick.com/username")
            self._url_hint.remove_css_class("success")

    def _on_add(self, button: Optional[Gtk.Button]) -> None:
        """Handle add button click."""
        text = self.channel_entry.get_text().strip()
        if not text:
            return

        # Check if it's a URL
        parsed = parse_channel_url(text)
        if parsed:
            channel_name, platform = parsed
        else:
            channel_name = text
            platform_idx = self.platform_row.get_selected()
            platforms = [StreamPlatform.TWITCH, StreamPlatform.YOUTUBE, StreamPlatform.KICK]
            platform = platforms[platform_idx]

        app = self.parent_window.app
        if app.monitor and app._loop:

            async def add():
                channel = await app.monitor.add_channel(channel_name, platform)
                return channel

            channel = app._loop.run_until_complete(add())

            if channel:
                self.parent_window.refresh_stream_list()
                self.parent_window.show_toast(f"Added {channel.display_name}")
                self.close()
            else:
                self.parent_window.show_toast(f"Channel not found: {channel_name}")


class ExportDialog(Adw.Window):
    """Dialog for exporting channels and settings."""

    def __init__(self, parent: MainWindow) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Export",
            default_width=400,
            default_height=300,
        )

        self.parent_window = parent

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)

        # Header
        header = Adw.HeaderBar()
        header.add_css_class("flat")

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        header.pack_start(cancel_btn)

        export_btn = Gtk.Button(label="Export")
        export_btn.add_css_class("suggested-action")
        export_btn.connect("clicked", self._on_export_clicked)
        header.pack_end(export_btn)

        content.append(header)

        # Export options
        options_group = Adw.PreferencesGroup(title="Export Options")
        options_group.set_margin_start(12)
        options_group.set_margin_end(12)
        options_group.set_margin_top(12)
        options_group.set_margin_bottom(12)

        # Channel count info
        channel_count = len(parent.app.monitor.channels) if parent.app.monitor else 0
        channels_row = Adw.ActionRow(title="Channels")
        channels_row.set_subtitle(f"{channel_count} channels will be exported")
        channels_row.add_prefix(Gtk.Image.new_from_icon_name("view-list-symbolic"))
        options_group.add(channels_row)

        # Include settings toggle
        self._include_settings = Adw.SwitchRow(title="Include Settings")
        self._include_settings.set_subtitle("Export application settings (streamlink, chat, etc.)")
        self._include_settings.set_active(True)
        options_group.add(self._include_settings)

        content.append(options_group)

        # Info
        info_group = Adw.PreferencesGroup()
        info_group.set_margin_start(12)
        info_group.set_margin_end(12)

        info_label = Gtk.Label(
            label=f"Export will include app version ({__version__}) and export date for compatibility tracking."
        )
        info_label.set_wrap(True)
        info_label.set_halign(Gtk.Align.START)
        info_label.add_css_class("caption")
        info_label.add_css_class("dim-label")
        info_group.add(info_label)

        content.append(info_group)

    def _on_export_clicked(self, button: Gtk.Button) -> None:
        """Handle export button click."""
        include_settings = self._include_settings.get_active()

        # Build export data
        export_data = self._build_export_data(include_settings)

        # Show file save dialog
        dialog = Gtk.FileDialog()
        dialog.set_title("Save Export")

        # Default filename with date
        date_str = datetime.now().strftime("%Y-%m-%d")
        dialog.set_initial_name(f"livestream-list-export-{date_str}.json")

        # Set up file filter
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON files (*.json)")
        json_filter.add_pattern("*.json")
        filters.append(json_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(json_filter)

        dialog.save(self, None, self._on_save_response, export_data)

    def _on_save_response(self, dialog: Gtk.FileDialog, result, export_data: dict) -> None:
        """Handle save dialog response."""
        try:
            file = dialog.save_finish(result)
            if file:
                file_path = file.get_path()

                # Ensure .json extension
                if not file_path.endswith('.json'):
                    file_path += '.json'

                # Write file
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)

                channel_count = len(export_data.get("channels", []))
                has_settings = "settings" in export_data
                msg = f"Exported {channel_count} channels"
                if has_settings:
                    msg += " and settings"
                self.parent_window.show_toast(msg)
                self.close()

        except GLib.Error as e:
            if e.code != Gtk.DialogError.DISMISSED:
                self.parent_window.show_toast(f"Export error: {e.message}")
        except Exception as e:
            self.parent_window.show_toast(f"Export error: {e}")

    def _build_export_data(self, include_settings: bool) -> dict:
        """Build the export data structure."""
        app = self.parent_window.app

        # Metadata
        export_data = {
            "meta": {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "app_version": __version__,
                "export_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "channels": [],
        }

        # Export channels
        if app.monitor:
            for livestream in app.monitor.livestreams:
                channel = livestream.channel
                ch_data = {
                    "channel_id": channel.channel_id,
                    "platform": channel.platform.value,
                    "display_name": channel.display_name,
                    "favorite": channel.favorite,
                }
                # Include last_live_time if available
                if livestream.last_live_time:
                    ch_data["last_live_time"] = livestream.last_live_time.isoformat()
                export_data["channels"].append(ch_data)

        # Export settings if requested
        if include_settings:
            settings = app.settings
            export_data["settings"] = {
                "refresh_interval": settings.refresh_interval,
                "ui_style": settings.ui_style,
                "platform_colors": settings.platform_colors,
                "streamlink": {
                    "enabled": settings.streamlink.enabled,
                    "path": settings.streamlink.path,
                    "player": settings.streamlink.player,
                    "player_args": settings.streamlink.player_args,
                    "additional_args": settings.streamlink.additional_args,
                },
                "notifications": {
                    "enabled": settings.notifications.enabled,
                    "sound_enabled": settings.notifications.sound_enabled,
                },
                "chat": {
                    "enabled": settings.chat.enabled,
                    "browser": settings.chat.browser,
                    "url_type": settings.chat.url_type,
                    "auto_open": settings.chat.auto_open,
                },
                "window": {
                    "width": settings.window.width,
                    "height": settings.window.height,
                },
            }

        return export_data


class PreferencesDialog(Adw.PreferencesWindow):
    """Preferences dialog."""

    def __init__(self, parent: MainWindow) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Preferences",
        )

        self.parent_window = parent
        self.settings = parent.app.settings

        # General page
        general_page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        # Startup group (at top)
        startup_group = Adw.PreferencesGroup(title="Startup")

        autostart_row = Adw.SwitchRow(title="Launch on Login")
        autostart_row.set_subtitle("Automatically start the application when you log in")
        autostart_row.set_active(self.settings.autostart)
        autostart_row.connect("notify::active", self._on_autostart_changed)
        startup_group.add(autostart_row)

        close_to_tray_row = Adw.SwitchRow(title="Run in Background")
        close_to_tray_row.set_subtitle("Keep running when closing window (re-launch to restore)")
        close_to_tray_row.set_active(self.settings.close_to_tray)
        close_to_tray_row.connect("notify::active", self._on_close_to_tray_changed)
        startup_group.add(close_to_tray_row)

        general_page.add(startup_group)

        # Refresh group
        refresh_group = Adw.PreferencesGroup(title="Refresh")

        refresh_row = Adw.SpinRow.new_with_range(10, 300, 10)
        refresh_row.set_title("Refresh Interval")
        refresh_row.set_subtitle("Seconds between automatic refreshes")
        refresh_row.set_value(self.settings.refresh_interval)
        refresh_row.connect("notify::value", self._on_refresh_interval_changed)
        refresh_group.add(refresh_row)

        general_page.add(refresh_group)

        # Notifications group
        notif_group = Adw.PreferencesGroup(title="Notifications")

        notif_enabled = Adw.SwitchRow(title="Enable Notifications")
        notif_enabled.set_active(self.settings.notifications.enabled)
        notif_enabled.connect("notify::active", self._on_notif_enabled_changed)
        notif_group.add(notif_enabled)

        sound_enabled = Adw.SwitchRow(title="Notification Sound")
        sound_enabled.set_active(self.settings.notifications.sound_enabled)
        sound_enabled.connect("notify::active", self._on_sound_enabled_changed)
        notif_group.add(sound_enabled)

        # Notification backend dropdown
        backend_row = Adw.ComboRow(title="Backend")
        backend_row.set_subtitle("Method for sending notifications")
        backend_model = Gtk.StringList.new(["Auto (Recommended)", "D-Bus", "notify-send"])
        backend_row.set_model(backend_model)
        # Map setting value to index
        backend_map = {"auto": 0, "dbus": 1, "notify-send": 2}
        backend_row.set_selected(backend_map.get(self.settings.notifications.backend, 0))
        backend_row.connect("notify::selected", self._on_notif_backend_changed)
        notif_group.add(backend_row)

        # Test notification button
        test_row = Adw.ActionRow(title="Test Notification")
        test_row.set_subtitle("Send a test notification")
        test_button = Gtk.Button(label="Test")
        test_button.set_valign(Gtk.Align.CENTER)
        test_button.connect("clicked", self._on_test_notification_clicked)
        test_row.add_suffix(test_button)
        notif_group.add(test_row)

        general_page.add(notif_group)

        # Appearance group
        appearance_group = Adw.PreferencesGroup(title="Appearance")

        style_row = Adw.ComboRow(title="Style")
        style_row.set_subtitle("UI layout style")
        style_model = Gtk.StringList.new(["Default", "Compact 1", "Compact 2", "Compact 3"])
        style_row.set_model(style_model)
        style_row.set_selected(self.settings.ui_style)
        style_row.connect("notify::selected", self._on_style_changed)
        appearance_group.add(style_row)

        platform_colors_row = Adw.SwitchRow(title="Platform Colors")
        platform_colors_row.set_subtitle("Color channel names by platform (purple for Twitch, green for Kick)")
        platform_colors_row.set_active(self.settings.platform_colors)
        platform_colors_row.connect("notify::active", self._on_platform_colors_changed)
        appearance_group.add(platform_colors_row)

        general_page.add(appearance_group)

        # Channel Information group
        info_group = Adw.PreferencesGroup(title="Channel Information")
        info_group.set_description("Show or hide information on channel rows")

        show_live_duration_row = Adw.SwitchRow(title="Live Duration")
        show_live_duration_row.set_subtitle("Show how long stream has been live (hh:mm or dd:hh:mm)")
        show_live_duration_row.set_active(self.settings.channel_info.show_live_duration)
        show_live_duration_row.connect("notify::active", self._on_show_live_duration_changed)
        info_group.add(show_live_duration_row)

        show_viewers_row = Adw.SwitchRow(title="Viewer Count")
        show_viewers_row.set_subtitle("Show number of viewers for live streams")
        show_viewers_row.set_active(self.settings.channel_info.show_viewers)
        show_viewers_row.connect("notify::active", self._on_show_viewers_changed)
        info_group.add(show_viewers_row)

        general_page.add(info_group)

        # Channel Icons group
        icons_group = Adw.PreferencesGroup(title="Channel Icons")
        icons_group.set_description("Show or hide icons on channel rows")

        show_platform_row = Adw.SwitchRow(title="Platform Icon")
        show_platform_row.set_subtitle("Show T/Y/K indicator for Twitch/YouTube/Kick")
        show_platform_row.set_active(self.settings.channel_icons.show_platform)
        show_platform_row.connect("notify::active", self._on_show_platform_changed)
        icons_group.add(show_platform_row)

        show_play_row = Adw.SwitchRow(title="Play Button")
        show_play_row.set_subtitle("Launch stream in player")
        show_play_row.set_active(self.settings.channel_icons.show_play)
        show_play_row.connect("notify::active", self._on_show_play_changed)
        icons_group.add(show_play_row)

        show_favorite_row = Adw.SwitchRow(title="Favorite Button")
        show_favorite_row.set_subtitle("Mark channel as favorite")
        show_favorite_row.set_active(self.settings.channel_icons.show_favorite)
        show_favorite_row.connect("notify::active", self._on_show_favorite_changed)
        icons_group.add(show_favorite_row)

        show_chat_row = Adw.SwitchRow(title="Chat Button")
        show_chat_row.set_subtitle("Open chat in browser")
        show_chat_row.set_active(self.settings.channel_icons.show_chat)
        show_chat_row.connect("notify::active", self._on_show_chat_changed)
        icons_group.add(show_chat_row)

        show_browser_row = Adw.SwitchRow(title="Browser Button")
        show_browser_row.set_subtitle("Open channel page in browser")
        show_browser_row.set_active(self.settings.channel_icons.show_browser)
        show_browser_row.connect("notify::active", self._on_show_browser_changed)
        icons_group.add(show_browser_row)

        general_page.add(icons_group)

        # Window group
        window_group = Adw.PreferencesGroup(title="Window")

        self._width_row = Adw.SpinRow.new_with_range(400, 3840, 10)
        self._width_row.set_title("Window Width")
        self._width_row.set_value(self.settings.window.width)
        self._width_row.connect("notify::value", self._on_window_width_changed)
        window_group.add(self._width_row)

        self._height_row = Adw.SpinRow.new_with_range(300, 2160, 10)
        self._height_row.set_title("Window Height")
        self._height_row.set_value(self.settings.window.height)
        self._height_row.connect("notify::value", self._on_window_height_changed)
        window_group.add(self._height_row)

        apply_size_btn = Gtk.Button(label="Apply Size")
        apply_size_btn.set_halign(Gtk.Align.END)
        apply_size_btn.set_margin_top(8)
        apply_size_btn.connect("clicked", self._on_apply_window_size)
        window_group.add(apply_size_btn)

        general_page.add(window_group)
        self.add(general_page)

        # Streamlink page
        streamlink_page = Adw.PreferencesPage(
            title="Streamlink", icon_name="video-display-symbolic"
        )

        # Streamlink settings group
        streamlink_group = Adw.PreferencesGroup(title="Streamlink")

        streamlink_enabled = Adw.SwitchRow(title="Use Streamlink")
        streamlink_enabled.set_subtitle("Launch streams in external player")
        streamlink_enabled.set_active(self.settings.streamlink.enabled)
        streamlink_enabled.connect("notify::active", self._on_streamlink_enabled_changed)
        streamlink_group.add(streamlink_enabled)

        streamlink_path_row = Adw.EntryRow(title="Streamlink Path")
        streamlink_path_row.set_text(self.settings.streamlink.path or "streamlink")
        streamlink_path_row.connect("notify::text", self._on_streamlink_path_changed)
        streamlink_group.add(streamlink_path_row)

        # Hint for streamlink path
        streamlink_path_hint = Gtk.Label(label="e.g. streamlink, /usr/bin/streamlink, ~/.local/bin/streamlink")
        streamlink_path_hint.set_halign(Gtk.Align.START)
        streamlink_path_hint.set_margin_start(16)
        streamlink_path_hint.set_margin_bottom(8)
        streamlink_path_hint.add_css_class("caption")
        streamlink_path_hint.add_css_class("dim-label")
        streamlink_path_hint.set_attributes(self._get_italic_attrs())
        streamlink_group.add(streamlink_path_hint)

        streamlink_args_row = Adw.EntryRow(title="Streamlink Arguments")
        streamlink_args_row.set_text(self.settings.streamlink.additional_args or "")
        streamlink_args_row.connect("notify::text", self._on_streamlink_args_changed)
        streamlink_group.add(streamlink_args_row)

        # Hint for streamlink args
        streamlink_args_hint = Gtk.Label(label="e.g. --twitch-low-latency --twitch-disable-ads")
        streamlink_args_hint.set_halign(Gtk.Align.START)
        streamlink_args_hint.set_margin_start(16)
        streamlink_args_hint.set_margin_bottom(8)
        streamlink_args_hint.add_css_class("caption")
        streamlink_args_hint.add_css_class("dim-label")
        streamlink_args_hint.set_attributes(self._get_italic_attrs())
        streamlink_group.add(streamlink_args_hint)

        streamlink_page.add(streamlink_group)

        # Player settings group
        player_group = Adw.PreferencesGroup(title="Player")

        player_row = Adw.EntryRow(title="Player Path")
        player_row.set_text(self.settings.streamlink.player or "mpv")
        player_row.connect("notify::text", self._on_player_changed)
        player_group.add(player_row)

        # Hint for player path
        player_path_hint = Gtk.Label(label="e.g. mpv, vlc, /usr/bin/mpv, /usr/bin/vlc")
        player_path_hint.set_halign(Gtk.Align.START)
        player_path_hint.set_margin_start(16)
        player_path_hint.set_margin_bottom(8)
        player_path_hint.add_css_class("caption")
        player_path_hint.add_css_class("dim-label")
        player_path_hint.set_attributes(self._get_italic_attrs())
        player_group.add(player_path_hint)

        player_args_row = Adw.EntryRow(title="Player Arguments")
        player_args_row.set_text(self.settings.streamlink.player_args or "")
        player_args_row.connect("notify::text", self._on_player_args_changed)
        player_group.add(player_args_row)

        # Hint for player args
        player_args_hint = Gtk.Label(label="e.g. --fullscreen --volume=80 --ontop")
        player_args_hint.set_halign(Gtk.Align.START)
        player_args_hint.set_margin_start(16)
        player_args_hint.set_margin_bottom(8)
        player_args_hint.add_css_class("caption")
        player_args_hint.add_css_class("dim-label")
        player_args_hint.set_attributes(self._get_italic_attrs())
        player_group.add(player_args_hint)

        streamlink_page.add(player_group)
        self.add(streamlink_page)

        # Chat page
        chat_page = Adw.PreferencesPage(
            title="Chat", icon_name="user-available-symbolic"
        )

        # Chat settings group
        chat_group = Adw.PreferencesGroup(title="Chat")

        chat_enabled = Adw.SwitchRow(title="Enable Chat")
        chat_enabled.set_subtitle("Show chat button on stream rows")
        chat_enabled.set_active(self.settings.chat.enabled)
        chat_enabled.connect("notify::active", self._on_chat_enabled_changed)
        chat_group.add(chat_enabled)

        auto_open = Adw.SwitchRow(title="Auto-open Chat")
        auto_open.set_subtitle("Automatically open chat when launching a stream")
        auto_open.set_active(self.settings.chat.auto_open)
        auto_open.connect("notify::active", self._on_chat_auto_open_changed)
        chat_group.add(auto_open)

        chat_page.add(chat_group)

        # Chat Application group
        app_group = Adw.PreferencesGroup(title="Chat Application")

        # Chat application dropdown (Browser for now, later Chatterino/Chatty)
        app_row = Adw.ComboRow(title="Application")
        app_row.set_subtitle("Application to open chat with")
        app_model = Gtk.StringList.new(["Browser"])
        app_row.set_model(app_model)
        app_row.set_selected(0)  # Browser is only option for now
        app_row.connect("notify::selected", self._on_chat_app_changed)
        app_group.add(app_row)

        # Browser selection (only visible when Browser is selected)
        self._browser_row = Adw.ComboRow(title="Browser")
        self._browser_row.set_subtitle("Browser to open chat in")
        browser_model = Gtk.StringList.new(["System Default", "Chrome", "Chromium", "Edge", "Firefox"])
        self._browser_row.set_model(browser_model)
        # Map setting value to index
        browser_map = {"default": 0, "chrome": 1, "chromium": 2, "edge": 3, "firefox": 4}
        self._browser_row.set_selected(browser_map.get(self.settings.chat.browser, 0))
        self._browser_row.connect("notify::selected", self._on_chat_browser_changed)
        app_group.add(self._browser_row)

        # New window option (only visible when Browser is selected)
        self._new_window_row = Adw.SwitchRow(title="Open in New Window")
        self._new_window_row.set_subtitle("Open chat in a new browser window instead of a tab")
        self._new_window_row.set_active(self.settings.chat.new_window)
        self._new_window_row.connect("notify::active", self._on_chat_new_window_changed)
        app_group.add(self._new_window_row)

        chat_page.add(app_group)

        # URL type group (only visible when Browser is selected)
        self._url_group = Adw.PreferencesGroup(title="Chat URL")

        url_row = Adw.ComboRow(title="Chat URL Type")
        url_row.set_subtitle("Style of Twitch chat page to open")
        url_model = Gtk.StringList.new(["Popout (Recommended)", "Embedded", "Default (Legacy)"])
        url_row.set_model(url_model)
        url_row.set_selected(self.settings.chat.url_type)
        url_row.connect("notify::selected", self._on_chat_url_type_changed)
        self._url_group.add(url_row)

        # URL type hints
        url_hint = Gtk.Label()
        url_hint.set_markup(
            "<small><i>Popout: twitch.tv/popout/{channel}/chat\n"
            "Embedded: twitch.tv/embed/{channel}/chat\n"
            "Default: twitch.tv/{channel}/chat</i></small>"
        )
        url_hint.set_halign(Gtk.Align.START)
        url_hint.set_margin_start(16)
        url_hint.add_css_class("dim-label")
        self._url_group.add(url_hint)

        chat_page.add(self._url_group)
        self.add(chat_page)

        # Accounts page
        accounts_page = Adw.PreferencesPage(
            title="Accounts", icon_name="system-users-symbolic"
        )

        # Twitch group
        twitch_group = Adw.PreferencesGroup(title="Twitch")

        # Status row showing auth state
        self._twitch_status_row = Adw.ActionRow(title="Status")
        self._twitch_status_row.set_subtitle("Checking...")
        twitch_group.add(self._twitch_status_row)

        # Login/Import row
        twitch_action_row = Adw.ActionRow(title="Account Actions")

        self._twitch_login_btn = Gtk.Button(label="Login")
        self._twitch_login_btn.add_css_class("suggested-action")
        self._twitch_login_btn.set_valign(Gtk.Align.CENTER)
        self._twitch_login_btn.connect("clicked", self._on_twitch_login_clicked)
        twitch_action_row.add_suffix(self._twitch_login_btn)

        self._twitch_import_btn = Gtk.Button(label="Import Follows")
        self._twitch_import_btn.set_valign(Gtk.Align.CENTER)
        self._twitch_import_btn.connect("clicked", self._on_twitch_import_clicked)
        self._twitch_import_btn.set_sensitive(False)
        twitch_action_row.add_suffix(self._twitch_import_btn)

        self._twitch_logout_btn = Gtk.Button(label="Logout")
        self._twitch_logout_btn.add_css_class("destructive-action")
        self._twitch_logout_btn.set_valign(Gtk.Align.CENTER)
        self._twitch_logout_btn.connect("clicked", self._on_twitch_logout_clicked)
        self._twitch_logout_btn.set_visible(False)
        twitch_action_row.add_suffix(self._twitch_logout_btn)

        twitch_group.add(twitch_action_row)
        accounts_page.add(twitch_group)

        # Kick group
        kick_group = Adw.PreferencesGroup(title="Kick")
        kick_group.set_description(
            "Kick's API doesn't support importing follows.\n"
            "Add Kick channels manually using the + button."
        )
        accounts_page.add(kick_group)

        # YouTube group
        youtube_group = Adw.PreferencesGroup(title="YouTube")
        youtube_group.set_description(
            "YouTube's API doesn't support importing follows.\n"
            "Add YouTube channels manually using the + button."
        )
        accounts_page.add(youtube_group)

        self.add(accounts_page)

        # Check auth status after window is shown
        GLib.idle_add(self._check_twitch_auth_status)

    def _get_italic_attrs(self) -> Pango.AttrList:
        """Get Pango attributes for italic text."""
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_style_new(Pango.Style.ITALIC))
        return attrs

    def _on_refresh_interval_changed(self, row: Adw.SpinRow, param) -> None:
        self.settings.refresh_interval = int(row.get_value())

    def _on_notif_enabled_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.notifications.enabled = row.get_active()

    def _on_sound_enabled_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.notifications.sound_enabled = row.get_active()

    def _on_notif_backend_changed(self, row: Adw.ComboRow, param) -> None:
        backend_map = {0: "auto", 1: "dbus", 2: "notify-send"}
        self.settings.notifications.backend = backend_map.get(row.get_selected(), "auto")
        self.settings.save()
        # Update notifier backend
        if hasattr(self.parent_window.app, 'notifier') and self.parent_window.app.notifier:
            self.parent_window.app.notifier.update_settings(self.settings.notifications)

    def _on_test_notification_clicked(self, button: Gtk.Button) -> None:
        """Send a test notification."""
        import asyncio
        import threading

        def send_test():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                if hasattr(self.parent_window.app, 'notifier') and self.parent_window.app.notifier:
                    success = loop.run_until_complete(
                        self.parent_window.app.notifier.send_test_notification()
                    )
                    if success:
                        GLib.idle_add(lambda: (self.parent_window.show_toast("Test notification sent"), None)[1])
                    else:
                        GLib.idle_add(lambda: (self.parent_window.show_toast("Failed to send notification"), None)[1])
                else:
                    GLib.idle_add(lambda: (self.parent_window.show_toast("Notifier not available"), None)[1])
            except Exception as e:
                GLib.idle_add(lambda: (self.parent_window.show_toast(f"Error: {e}"), None)[1])
            finally:
                loop.close()

        thread = threading.Thread(target=send_test, daemon=True)
        thread.start()

    def _on_autostart_changed(self, row: Adw.SwitchRow, param) -> None:
        """Handle autostart toggle change."""
        from ..core.autostart import set_autostart
        enabled = row.get_active()
        if set_autostart(enabled):
            self.settings.autostart = enabled
            self.settings.save()
        else:
            # Failed to set autostart, revert the toggle
            row.set_active(not enabled)
            self.parent_window.show_toast("Failed to update autostart setting")

    def _on_close_to_tray_changed(self, row: Adw.SwitchRow, param) -> None:
        """Handle close to tray toggle change."""
        self.settings.close_to_tray = row.get_active()
        self.settings.close_to_tray_asked = True  # User has explicitly set preference
        self.settings.save()

    def _on_streamlink_enabled_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.streamlink.enabled = row.get_active()

    def _on_player_changed(self, row: Adw.EntryRow, param) -> None:
        self.settings.streamlink.player = row.get_text()

    def _on_player_args_changed(self, row: Adw.EntryRow, param) -> None:
        self.settings.streamlink.player_args = row.get_text()

    def _on_streamlink_path_changed(self, row: Adw.EntryRow, param) -> None:
        self.settings.streamlink.path = row.get_text()

    def _on_streamlink_args_changed(self, row: Adw.EntryRow, param) -> None:
        self.settings.streamlink.additional_args = row.get_text()

    def _on_style_changed(self, row: Adw.ComboRow, param) -> None:
        self.settings.ui_style = row.get_selected()
        self.settings.save()
        # Apply style immediately
        self.parent_window.apply_style()

    def _on_platform_colors_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.platform_colors = row.get_active()
        self.settings.save()
        # Refresh stream list to apply colors
        self.parent_window.refresh_stream_list()

    def _on_show_live_duration_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_info.show_live_duration = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_viewers_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_info.show_viewers = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_platform_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_icons.show_platform = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_play_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_icons.show_play = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_favorite_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_icons.show_favorite = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_chat_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_icons.show_chat = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_show_browser_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.channel_icons.show_browser = row.get_active()
        self.settings.save()
        self.parent_window.refresh_stream_list()

    def _on_window_width_changed(self, row: Adw.SpinRow, param) -> None:
        self.settings.window.width = int(row.get_value())

    def _on_window_height_changed(self, row: Adw.SpinRow, param) -> None:
        self.settings.window.height = int(row.get_value())

    def _on_apply_window_size(self, button: Gtk.Button) -> None:
        width = int(self._width_row.get_value())
        height = int(self._height_row.get_value())
        self.settings.window.width = width
        self.settings.window.height = height
        self.settings.save()
        self.parent_window.set_default_size(width, height)
        # Also resize the current window
        self.parent_window.set_size_request(width, height)

    def _on_chat_enabled_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.chat.enabled = row.get_active()
        self.settings.save()
        # Refresh to show/hide chat buttons
        self.parent_window.refresh_stream_list()

    def _on_chat_auto_open_changed(self, row: Adw.SwitchRow, param) -> None:
        self.settings.chat.auto_open = row.get_active()
        self.settings.save()

    def _on_chat_app_changed(self, row: Adw.ComboRow, param) -> None:
        """Handle chat application selection change."""
        selected = row.get_selected()
        # 0 = Browser (show browser options), future: 1 = Chatterino, 2 = Chatty
        is_browser = selected == 0
        self._browser_row.set_visible(is_browser)
        self._new_window_row.set_visible(is_browser)
        self._url_group.set_visible(is_browser)

    def _on_chat_new_window_changed(self, row: Adw.SwitchRow, param) -> None:
        """Handle chat new window setting change."""
        self.settings.chat.new_window = row.get_active()
        self.settings.save()
        # Update chat launcher settings
        self.parent_window._chat_launcher.settings = self.settings.chat

    def _on_chat_browser_changed(self, row: Adw.ComboRow, param) -> None:
        browser_map = {0: "default", 1: "chrome", 2: "chromium", 3: "edge", 4: "firefox"}
        self.settings.chat.browser = browser_map.get(row.get_selected(), "default")
        self.settings.save()
        # Update chat launcher settings
        self.parent_window._chat_launcher.settings = self.settings.chat

    def _on_chat_url_type_changed(self, row: Adw.ComboRow, param) -> None:
        self.settings.chat.url_type = row.get_selected()
        self.settings.save()
        # Update chat launcher settings
        self.parent_window._chat_launcher.settings = self.settings.chat

    def _check_twitch_auth_status(self) -> bool:
        """Check and update Twitch auth status."""
        app = self.parent_window.app
        if app.monitor and app._loop:
            twitch = app.monitor.get_client(StreamPlatform.TWITCH)

            async def check():
                return await twitch.is_authorized()

            try:
                is_auth = app._loop.run_until_complete(check())
            except RuntimeError:
                # Loop might be running, try later
                return True

            if is_auth:
                self._twitch_status_row.set_subtitle("Logged in")
                self._twitch_login_btn.set_visible(False)
                self._twitch_import_btn.set_sensitive(True)
                self._twitch_logout_btn.set_visible(True)
            else:
                self._twitch_status_row.set_subtitle("Not logged in")
                self._twitch_login_btn.set_visible(True)
                self._twitch_import_btn.set_sensitive(False)
                self._twitch_logout_btn.set_visible(False)

        return False  # Don't repeat

    def _on_twitch_login_clicked(self, button: Gtk.Button) -> None:
        """Handle Twitch login button click."""
        app = self.parent_window.app
        if app.monitor and app._loop:
            twitch = app.monitor.get_client(StreamPlatform.TWITCH)

            # Show logging in state
            self._twitch_status_row.set_subtitle("Logging in...")
            button.set_sensitive(False)

            import threading

            def login_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    twitch._session = None

                    async def do_oauth():
                        return await twitch.oauth_login(timeout=300)

                    success = loop.run_until_complete(do_oauth())

                    if success:
                        GLib.idle_add(lambda: app.settings.save())
                        GLib.idle_add(self._check_twitch_auth_status)
                        GLib.idle_add(lambda: self.parent_window.show_toast("Twitch login successful!"))
                    else:
                        GLib.idle_add(lambda: self._twitch_status_row.set_subtitle("Login failed"))
                        GLib.idle_add(lambda: button.set_sensitive(True))
                        GLib.idle_add(lambda: self.parent_window.show_toast("Twitch login failed or timed out"))
                finally:
                    twitch._session = None
                    loop.close()

            thread = threading.Thread(target=login_thread, daemon=True)
            thread.start()

    def _on_twitch_logout_clicked(self, button: Gtk.Button) -> None:
        """Handle Twitch logout button click."""
        app = self.parent_window.app
        if app.monitor:
            twitch = app.monitor.get_client(StreamPlatform.TWITCH)
            twitch.logout()
            app.settings.save()
            self._check_twitch_auth_status()
            self.parent_window.show_toast("Logged out of Twitch")

    def _on_twitch_import_clicked(self, button: Gtk.Button) -> None:
        """Handle Twitch import button click."""
        # Close preferences and open import dialog
        self.close()
        self.parent_window.show_import_follows_dialog()


