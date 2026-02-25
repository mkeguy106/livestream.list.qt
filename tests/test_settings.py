"""Round-trip safety tests for Settings serialization."""

from livestream_list.core.models import LaunchMethod, SortMode, StreamQuality, UIStyle
from livestream_list.core.settings import (
    BuiltinChatSettings,
    ChannelIconSettings,
    ChannelInfoSettings,
    ChatColorSettings,
    ChatLoggingSettings,
    ChatSettings,
    ChatWindowSettings,
    KickSettings,
    NotificationSettings,
    PerformanceSettings,
    Settings,
    StreamlinkSettings,
    ThemeMode,
    TwitchSettings,
    WindowSettings,
    YouTubeSettings,
    _default_light_colors,
)


def _make_non_default_settings() -> Settings:
    """Create settings with every field set to a non-default value."""
    return Settings(
        refresh_interval=120,
        minimize_to_tray=False,
        start_minimized=True,
        check_for_updates=False,
        autostart=True,
        close_to_tray=True,
        close_to_tray_asked=True,
        emote_cache_mb=250,
        sort_mode=SortMode.NAME,
        hide_offline=True,
        favorites_only=True,
        ui_style=UIStyle.COMPACT_1,
        platform_colors=False,
        font_size=14,
        theme_mode=ThemeMode.DARK,
        custom_theme_slug="monokai",
        custom_theme_base="light",
        twitch=TwitchSettings(
            client_id="twitch_cid",
            client_secret="twitch_cs",
            access_token="twitch_at",
            refresh_token="twitch_rt",
            login_name="twitchuser",
            browser_auth_token="twitch_bat",
        ),
        youtube=YouTubeSettings(
            api_key="yt_api_key",
            cookies="SID=abc; HSID=def",
            use_ytdlp_fallback=False,
            cookie_browser="firefox",
            cookie_auto_refresh=False,
            login_name="ytuser",
        ),
        kick=KickSettings(
            client_id="kick_cid",
            client_secret="kick_cs",
            access_token="kick_at",
            refresh_token="kick_rt",
            login_name="kickuser",
        ),
        streamlink=StreamlinkSettings(
            enabled=False,
            path="/usr/bin/streamlink",
            player="vlc",
            player_args="--no-cache",
            default_quality=StreamQuality.P720,
            additional_args="--twitch-low-latency",
            twitch_launch_method=LaunchMethod.YT_DLP,
            youtube_launch_method=LaunchMethod.STREAMLINK,
            kick_launch_method=LaunchMethod.YT_DLP,
            twitch_turbo=False,
            show_console=True,
            auto_close_console=False,
            record_streams=True,
            record_directory="/tmp/recordings",
        ),
        notifications=NotificationSettings(
            enabled=False,
            sound_enabled=True,
            show_game=False,
            show_title=False,
            excluded_channels=["twitch:ignored"],
            backend="dbus",
            custom_sound_path="/tmp/ding.wav",
            urgency="critical",
            timeout_seconds=10,
            platform_filter=["twitch"],
            quiet_hours_enabled=True,
            quiet_hours_start="23:00",
            quiet_hours_end="07:00",
            raid_notifications_enabled=False,
            mention_notifications_enabled=False,
            mention_custom_sound_path="/tmp/mention.wav",
        ),
        window=WindowSettings(
            width=1200,
            height=800,
            x=100,
            y=200,
            maximized=True,
            always_on_top=True,
        ),
        chat=ChatSettings(
            enabled=False,
            mode="browser",
            browser="chrome",
            url_type=1,
            auto_open=True,
            new_window=False,
            recent_channels=["twitch:testchan"],
            builtin=BuiltinChatSettings(
                font_size=16,
                show_timestamps=True,
                timestamp_format="24h",
                show_badges=False,
                show_mod_badges=False,
                show_emotes=False,
                animate_emotes=False,
                line_spacing=8,
                max_messages=500,
                emote_providers=["7tv"],
                show_alternating_rows=False,
                show_metrics=False,
                blocked_users=["twitch:blocked1"],
                blocked_user_names={"twitch:blocked1": "BlockedUser"},
                highlight_keywords=["important"],
                user_nicknames={"twitch:user1": "Buddy"},
                user_nickname_display_names={"twitch:user1": "User1"},
                user_notes={"twitch:user1": "some note"},
                user_note_display_names={"twitch:user1": "User1"},
                use_platform_name_colors=False,
                show_stream_title=False,
                show_socials_banner=False,
                spellcheck_enabled=False,
                autocorrect_enabled=False,
                moderated_message_display="hidden",
                user_card_hover=False,
                always_on_top=True,
                window=ChatWindowSettings(width=500, height=700, x=50, y=75),
                dark_colors=ChatColorSettings(
                    alt_row_color_even="#11111111",
                    alt_row_color_odd="#22222222",
                    tab_active_color="#333333",
                    tab_inactive_color="#444444",
                    mention_highlight_color="#55555555",
                    banner_bg_color="#666666",
                    banner_text_color="#777777",
                ),
                light_colors=ChatColorSettings(
                    alt_row_color_even="#aaaaaaaa",
                    alt_row_color_odd="#bbbbbbbb",
                    tab_active_color="#cccccc",
                    tab_inactive_color="#dddddd",
                    mention_highlight_color="#eeeeeeee",
                    banner_bg_color="#ffffff",
                    banner_text_color="#000000",
                ),
            ),
            logging=ChatLoggingSettings(
                enabled=True,
                max_disk_mb=200,
                log_format="text",
                load_history_on_open=False,
                history_lines=50,
            ),
        ),
        channel_info=ChannelInfoSettings(
            show_live_duration=False,
            show_viewers=False,
        ),
        channel_icons=ChannelIconSettings(
            show_platform=False,
            show_play=False,
            show_favorite=False,
            show_chat=False,
            show_browser=False,
        ),
        performance=PerformanceSettings(
            youtube_concurrency=5,
            kick_concurrency=20,
        ),
    )


def test_round_trip_with_secrets():
    """Serialize → deserialize → re-serialize produces identical dicts."""
    settings = _make_non_default_settings()
    d1 = settings._to_dict(exclude_secrets=False)
    restored = Settings._from_dict(d1)
    d2 = restored._to_dict(exclude_secrets=False)
    assert d1 == d2


def test_round_trip_without_secrets():
    """Serialize (excluding secrets) → deserialize → re-serialize produces identical dicts."""
    settings = _make_non_default_settings()
    d1 = settings._to_dict(exclude_secrets=True)
    restored = Settings._from_dict(d1)
    d2 = restored._to_dict(exclude_secrets=True)
    assert d1 == d2


def test_default_settings_round_trip():
    """Default Settings() round-trips cleanly."""
    settings = Settings()
    d1 = settings._to_dict(exclude_secrets=False)
    restored = Settings._from_dict(d1)
    d2 = restored._to_dict(exclude_secrets=False)
    assert d1 == d2


def test_exclude_secrets_strips_tokens():
    """All 6 secret fields are absent when exclude_secrets=True."""
    settings = _make_non_default_settings()
    d = settings._to_dict(exclude_secrets=True)

    # Twitch secrets
    assert "access_token" not in d["twitch"]
    assert "refresh_token" not in d["twitch"]
    assert "browser_auth_token" not in d["twitch"]
    # YouTube secrets
    assert "cookies" not in d["youtube"]
    # Kick secrets
    assert "access_token" not in d["kick"]
    assert "refresh_token" not in d["kick"]

    # Non-secret fields should still be present
    assert d["twitch"]["client_id"] == "twitch_cid"
    assert d["youtube"]["api_key"] == "yt_api_key"
    assert d["kick"]["client_id"] == "kick_cid"


def test_legacy_color_migration():
    """Old config with top-level color fields (no dark_colors key) migrates correctly."""
    legacy_data = {
        "chat": {
            "enabled": True,
            "mode": "builtin",
            "browser": "default",
            "url_type": 0,
            "auto_open": False,
            "new_window": True,
            "recent_channels": [],
            "builtin": {
                # Legacy: color fields at top level, no dark_colors/light_colors keys
                "alt_row_color_even": "#aabbccdd",
                "alt_row_color_odd": "#11223344",
                "tab_active_color": "#abcdef",
                "tab_inactive_color": "#fedcba",
                "mention_highlight_color": "#99887766",
                "banner_bg_color": "#112233",
                "banner_text_color": "#445566",
                "font_size": 13,
            },
        }
    }
    settings = Settings._from_dict(legacy_data)

    # Legacy fields should have been migrated into dark_colors
    assert settings.chat.builtin.dark_colors.alt_row_color_even == "#aabbccdd"
    assert settings.chat.builtin.dark_colors.alt_row_color_odd == "#11223344"
    assert settings.chat.builtin.dark_colors.tab_active_color == "#abcdef"
    assert settings.chat.builtin.dark_colors.tab_inactive_color == "#fedcba"
    assert settings.chat.builtin.dark_colors.mention_highlight_color == "#99887766"
    assert settings.chat.builtin.dark_colors.banner_bg_color == "#112233"
    assert settings.chat.builtin.dark_colors.banner_text_color == "#445566"

    # Light colors should be the defaults (no legacy light fields)
    light_defaults = _default_light_colors()
    assert settings.chat.builtin.light_colors.alt_row_color_even == light_defaults.alt_row_color_even
    assert settings.chat.builtin.light_colors.tab_active_color == light_defaults.tab_active_color
