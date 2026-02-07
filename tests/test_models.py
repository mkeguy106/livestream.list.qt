"""Tests for core data models."""

from datetime import datetime, timedelta, timezone

from livestream_list.core.models import Channel, Livestream, StreamPlatform

# --- Channel.unique_key ---


def test_unique_key_twitch(twitch_channel):
    assert twitch_channel.unique_key == "twitch:testuser"


def test_unique_key_youtube(youtube_channel):
    assert youtube_channel.unique_key == "youtube:UC1234567890abcdef"


def test_unique_key_kick(kick_channel):
    assert kick_channel.unique_key == "kick:kickstreamer"


# --- Channel equality ---


def test_channel_equality():
    c1 = Channel(channel_id="foo", platform=StreamPlatform.TWITCH)
    c2 = Channel(channel_id="foo", platform=StreamPlatform.TWITCH, display_name="Different")
    assert c1 == c2


def test_channel_inequality_platform():
    c1 = Channel(channel_id="foo", platform=StreamPlatform.TWITCH)
    c2 = Channel(channel_id="foo", platform=StreamPlatform.KICK)
    assert c1 != c2


def test_channel_hash():
    c1 = Channel(channel_id="foo", platform=StreamPlatform.TWITCH)
    c2 = Channel(channel_id="foo", platform=StreamPlatform.TWITCH)
    assert hash(c1) == hash(c2)
    assert len({c1, c2}) == 1


# --- Livestream.display_name ---


def test_display_name_from_channel(twitch_channel):
    ls = Livestream(channel=twitch_channel)
    assert ls.display_name == "TestUser"


def test_display_name_fallback():
    ch = Channel(channel_id="fallback_id", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch)
    assert ls.display_name == "fallback_id"


# --- Livestream.uptime_str ---


def test_uptime_str_not_live(twitch_channel):
    ls = Livestream(channel=twitch_channel, live=False)
    assert ls.uptime_str == ""


def test_uptime_str_no_start_time(twitch_channel):
    ls = Livestream(channel=twitch_channel, live=True, start_time=None)
    assert ls.uptime_str == ""


def test_uptime_str_hours_and_minutes(twitch_channel):
    start = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)
    ls = Livestream(channel=twitch_channel, live=True, start_time=start)
    result = ls.uptime_str
    assert "2h" in result
    assert "30m" in result


def test_uptime_str_minutes_and_seconds(twitch_channel):
    start = datetime.now(timezone.utc) - timedelta(minutes=5, seconds=10)
    ls = Livestream(channel=twitch_channel, live=True, start_time=start)
    result = ls.uptime_str
    assert "5m" in result
    assert "h" not in result


# --- Livestream.viewers_str ---


def test_viewers_str_small():
    ch = Channel(channel_id="a", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch, viewers=123)
    assert ls.viewers_str == "123"


def test_viewers_str_thousands():
    ch = Channel(channel_id="a", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch, viewers=1500)
    assert ls.viewers_str == "1.5K"


def test_viewers_str_millions():
    ch = Channel(channel_id="a", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch, viewers=2_500_000)
    assert ls.viewers_str == "2.5M"


def test_viewers_str_zero():
    ch = Channel(channel_id="a", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch, viewers=0)
    assert ls.viewers_str == "0"


def test_viewers_str_exact_thousand():
    ch = Channel(channel_id="a", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch, viewers=1000)
    assert ls.viewers_str == "1.0K"


# --- Livestream.last_seen_str ---


def test_last_seen_str_live(twitch_channel):
    ls = Livestream(channel=twitch_channel, live=True)
    assert ls.last_seen_str == ""


def test_last_seen_str_no_time(twitch_channel):
    ls = Livestream(channel=twitch_channel, live=False)
    assert ls.last_seen_str == ""


def test_last_seen_str_just_now(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    assert ls.last_seen_str == "just now"


def test_last_seen_str_minutes(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert ls.last_seen_str == "15m ago"


def test_last_seen_str_hours(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    assert ls.last_seen_str == "5h ago"


def test_last_seen_str_days(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(days=3),
    )
    assert ls.last_seen_str == "3d ago"


def test_last_seen_str_months(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(days=60),
    )
    assert ls.last_seen_str == "2mo ago"


def test_last_seen_str_years(twitch_channel):
    ls = Livestream(
        channel=twitch_channel,
        live=False,
        last_live_time=datetime.now(timezone.utc) - timedelta(days=400),
    )
    assert ls.last_seen_str == "1y ago"


# --- Livestream.stream_url ---


def test_stream_url_twitch():
    ch = Channel(channel_id="streamer", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch)
    assert ls.stream_url == "https://twitch.tv/streamer"


def test_stream_url_youtube_uc():
    ch = Channel(channel_id="UC1234567890", platform=StreamPlatform.YOUTUBE)
    ls = Livestream(channel=ch)
    assert ls.stream_url == "https://youtube.com/channel/UC1234567890/live"


def test_stream_url_youtube_handle():
    ch = Channel(channel_id="@channelname", platform=StreamPlatform.YOUTUBE)
    ls = Livestream(channel=ch)
    assert ls.stream_url == "https://youtube.com/@channelname/live"


def test_stream_url_youtube_plain():
    ch = Channel(channel_id="channelname", platform=StreamPlatform.YOUTUBE)
    ls = Livestream(channel=ch)
    assert ls.stream_url == "https://youtube.com/@channelname/live"


def test_stream_url_kick():
    ch = Channel(channel_id="kickuser", platform=StreamPlatform.KICK)
    ls = Livestream(channel=ch)
    assert ls.stream_url == "https://kick.com/kickuser"


# --- Livestream.chat_url ---


def test_chat_url_twitch():
    ch = Channel(channel_id="streamer", platform=StreamPlatform.TWITCH)
    ls = Livestream(channel=ch)
    assert ls.chat_url == "https://twitch.tv/popout/streamer/chat"


def test_chat_url_youtube_with_video():
    ch = Channel(channel_id="yt_user", platform=StreamPlatform.YOUTUBE)
    ls = Livestream(channel=ch, video_id="dQw4w9WgXcQ")
    assert ls.chat_url == "https://youtube.com/live_chat?v=dQw4w9WgXcQ"


def test_chat_url_youtube_no_video():
    ch = Channel(channel_id="yt_user", platform=StreamPlatform.YOUTUBE)
    ls = Livestream(channel=ch)
    assert ls.chat_url == ""


def test_chat_url_kick():
    ch = Channel(channel_id="kickuser", platform=StreamPlatform.KICK)
    ls = Livestream(channel=ch)
    assert ls.chat_url == "https://kick.com/popout/kickuser/chat"


# --- Livestream.set_offline ---


def test_set_offline(twitch_livestream):
    twitch_livestream.set_offline()
    assert twitch_livestream.live is False
    assert twitch_livestream.viewers == 0
    assert twitch_livestream.start_time is None
