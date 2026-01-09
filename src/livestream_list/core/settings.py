"""Settings management for Livestream List."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from appdirs import user_config_dir, user_data_dir

from .models import StreamQuality


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
    player_args: str = ""
    default_quality: StreamQuality = StreamQuality.SOURCE
    low_latency: bool = False
    additional_args: str = ""


@dataclass
class NotificationSettings:
    """Notification-related settings."""

    enabled: bool = True
    sound_enabled: bool = True
    show_game: bool = True
    show_title: bool = True
    excluded_channels: list[str] = field(default_factory=list)
    backend: str = "auto"  # auto, dbus, notify-send


@dataclass
class TwitchSettings:
    """Twitch API settings."""

    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""


@dataclass
class YouTubeSettings:
    """YouTube API settings."""

    api_key: str = ""


@dataclass
class KickSettings:
    """Kick API settings.

    Note: Kick's API doesn't support importing follows,
    so no authentication tokens are needed.
    """

    pass


@dataclass
class WindowSettings:
    """Window state settings."""

    width: int = 1000
    height: int = 700
    x: Optional[int] = None
    y: Optional[int] = None
    maximized: bool = False


@dataclass
class ChatSettings:
    """Chat-related settings."""

    enabled: bool = True
    browser: str = "default"  # default, chrome, chromium, edge, firefox
    url_type: int = 0  # 0=Popout, 1=Embedded, 2=Default (legacy)
    auto_open: bool = False  # Auto-open chat when launching stream
    new_window: bool = True  # Open chat in new window instead of tab


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

    # UI preferences
    sort_mode: int = 1  # 0=Name, 1=Viewers, 2=Playing, 3=Last Seen, 4=Time Live
    hide_offline: bool = False
    favorites_only: bool = False
    ui_style: int = 0  # 0=Default, 1=Compact 1, 2=Compact 2
    platform_colors: bool = True  # Color platform icons and channel names

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

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        """Load settings from file."""
        if path is None:
            path = get_config_dir() / "settings.json"

        if not path.exists():
            return cls()

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls._from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            # Return default settings if file is corrupted
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        """Save settings to file."""
        if path is None:
            path = get_config_dir() / "settings.json"

        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, indent=2)

    @classmethod
    def _from_dict(cls, data: dict) -> "Settings":
        """Create Settings from a dictionary."""
        settings = cls()

        # General settings
        settings.refresh_interval = data.get("refresh_interval", settings.refresh_interval)
        settings.minimize_to_tray = data.get("minimize_to_tray", settings.minimize_to_tray)
        settings.start_minimized = data.get("start_minimized", settings.start_minimized)
        settings.check_for_updates = data.get("check_for_updates", settings.check_for_updates)
        settings.autostart = data.get("autostart", settings.autostart)
        settings.close_to_tray = data.get("close_to_tray", settings.close_to_tray)
        settings.close_to_tray_asked = data.get("close_to_tray_asked", settings.close_to_tray_asked)

        # UI preferences
        old_sort_mode = data.get("sort_mode", settings.sort_mode)
        # Valid sort modes: 0=Name, 1=Viewers, 2=Playing, 3=Last Seen, 4=Time Live
        if old_sort_mode in (0, 1, 2, 3, 4):
            settings.sort_mode = old_sort_mode
        else:
            settings.sort_mode = 1  # Default to Viewers
        settings.hide_offline = data.get("hide_offline", settings.hide_offline)
        settings.favorites_only = data.get("favorites_only", settings.favorites_only)
        settings.ui_style = data.get("ui_style", settings.ui_style)
        settings.platform_colors = data.get("platform_colors", settings.platform_colors)

        # Twitch
        if "twitch" in data:
            t = data["twitch"]
            settings.twitch = TwitchSettings(
                client_id=t.get("client_id", ""),
                client_secret=t.get("client_secret", ""),
                access_token=t.get("access_token", ""),
                refresh_token=t.get("refresh_token", ""),
            )

        # YouTube
        if "youtube" in data:
            settings.youtube = YouTubeSettings(api_key=data["youtube"].get("api_key", ""))

        # Kick - no settings needed currently
        settings.kick = KickSettings()

        # Streamlink
        if "streamlink" in data:
            s = data["streamlink"]
            settings.streamlink = StreamlinkSettings(
                enabled=s.get("enabled", True),
                path=s.get("path", "streamlink"),
                player=s.get("player", "mpv"),
                player_args=s.get("player_args", ""),
                default_quality=StreamQuality(s.get("default_quality", "best")),
                low_latency=s.get("low_latency", False),
                additional_args=s.get("additional_args", ""),
            )

        # Notifications
        if "notifications" in data:
            n = data["notifications"]
            settings.notifications = NotificationSettings(
                enabled=n.get("enabled", True),
                sound_enabled=n.get("sound_enabled", True),
                show_game=n.get("show_game", True),
                show_title=n.get("show_title", True),
                excluded_channels=n.get("excluded_channels", []),
                backend=n.get("backend", "auto"),
            )

        # Window
        if "window" in data:
            w = data["window"]
            settings.window = WindowSettings(
                width=w.get("width", 1000),
                height=w.get("height", 700),
                x=w.get("x"),
                y=w.get("y"),
                maximized=w.get("maximized", False),
            )

        # Chat
        if "chat" in data:
            c = data["chat"]
            settings.chat = ChatSettings(
                enabled=c.get("enabled", True),
                browser=c.get("browser", "default"),
                url_type=c.get("url_type", 0),
                auto_open=c.get("auto_open", False),
                new_window=c.get("new_window", True),
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

        return settings

    def _to_dict(self) -> dict:
        """Convert Settings to a dictionary."""
        return {
            "refresh_interval": self.refresh_interval,
            "minimize_to_tray": self.minimize_to_tray,
            "start_minimized": self.start_minimized,
            "check_for_updates": self.check_for_updates,
            "autostart": self.autostart,
            "close_to_tray": self.close_to_tray,
            "close_to_tray_asked": self.close_to_tray_asked,
            "sort_mode": self.sort_mode,
            "hide_offline": self.hide_offline,
            "favorites_only": self.favorites_only,
            "ui_style": self.ui_style,
            "platform_colors": self.platform_colors,
            "twitch": {
                "client_id": self.twitch.client_id,
                "client_secret": self.twitch.client_secret,
                "access_token": self.twitch.access_token,
                "refresh_token": self.twitch.refresh_token,
            },
            "youtube": {
                "api_key": self.youtube.api_key,
            },
            "kick": {},
            "streamlink": {
                "enabled": self.streamlink.enabled,
                "path": self.streamlink.path,
                "player": self.streamlink.player,
                "player_args": self.streamlink.player_args,
                "default_quality": self.streamlink.default_quality.value,
                "low_latency": self.streamlink.low_latency,
                "additional_args": self.streamlink.additional_args,
            },
            "notifications": {
                "enabled": self.notifications.enabled,
                "sound_enabled": self.notifications.sound_enabled,
                "show_game": self.notifications.show_game,
                "show_title": self.notifications.show_title,
                "excluded_channels": self.notifications.excluded_channels,
                "backend": self.notifications.backend,
            },
            "window": {
                "width": self.window.width,
                "height": self.window.height,
                "x": self.window.x,
                "y": self.window.y,
                "maximized": self.window.maximized,
            },
            "chat": {
                "enabled": self.chat.enabled,
                "browser": self.chat.browser,
                "url_type": self.chat.url_type,
                "auto_open": self.chat.auto_open,
                "new_window": self.chat.new_window,
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
        }
