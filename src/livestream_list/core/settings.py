"""Settings management for Livestream List."""

import json
import os
import tempfile
from dataclasses import dataclass, field

# Import ThemeMode here to avoid circular imports - it's defined in gui.theme
# but we need a local definition for settings serialization
from enum import Enum as _Enum
from pathlib import Path

from appdirs import user_config_dir, user_data_dir

from .models import LaunchMethod, SortMode, StreamQuality, UIStyle


class ThemeMode(str, _Enum):
    """Theme mode options."""

    AUTO = "auto"  # Follow system preference
    LIGHT = "light"
    DARK = "dark"
    HIGH_CONTRAST = "high_contrast"
    CUSTOM = "custom"  # User-defined or built-in named theme


APP_NAME = "livestream-list-qt"
APP_AUTHOR = "livestream-list-qt"


def get_config_dir() -> Path:
    """Get the configuration directory."""
    path = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Get the data directory."""
    path = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class StreamlinkSettings:
    """Streamlink-related settings."""

    enabled: bool = True
    path: str = "streamlink"
    player: str = "mpv"
    player_args: str = (
        "--cache=yes --demuxer-readahead-secs=5 --demuxer-max-bytes=50MiB --cache-pause=no"
    )
    default_quality: StreamQuality = StreamQuality.SOURCE
    additional_args: str = "--twitch-low-latency --kick-low-latency"

    # Per-platform launch method (streamlink or yt-dlp)
    twitch_launch_method: LaunchMethod = LaunchMethod.STREAMLINK
    youtube_launch_method: LaunchMethod = LaunchMethod.YT_DLP
    kick_launch_method: LaunchMethod = LaunchMethod.STREAMLINK


@dataclass
class NotificationSettings:
    """Notification-related settings."""

    enabled: bool = True
    sound_enabled: bool = False
    show_game: bool = True
    show_title: bool = True
    excluded_channels: list[str] = field(default_factory=list)
    backend: str = "auto"  # auto, dbus, notify-send
    custom_sound_path: str = ""  # empty = system default
    urgency: str = "normal"  # low, normal, critical
    timeout_seconds: int = 0  # 0 = system default
    platform_filter: list[str] = field(default_factory=lambda: ["twitch", "youtube", "kick"])
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"  # HH:MM 24h
    quiet_hours_end: str = "08:00"
    raid_notifications_enabled: bool = True
    mention_notifications_enabled: bool = True
    mention_custom_sound_path: str = ""  # empty = bell.oga default


@dataclass
class TwitchSettings:
    """Twitch API settings."""

    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    login_name: str = ""  # Twitch username of the logged-in account


@dataclass
class YouTubeSettings:
    """YouTube API settings."""

    api_key: str = ""
    cookies: str = ""  # Browser cookies for chat sending (InnerTube)
    use_ytdlp_fallback: bool = True  # Fall back to yt-dlp if HTML scraping fails
    cookie_browser: str = ""  # Browser ID used for import (e.g. "firefox", "chrome")
    cookie_auto_refresh: bool = True  # Auto-refresh cookies when expired


@dataclass
class KickSettings:
    """Kick API settings for OAuth authentication."""

    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    login_name: str = ""  # Kick username of the logged-in account


@dataclass
class WindowSettings:
    """Window state settings."""

    width: int = 1000
    height: int = 700
    x: int | None = None
    y: int | None = None
    maximized: bool = False
    always_on_top: bool = False


@dataclass
class ChatWindowSettings:
    """Chat window position/size persistence."""

    width: int = 400
    height: int = 600
    x: int | None = None
    y: int | None = None


@dataclass
class ChatColorSettings:
    """Color settings for chat UI - separate instances for dark and light themes."""

    alt_row_color_even: str = "#00000000"  # AARRGGBB: transparent (default bg)
    alt_row_color_odd: str = "#0fffffff"  # AARRGGBB: white at ~6% opacity
    tab_active_color: str = "#6441a5"  # Twitch purple
    tab_inactive_color: str = "#16213e"  # Dark blue
    mention_highlight_color: str = "#33ff8800"  # AARRGGBB: orange at ~20% opacity
    banner_bg_color: str = "#16213e"  # Dark blue
    banner_text_color: str = "#cccccc"  # Light gray


# Default colors for light theme
def _default_light_colors() -> ChatColorSettings:
    return ChatColorSettings(
        alt_row_color_even="#00000000",  # transparent
        alt_row_color_odd="#0a000000",  # black at ~4% opacity
        tab_active_color="#6441a5",  # Twitch purple
        tab_inactive_color="#e0e0e8",  # Light gray-blue
        mention_highlight_color="#33ff8800",  # orange at ~20% opacity
        banner_bg_color="#e8e8f0",  # Light gray
        banner_text_color="#333333",  # Dark gray
    )


@dataclass
class BuiltinChatSettings:
    """Settings for the built-in chat client."""

    font_size: int = 13
    show_timestamps: bool = False
    timestamp_format: str = "12h"  # "12h" or "24h"
    show_badges: bool = True
    show_mod_badges: bool = True
    show_emotes: bool = True
    animate_emotes: bool = True
    line_spacing: int = 4
    max_messages: int = 1000
    emote_providers: list[str] = field(default_factory=lambda: ["7tv", "bttv", "ffz"])
    show_alternating_rows: bool = True
    show_metrics: bool = True
    blocked_users: list[str] = field(default_factory=list)  # "platform:user_id" strings
    blocked_user_names: dict[str, str] = field(default_factory=dict)  # user_key → display name
    highlight_keywords: list[str] = field(default_factory=list)
    user_nicknames: dict[str, str] = field(default_factory=dict)  # user_key → nickname
    user_nickname_display_names: dict[str, str] = field(default_factory=dict)  # user_key → original
    user_notes: dict[str, str] = field(default_factory=dict)  # user_key → note text
    user_note_display_names: dict[str, str] = field(default_factory=dict)  # user_key → display name
    use_platform_name_colors: bool = True
    # Banner settings (stream title + socials)
    show_stream_title: bool = True
    show_socials_banner: bool = True
    spellcheck_enabled: bool = True
    autocorrect_enabled: bool = True
    moderated_message_display: str = "strikethrough"  # strikethrough, truncated, hidden
    user_card_hover: bool = True
    always_on_top: bool = False
    window: ChatWindowSettings = field(default_factory=ChatWindowSettings)
    # Theme-specific color settings
    dark_colors: ChatColorSettings = field(default_factory=ChatColorSettings)
    light_colors: ChatColorSettings = field(default_factory=_default_light_colors)

    @property
    def ts_strftime(self) -> str:
        """Return strftime format string based on timestamp_format setting."""
        return "%I:%M %p" if self.timestamp_format == "12h" else "%H:%M"

    @property
    def ts_measure_text(self) -> str:
        """Return a representative text for measuring timestamp width."""
        return "00:00 AM" if self.timestamp_format == "12h" else "00:00"

    def get_colors(self, is_dark: bool) -> ChatColorSettings:
        """Get the color settings for the current theme mode.

        Args:
            is_dark: True for dark mode, False for light mode.

        Returns:
            The appropriate ChatColorSettings for the theme.
        """
        return self.dark_colors if is_dark else self.light_colors


@dataclass
class ChatLoggingSettings:
    """Chat logging and history settings."""

    enabled: bool = False  # Off by default
    max_disk_mb: int = 100
    log_format: str = "jsonl"  # "jsonl" or "text"
    load_history_on_open: bool = True
    history_lines: int = 100


@dataclass
class ChatSettings:
    """Chat-related settings."""

    enabled: bool = True
    mode: str = "builtin"  # "browser" or "builtin"
    browser: str = "default"  # default, chrome, chromium, edge, firefox
    url_type: int = 0  # 0=Popout, 1=Embedded, 2=Default (legacy)
    auto_open: bool = False  # Auto-open chat when launching stream
    new_window: bool = True  # Open chat in new window instead of tab
    recent_channels: list[str] = field(default_factory=list)  # recent chat channel keys
    builtin: BuiltinChatSettings = field(default_factory=BuiltinChatSettings)
    logging: ChatLoggingSettings = field(default_factory=ChatLoggingSettings)


@dataclass
class PerformanceSettings:
    """Performance-related settings for API concurrency."""

    youtube_concurrency: int = 10  # Concurrent HTTP requests for YouTube (I/O-bound)
    kick_concurrency: int = 10  # Concurrent API calls for Kick


@dataclass
class ChannelInfoSettings:
    """Channel row information visibility settings."""

    show_live_duration: bool = True
    show_viewers: bool = True


@dataclass
class ChannelIconSettings:
    """Channel row icon visibility settings."""

    show_platform: bool = True
    show_play: bool = True
    show_favorite: bool = True
    show_chat: bool = True
    show_browser: bool = True


@dataclass
class Settings:
    """Application settings."""

    # General
    refresh_interval: int = 60  # seconds
    minimize_to_tray: bool = True
    start_minimized: bool = False
    check_for_updates: bool = True
    autostart: bool = False  # Launch on system startup
    close_to_tray: bool = False  # Minimize to tray instead of closing
    close_to_tray_asked: bool = False  # Whether user has been asked about close behavior
    emote_cache_mb: int = 500  # Disk cache size for emotes (MB)

    # UI preferences
    sort_mode: SortMode = SortMode.VIEWERS
    hide_offline: bool = False
    favorites_only: bool = False
    ui_style: UIStyle = UIStyle.DEFAULT
    platform_colors: bool = True  # Color platform icons and channel names
    font_size: int = 0  # 0 = system default, otherwise point size for stream list
    theme_mode: ThemeMode = ThemeMode.AUTO  # auto, light, or dark
    custom_theme_slug: str = ""  # slug of active custom/built-in named theme
    custom_theme_base: str = "dark"  # "dark" or "light" for CUSTOM mode is_dark_mode()

    # Platform settings
    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)
    kick: KickSettings = field(default_factory=KickSettings)

    # Feature settings
    streamlink: StreamlinkSettings = field(default_factory=StreamlinkSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    window: WindowSettings = field(default_factory=WindowSettings)
    chat: ChatSettings = field(default_factory=ChatSettings)
    channel_info: ChannelInfoSettings = field(default_factory=ChannelInfoSettings)
    channel_icons: ChannelIconSettings = field(default_factory=ChannelIconSettings)
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        """Load settings from file."""
        from .credential_store import (
            KEY_KICK_ACCESS_TOKEN,
            KEY_KICK_REFRESH_TOKEN,
            KEY_TWITCH_ACCESS_TOKEN,
            KEY_TWITCH_REFRESH_TOKEN,
            KEY_YOUTUBE_COOKIES,
            get_secret,
            is_available,
            store_secret,
        )

        if path is None:
            path = get_config_dir() / "settings.json"

        if not path.exists():
            return cls()

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            settings = cls._from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return cls()

        # Load secrets from keyring (overrides JSON values)
        if is_available():
            kr_twitch_at = get_secret(KEY_TWITCH_ACCESS_TOKEN)
            kr_twitch_rt = get_secret(KEY_TWITCH_REFRESH_TOKEN)
            kr_yt_cookies = get_secret(KEY_YOUTUBE_COOKIES)
            kr_kick_at = get_secret(KEY_KICK_ACCESS_TOKEN)
            kr_kick_rt = get_secret(KEY_KICK_REFRESH_TOKEN)

            # Migrate: if JSON has secrets but keyring doesn't, store in keyring
            needs_resave = False
            if settings.twitch.access_token and not kr_twitch_at:
                store_secret(KEY_TWITCH_ACCESS_TOKEN, settings.twitch.access_token)
                needs_resave = True
            elif kr_twitch_at:
                settings.twitch.access_token = kr_twitch_at

            if settings.twitch.refresh_token and not kr_twitch_rt:
                store_secret(KEY_TWITCH_REFRESH_TOKEN, settings.twitch.refresh_token)
                needs_resave = True
            elif kr_twitch_rt:
                settings.twitch.refresh_token = kr_twitch_rt

            if settings.youtube.cookies and not kr_yt_cookies:
                store_secret(KEY_YOUTUBE_COOKIES, settings.youtube.cookies)
                needs_resave = True
            elif kr_yt_cookies:
                settings.youtube.cookies = kr_yt_cookies

            if settings.kick.access_token and not kr_kick_at:
                store_secret(KEY_KICK_ACCESS_TOKEN, settings.kick.access_token)
                needs_resave = True
            elif kr_kick_at:
                settings.kick.access_token = kr_kick_at

            if settings.kick.refresh_token and not kr_kick_rt:
                store_secret(KEY_KICK_REFRESH_TOKEN, settings.kick.refresh_token)
                needs_resave = True
            elif kr_kick_rt:
                settings.kick.refresh_token = kr_kick_rt

            # Re-save to clear secrets from JSON after migration
            if needs_resave:
                settings.save(path)

        return settings

    def save(self, path: Path | None = None) -> None:
        """Save settings to file."""
        from .credential_store import (
            KEY_KICK_ACCESS_TOKEN,
            KEY_KICK_REFRESH_TOKEN,
            KEY_TWITCH_ACCESS_TOKEN,
            KEY_TWITCH_REFRESH_TOKEN,
            KEY_YOUTUBE_COOKIES,
            is_available,
            secure_file_permissions,
            store_secret,
        )

        if path is None:
            path = get_config_dir() / "settings.json"

        path.parent.mkdir(parents=True, exist_ok=True)

        # Store secrets in keyring
        use_keyring = is_available()
        if use_keyring:
            store_secret(KEY_TWITCH_ACCESS_TOKEN, self.twitch.access_token)
            store_secret(KEY_TWITCH_REFRESH_TOKEN, self.twitch.refresh_token)
            store_secret(KEY_YOUTUBE_COOKIES, self.youtube.cookies)
            store_secret(KEY_KICK_ACCESS_TOKEN, self.kick.access_token)
            store_secret(KEY_KICK_REFRESH_TOKEN, self.kick.refresh_token)

        # Atomic write: write to temp file then rename to prevent corruption on crash
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix="settings_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._to_dict(exclude_secrets=use_keyring), f, indent=2)
            os.replace(tmp_path, path)  # Atomic on POSIX
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        if not use_keyring:
            # Fallback: protect the file with restrictive permissions
            secure_file_permissions(str(path))

    @staticmethod
    def _validate_int(value, default: int, min_val: int = 0, max_val: int | None = None) -> int:
        """Validate and constrain an integer value."""
        if not isinstance(value, int):
            return default
        if value < min_val:
            return min_val
        if max_val is not None and value > max_val:
            return max_val
        return value

    @classmethod
    def _from_dict(cls, data: dict) -> "Settings":
        """Create Settings from a dictionary with validation."""
        settings = cls()

        # General settings with validation
        settings.refresh_interval = cls._validate_int(
            data.get("refresh_interval"), 60, min_val=10, max_val=3600
        )
        settings.minimize_to_tray = data.get("minimize_to_tray", settings.minimize_to_tray)
        settings.start_minimized = data.get("start_minimized", settings.start_minimized)
        settings.check_for_updates = data.get("check_for_updates", settings.check_for_updates)
        settings.autostart = data.get("autostart", settings.autostart)
        settings.close_to_tray = data.get("close_to_tray", settings.close_to_tray)
        settings.close_to_tray_asked = data.get("close_to_tray_asked", settings.close_to_tray_asked)
        settings.emote_cache_mb = cls._validate_int(
            data.get("emote_cache_mb"), 500, min_val=50, max_val=5000
        )

        # UI preferences
        old_sort_mode = data.get("sort_mode", settings.sort_mode.value)
        # Valid sort modes: 0=Name, 1=Viewers, 2=Playing, 3=Last Seen, 4=Time Live
        try:
            settings.sort_mode = SortMode(old_sort_mode)
        except ValueError:
            settings.sort_mode = SortMode.VIEWERS  # Default to Viewers
        settings.hide_offline = data.get("hide_offline", settings.hide_offline)
        settings.favorites_only = data.get("favorites_only", settings.favorites_only)
        old_ui_style = data.get("ui_style", settings.ui_style.value)
        try:
            settings.ui_style = UIStyle(old_ui_style)
        except ValueError:
            settings.ui_style = UIStyle.DEFAULT
        settings.platform_colors = data.get("platform_colors", settings.platform_colors)
        settings.font_size = data.get("font_size", settings.font_size)
        old_theme_mode = data.get("theme_mode", settings.theme_mode.value)
        try:
            settings.theme_mode = ThemeMode(old_theme_mode)
        except ValueError:
            settings.theme_mode = ThemeMode.AUTO
        settings.custom_theme_slug = data.get("custom_theme_slug", "")
        settings.custom_theme_base = data.get("custom_theme_base", "dark")

        # Twitch
        if "twitch" in data:
            t = data["twitch"]
            settings.twitch = TwitchSettings(
                client_id=t.get("client_id", ""),
                client_secret=t.get("client_secret", ""),
                access_token=t.get("access_token", ""),
                refresh_token=t.get("refresh_token", ""),
                login_name=t.get("login_name", ""),
            )

        # YouTube
        if "youtube" in data:
            yt = data["youtube"]
            settings.youtube = YouTubeSettings(
                api_key=yt.get("api_key", ""),
                cookies=yt.get("cookies", ""),
                use_ytdlp_fallback=yt.get("use_ytdlp_fallback", True),
                cookie_browser=yt.get("cookie_browser", ""),
                cookie_auto_refresh=yt.get("cookie_auto_refresh", True),
            )

        # Kick
        if "kick" in data:
            k = data["kick"]
            settings.kick = KickSettings(
                client_id=k.get("client_id", ""),
                client_secret=k.get("client_secret", ""),
                access_token=k.get("access_token", ""),
                refresh_token=k.get("refresh_token", ""),
                login_name=k.get("login_name", ""),
            )

        # Streamlink
        if "streamlink" in data:
            s = data["streamlink"]
            settings.streamlink = StreamlinkSettings(
                enabled=s.get("enabled", True),
                path=s.get("path", "streamlink"),
                player=s.get("player", "mpv"),
                player_args=s.get("player_args", ""),
                default_quality=StreamQuality(s.get("default_quality", "best")),
                additional_args=s.get("additional_args", ""),
                twitch_launch_method=LaunchMethod(s.get("twitch_launch_method", "streamlink")),
                youtube_launch_method=LaunchMethod(s.get("youtube_launch_method", "yt-dlp")),
                kick_launch_method=LaunchMethod(s.get("kick_launch_method", "streamlink")),
            )

        # Notifications
        if "notifications" in data:
            n = data["notifications"]
            settings.notifications = NotificationSettings(
                enabled=n.get("enabled", True),
                sound_enabled=n.get("sound_enabled", False),
                show_game=n.get("show_game", True),
                show_title=n.get("show_title", True),
                excluded_channels=n.get("excluded_channels", []),
                backend=n.get("backend", "auto"),
                custom_sound_path=n.get("custom_sound_path", ""),
                urgency=n.get("urgency", "normal"),
                timeout_seconds=cls._validate_int(
                    n.get("timeout_seconds"), 0, min_val=0, max_val=60
                ),
                platform_filter=n.get("platform_filter", ["twitch", "youtube", "kick"]),
                quiet_hours_enabled=n.get("quiet_hours_enabled", False),
                quiet_hours_start=n.get("quiet_hours_start", "22:00"),
                quiet_hours_end=n.get("quiet_hours_end", "08:00"),
                raid_notifications_enabled=n.get("raid_notifications_enabled", True),
                mention_notifications_enabled=n.get("mention_notifications_enabled", True),
                mention_custom_sound_path=n.get("mention_custom_sound_path", ""),
            )

        # Window (with validation for reasonable dimensions)
        if "window" in data:
            w = data["window"]
            settings.window = WindowSettings(
                width=cls._validate_int(w.get("width"), 1000, min_val=200, max_val=10000),
                height=cls._validate_int(w.get("height"), 700, min_val=200, max_val=10000),
                x=w.get("x"),
                y=w.get("y"),
                maximized=bool(w.get("maximized", False)),
                always_on_top=bool(w.get("always_on_top", False)),
            )

        # Chat
        if "chat" in data:
            c = data["chat"]
            builtin_data = c.get("builtin", {})
            window_data = builtin_data.get("window", {})
            chat_window = ChatWindowSettings(
                width=window_data.get("width", 400),
                height=window_data.get("height", 600),
                x=window_data.get("x"),
                y=window_data.get("y"),
            )
            # Load dark/light color settings (with migration from legacy single-color fields)
            dark_data = builtin_data.get("dark_colors", {})
            light_data = builtin_data.get("light_colors", {})

            # Default dark colors
            dark_defaults = ChatColorSettings()
            # Default light colors
            light_defaults = _default_light_colors()

            # Check for legacy fields and migrate to dark_colors if new structure not present
            if not dark_data:
                # Migrate legacy fields to dark colors
                dark_colors = ChatColorSettings(
                    alt_row_color_even=builtin_data.get(
                        "alt_row_color_even", dark_defaults.alt_row_color_even
                    ),
                    alt_row_color_odd=builtin_data.get(
                        "alt_row_color_odd", dark_defaults.alt_row_color_odd
                    ),
                    tab_active_color=builtin_data.get(
                        "tab_active_color", dark_defaults.tab_active_color
                    ),
                    tab_inactive_color=builtin_data.get(
                        "tab_inactive_color", dark_defaults.tab_inactive_color
                    ),
                    mention_highlight_color=builtin_data.get(
                        "mention_highlight_color", dark_defaults.mention_highlight_color
                    ),
                    banner_bg_color=builtin_data.get(
                        "banner_bg_color", dark_defaults.banner_bg_color
                    ),
                    banner_text_color=builtin_data.get(
                        "banner_text_color", dark_defaults.banner_text_color
                    ),
                )
            else:
                dark_colors = ChatColorSettings(
                    alt_row_color_even=dark_data.get(
                        "alt_row_color_even", dark_defaults.alt_row_color_even
                    ),
                    alt_row_color_odd=dark_data.get(
                        "alt_row_color_odd", dark_defaults.alt_row_color_odd
                    ),
                    tab_active_color=dark_data.get(
                        "tab_active_color", dark_defaults.tab_active_color
                    ),
                    tab_inactive_color=dark_data.get(
                        "tab_inactive_color", dark_defaults.tab_inactive_color
                    ),
                    mention_highlight_color=dark_data.get(
                        "mention_highlight_color", dark_defaults.mention_highlight_color
                    ),
                    banner_bg_color=dark_data.get("banner_bg_color", dark_defaults.banner_bg_color),
                    banner_text_color=dark_data.get(
                        "banner_text_color", dark_defaults.banner_text_color
                    ),
                )

            light_colors = ChatColorSettings(
                alt_row_color_even=light_data.get(
                    "alt_row_color_even", light_defaults.alt_row_color_even
                ),
                alt_row_color_odd=light_data.get(
                    "alt_row_color_odd", light_defaults.alt_row_color_odd
                ),
                tab_active_color=light_data.get(
                    "tab_active_color", light_defaults.tab_active_color
                ),
                tab_inactive_color=light_data.get(
                    "tab_inactive_color", light_defaults.tab_inactive_color
                ),
                mention_highlight_color=light_data.get(
                    "mention_highlight_color", light_defaults.mention_highlight_color
                ),
                banner_bg_color=light_data.get("banner_bg_color", light_defaults.banner_bg_color),
                banner_text_color=light_data.get(
                    "banner_text_color", light_defaults.banner_text_color
                ),
            )

            builtin = BuiltinChatSettings(
                font_size=cls._validate_int(
                    builtin_data.get("font_size"), 13, min_val=8, max_val=72
                ),
                show_timestamps=builtin_data.get("show_timestamps", False),
                timestamp_format=builtin_data.get("timestamp_format", "12h"),
                show_badges=builtin_data.get("show_badges", True),
                show_mod_badges=builtin_data.get("show_mod_badges", True),
                show_emotes=builtin_data.get("show_emotes", True),
                animate_emotes=builtin_data.get("animate_emotes", True),
                line_spacing=cls._validate_int(
                    builtin_data.get("line_spacing"), 4, min_val=0, max_val=20
                ),
                max_messages=cls._validate_int(
                    builtin_data.get("max_messages"), 1000, min_val=100, max_val=50000
                ),
                emote_providers=builtin_data.get("emote_providers", ["7tv", "bttv", "ffz"]),
                show_alternating_rows=builtin_data.get("show_alternating_rows", True),
                show_metrics=builtin_data.get("show_metrics", True),
                blocked_users=builtin_data.get("blocked_users", []),
                blocked_user_names=builtin_data.get("blocked_user_names", {}),
                highlight_keywords=builtin_data.get("highlight_keywords", []),
                user_nicknames=builtin_data.get("user_nicknames", {}),
                user_nickname_display_names=builtin_data.get("user_nickname_display_names", {}),
                user_notes=builtin_data.get("user_notes", {}),
                user_note_display_names=builtin_data.get("user_note_display_names", {}),
                use_platform_name_colors=builtin_data.get("use_platform_name_colors", True),
                show_stream_title=builtin_data.get("show_stream_title", True),
                show_socials_banner=builtin_data.get("show_socials_banner", True),
                spellcheck_enabled=builtin_data.get("spellcheck_enabled", True),
                autocorrect_enabled=builtin_data.get("autocorrect_enabled", True),
                user_card_hover=builtin_data.get("user_card_hover", True),
                always_on_top=builtin_data.get("always_on_top", False),
                moderated_message_display=builtin_data.get(
                    "moderated_message_display", "strikethrough"
                ),
                window=chat_window,
                dark_colors=dark_colors,
                light_colors=light_colors,
            )
            # Chat logging settings
            log_data = c.get("logging", {})
            chat_logging = ChatLoggingSettings(
                enabled=log_data.get("enabled", False),
                max_disk_mb=cls._validate_int(
                    log_data.get("max_disk_mb"), 100, min_val=10, max_val=5000
                ),
                log_format=log_data.get("log_format", "jsonl"),
                load_history_on_open=log_data.get("load_history_on_open", True),
                history_lines=cls._validate_int(
                    log_data.get("history_lines"), 100, min_val=10, max_val=1000
                ),
            )
            settings.chat = ChatSettings(
                enabled=c.get("enabled", True),
                mode=c.get("mode", "builtin"),
                browser=c.get("browser", "default"),
                url_type=c.get("url_type", 0),
                auto_open=c.get("auto_open", False),
                new_window=c.get("new_window", True),
                recent_channels=c.get("recent_channels", []),
                builtin=builtin,
                logging=chat_logging,
            )

        # Channel info
        if "channel_info" in data:
            cinfo = data["channel_info"]
            settings.channel_info = ChannelInfoSettings(
                show_live_duration=cinfo.get("show_live_duration", True),
                show_viewers=cinfo.get("show_viewers", True),
            )

        # Channel icons
        if "channel_icons" in data:
            ci = data["channel_icons"]
            settings.channel_icons = ChannelIconSettings(
                show_platform=ci.get("show_platform", True),
                show_play=ci.get("show_play", True),
                show_favorite=ci.get("show_favorite", True),
                show_chat=ci.get("show_chat", True),
                show_browser=ci.get("show_browser", True),
            )

        # Performance (with validation to prevent resource exhaustion)
        if "performance" in data:
            perf = data["performance"]
            settings.performance = PerformanceSettings(
                youtube_concurrency=cls._validate_int(
                    perf.get("youtube_concurrency"), 10, min_val=1, max_val=50
                ),
                kick_concurrency=cls._validate_int(
                    perf.get("kick_concurrency"), 10, min_val=1, max_val=50
                ),
            )

        return settings

    def _to_dict(self, exclude_secrets: bool = False) -> dict:
        """Convert Settings to a dictionary.

        If exclude_secrets is True, sensitive tokens/cookies are omitted
        (they are stored in the system keyring instead).
        """
        return {
            "refresh_interval": self.refresh_interval,
            "minimize_to_tray": self.minimize_to_tray,
            "start_minimized": self.start_minimized,
            "check_for_updates": self.check_for_updates,
            "autostart": self.autostart,
            "close_to_tray": self.close_to_tray,
            "close_to_tray_asked": self.close_to_tray_asked,
            "emote_cache_mb": self.emote_cache_mb,
            "sort_mode": self.sort_mode.value,
            "hide_offline": self.hide_offline,
            "favorites_only": self.favorites_only,
            "ui_style": self.ui_style.value,
            "platform_colors": self.platform_colors,
            "font_size": self.font_size,
            "theme_mode": self.theme_mode.value,
            "custom_theme_slug": self.custom_theme_slug,
            "custom_theme_base": self.custom_theme_base,
            "twitch": {
                "client_id": self.twitch.client_id,
                "client_secret": self.twitch.client_secret,
                "login_name": self.twitch.login_name,
                **(
                    {
                        "access_token": self.twitch.access_token,
                        "refresh_token": self.twitch.refresh_token,
                    }
                    if not exclude_secrets
                    else {}
                ),
            },
            "youtube": {
                "api_key": self.youtube.api_key,
                "use_ytdlp_fallback": self.youtube.use_ytdlp_fallback,
                "cookie_browser": self.youtube.cookie_browser,
                "cookie_auto_refresh": self.youtube.cookie_auto_refresh,
                **({"cookies": self.youtube.cookies} if not exclude_secrets else {}),
            },
            "kick": {
                "client_id": self.kick.client_id,
                "client_secret": self.kick.client_secret,
                "login_name": self.kick.login_name,
                **(
                    {
                        "access_token": self.kick.access_token,
                        "refresh_token": self.kick.refresh_token,
                    }
                    if not exclude_secrets
                    else {}
                ),
            },
            "streamlink": {
                "enabled": self.streamlink.enabled,
                "path": self.streamlink.path,
                "player": self.streamlink.player,
                "player_args": self.streamlink.player_args,
                "default_quality": self.streamlink.default_quality.value,
                "additional_args": self.streamlink.additional_args,
                "twitch_launch_method": self.streamlink.twitch_launch_method.value,
                "youtube_launch_method": self.streamlink.youtube_launch_method.value,
                "kick_launch_method": self.streamlink.kick_launch_method.value,
            },
            "notifications": {
                "enabled": self.notifications.enabled,
                "sound_enabled": self.notifications.sound_enabled,
                "show_game": self.notifications.show_game,
                "show_title": self.notifications.show_title,
                "excluded_channels": self.notifications.excluded_channels,
                "backend": self.notifications.backend,
                "custom_sound_path": self.notifications.custom_sound_path,
                "urgency": self.notifications.urgency,
                "timeout_seconds": self.notifications.timeout_seconds,
                "platform_filter": self.notifications.platform_filter,
                "quiet_hours_enabled": self.notifications.quiet_hours_enabled,
                "quiet_hours_start": self.notifications.quiet_hours_start,
                "quiet_hours_end": self.notifications.quiet_hours_end,
                "raid_notifications_enabled": self.notifications.raid_notifications_enabled,
                "mention_notifications_enabled": self.notifications.mention_notifications_enabled,
                "mention_custom_sound_path": self.notifications.mention_custom_sound_path,
            },
            "window": {
                "width": self.window.width,
                "height": self.window.height,
                "x": self.window.x,
                "y": self.window.y,
                "maximized": self.window.maximized,
                "always_on_top": self.window.always_on_top,
            },
            "chat": {
                "enabled": self.chat.enabled,
                "mode": self.chat.mode,
                "browser": self.chat.browser,
                "url_type": self.chat.url_type,
                "auto_open": self.chat.auto_open,
                "new_window": self.chat.new_window,
                "recent_channels": self.chat.recent_channels,
                "builtin": {
                    "font_size": self.chat.builtin.font_size,
                    "show_timestamps": self.chat.builtin.show_timestamps,
                    "timestamp_format": self.chat.builtin.timestamp_format,
                    "show_badges": self.chat.builtin.show_badges,
                    "show_mod_badges": self.chat.builtin.show_mod_badges,
                    "show_emotes": self.chat.builtin.show_emotes,
                    "animate_emotes": self.chat.builtin.animate_emotes,
                    "line_spacing": self.chat.builtin.line_spacing,
                    "max_messages": self.chat.builtin.max_messages,
                    "emote_providers": self.chat.builtin.emote_providers,
                    "show_alternating_rows": self.chat.builtin.show_alternating_rows,
                    "show_metrics": self.chat.builtin.show_metrics,
                    "blocked_users": self.chat.builtin.blocked_users,
                    "blocked_user_names": self.chat.builtin.blocked_user_names,
                    "highlight_keywords": self.chat.builtin.highlight_keywords,
                    "user_nicknames": self.chat.builtin.user_nicknames,
                    "user_nickname_display_names": self.chat.builtin.user_nickname_display_names,
                    "user_notes": self.chat.builtin.user_notes,
                    "user_note_display_names": self.chat.builtin.user_note_display_names,
                    "use_platform_name_colors": self.chat.builtin.use_platform_name_colors,
                    "show_stream_title": self.chat.builtin.show_stream_title,
                    "show_socials_banner": self.chat.builtin.show_socials_banner,
                    "spellcheck_enabled": self.chat.builtin.spellcheck_enabled,
                    "autocorrect_enabled": self.chat.builtin.autocorrect_enabled,
                    "user_card_hover": self.chat.builtin.user_card_hover,
                    "always_on_top": self.chat.builtin.always_on_top,
                    "moderated_message_display": self.chat.builtin.moderated_message_display,
                    "dark_colors": {
                        "alt_row_color_even": self.chat.builtin.dark_colors.alt_row_color_even,
                        "alt_row_color_odd": self.chat.builtin.dark_colors.alt_row_color_odd,
                        "tab_active_color": self.chat.builtin.dark_colors.tab_active_color,
                        "tab_inactive_color": self.chat.builtin.dark_colors.tab_inactive_color,
                        "mention_highlight_color": (
                            self.chat.builtin.dark_colors.mention_highlight_color
                        ),
                        "banner_bg_color": self.chat.builtin.dark_colors.banner_bg_color,
                        "banner_text_color": self.chat.builtin.dark_colors.banner_text_color,
                    },
                    "light_colors": {
                        "alt_row_color_even": self.chat.builtin.light_colors.alt_row_color_even,
                        "alt_row_color_odd": self.chat.builtin.light_colors.alt_row_color_odd,
                        "tab_active_color": self.chat.builtin.light_colors.tab_active_color,
                        "tab_inactive_color": self.chat.builtin.light_colors.tab_inactive_color,
                        "mention_highlight_color": (
                            self.chat.builtin.light_colors.mention_highlight_color
                        ),
                        "banner_bg_color": self.chat.builtin.light_colors.banner_bg_color,
                        "banner_text_color": self.chat.builtin.light_colors.banner_text_color,
                    },
                    "window": {
                        "width": self.chat.builtin.window.width,
                        "height": self.chat.builtin.window.height,
                        "x": self.chat.builtin.window.x,
                        "y": self.chat.builtin.window.y,
                    },
                },
                "logging": {
                    "enabled": self.chat.logging.enabled,
                    "max_disk_mb": self.chat.logging.max_disk_mb,
                    "log_format": self.chat.logging.log_format,
                    "load_history_on_open": self.chat.logging.load_history_on_open,
                    "history_lines": self.chat.logging.history_lines,
                },
            },
            "channel_info": {
                "show_live_duration": self.channel_info.show_live_duration,
                "show_viewers": self.channel_info.show_viewers,
            },
            "channel_icons": {
                "show_platform": self.channel_icons.show_platform,
                "show_play": self.channel_icons.show_play,
                "show_favorite": self.channel_icons.show_favorite,
                "show_chat": self.channel_icons.show_chat,
                "show_browser": self.channel_icons.show_browser,
            },
            "performance": {
                "youtube_concurrency": self.performance.youtube_concurrency,
                "kick_concurrency": self.performance.kick_concurrency,
            },
        }
