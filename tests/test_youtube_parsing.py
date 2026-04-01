"""Tests for YouTube chat parsing functions."""

from unittest.mock import MagicMock, patch

import pytest

from livestream_list.chat.connections.youtube import (
    _get_superchat_tier,
    parse_cookie_string,
    validate_cookies,
)
from livestream_list.core.models import Channel, Livestream, StreamPlatform

# --- parse_cookie_string ---


def test_parse_cookie_string_empty():
    assert parse_cookie_string("") == {}


def test_parse_cookie_string_whitespace_only():
    assert parse_cookie_string("   ") == {}


def test_parse_cookie_string_single():
    result = parse_cookie_string("SID=abc123")
    assert result == {"SID": "abc123"}


def test_parse_cookie_string_multiple():
    result = parse_cookie_string("SID=abc; HSID=def; SSID=ghi")
    assert result == {"SID": "abc", "HSID": "def", "SSID": "ghi"}


def test_parse_cookie_string_whitespace_around_values():
    result = parse_cookie_string("  SID = abc ;  HSID = def  ")
    assert result == {"SID": "abc", "HSID": "def"}


def test_parse_cookie_string_value_with_equals():
    result = parse_cookie_string("SID=abc=def=ghi")
    assert result == {"SID": "abc=def=ghi"}


def test_parse_cookie_string_empty_value():
    result = parse_cookie_string("SID=;HSID=val")
    assert result == {"SID": "", "HSID": "val"}


def test_parse_cookie_string_no_equals():
    result = parse_cookie_string("justname")
    assert result == {}


def test_parse_cookie_string_mixed():
    result = parse_cookie_string("SID=abc;badentry;HSID=def")
    assert result == {"SID": "abc", "HSID": "def"}


# --- validate_cookies ---


def test_validate_cookies_all_present():
    cookie = "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e"
    assert validate_cookies(cookie) is True


def test_validate_cookies_extra_keys():
    cookie = "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e; OTHER=f"
    assert validate_cookies(cookie) is True


def test_validate_cookies_missing_key():
    cookie = "SID=a; HSID=b; SSID=c; APISID=d"
    assert validate_cookies(cookie) is False


def test_validate_cookies_empty():
    assert validate_cookies("") is False


def test_validate_cookies_whitespace():
    assert validate_cookies("   ") is False


# --- _get_superchat_tier ---


def test_superchat_tier_red():
    assert _get_superchat_tier(100) == "RED"
    assert _get_superchat_tier(500) == "RED"


def test_superchat_tier_magenta():
    assert _get_superchat_tier(50) == "MAGENTA"
    assert _get_superchat_tier(99.99) == "MAGENTA"


def test_superchat_tier_orange():
    assert _get_superchat_tier(20) == "ORANGE"
    assert _get_superchat_tier(49.99) == "ORANGE"


def test_superchat_tier_yellow():
    assert _get_superchat_tier(10) == "YELLOW"
    assert _get_superchat_tier(19.99) == "YELLOW"


def test_superchat_tier_green():
    assert _get_superchat_tier(5) == "GREEN"
    assert _get_superchat_tier(9.99) == "GREEN"


def test_superchat_tier_cyan():
    assert _get_superchat_tier(2) == "CYAN"
    assert _get_superchat_tier(4.99) == "CYAN"


def test_superchat_tier_blue():
    assert _get_superchat_tier(0) == "BLUE"
    assert _get_superchat_tier(1.99) == "BLUE"


def test_superchat_tier_negative():
    assert _get_superchat_tier(-1) == "BLUE"


# --- _get_all_concurrent_streams ---


@pytest.fixture
def yt_channel():
    return Channel(channel_id="UC_AP", platform=StreamPlatform.YOUTUBE, display_name="AP")


class TestGetAllConcurrentStreams:
    """Test _get_all_concurrent_streams returns multiple Livestreams."""

    async def test_single_stream_returns_one(self, yt_channel: Channel) -> None:
        """Channel with one live stream returns a single-element list."""
        from livestream_list.api.youtube import YouTubeApiClient

        settings = MagicMock()
        settings.use_ytdlp_fallback = False
        client = YouTubeApiClient(settings)

        primary = Livestream(
            channel=yt_channel, live=True, video_id="vid1", title="Stream 1"
        )

        with patch.object(client, "_get_livestream_scrape", return_value=primary):
            with patch.object(
                client, "_fetch_concurrent_live_video_ids", return_value=["vid1"]
            ):
                result = await client._get_all_concurrent_streams(yt_channel)

        assert len(result) == 1
        assert result[0].video_id == "vid1"
        await client.close()

    async def test_two_streams_returns_two(self, yt_channel: Channel) -> None:
        """Channel with two live streams returns both."""
        from livestream_list.api.youtube import YouTubeApiClient

        settings = MagicMock()
        settings.use_ytdlp_fallback = False
        client = YouTubeApiClient(settings)

        primary = Livestream(
            channel=yt_channel, live=True, video_id="vid1", title="Stream 1"
        )
        secondary_data = {
            "videoDetails": {
                "videoId": "vid2",
                "isLive": True,
                "isLiveContent": True,
                "title": "Stream 2",
                "author": "AP",
                "viewCount": "500",
            }
        }
        secondary = Livestream(
            channel=yt_channel, live=True, video_id="vid2", title="Stream 2"
        )

        with patch.object(client, "_get_livestream_scrape", return_value=primary):
            with patch.object(
                client,
                "_fetch_concurrent_live_video_ids",
                return_value=["vid1", "vid2"],
            ):
                with patch.object(
                    client,
                    "_fetch_video_player_response",
                    return_value=secondary_data,
                ):
                    with patch.object(
                        client,
                        "_extract_livestream_from_data",
                        return_value=secondary,
                    ):
                        result = await client._get_all_concurrent_streams(yt_channel)

        assert len(result) == 2
        video_ids = {ls.video_id for ls in result}
        assert video_ids == {"vid1", "vid2"}
        await client.close()

    async def test_offline_returns_single_offline(self, yt_channel: Channel) -> None:
        """Offline channel returns single offline Livestream."""
        from livestream_list.api.youtube import YouTubeApiClient

        settings = MagicMock()
        settings.use_ytdlp_fallback = False
        client = YouTubeApiClient(settings)

        offline = Livestream(channel=yt_channel, live=False)

        with patch.object(client, "_get_livestream_scrape", return_value=offline):
            result = await client._get_all_concurrent_streams(yt_channel)

        assert len(result) == 1
        assert result[0].live is False
        await client.close()

    async def test_scrape_failed_no_fallback(self, yt_channel: Channel) -> None:
        """When scrape returns None and no yt-dlp fallback, returns offline."""
        from livestream_list.api.youtube import YouTubeApiClient

        settings = MagicMock()
        settings.use_ytdlp_fallback = False
        client = YouTubeApiClient(settings)
        client._ytdlp_path = None

        with patch.object(client, "_get_livestream_scrape", return_value=None):
            result = await client._get_all_concurrent_streams(yt_channel)

        assert len(result) == 1
        assert result[0].live is False
        await client.close()

    async def test_scrape_failed_with_fallback(self, yt_channel: Channel) -> None:
        """When scrape returns None and yt-dlp fallback is enabled, uses yt-dlp."""
        from livestream_list.api.youtube import YouTubeApiClient

        settings = MagicMock()
        settings.use_ytdlp_fallback = True
        client = YouTubeApiClient(settings)
        client._ytdlp_path = "/usr/bin/yt-dlp"

        fallback_ls = Livestream(
            channel=yt_channel, live=True, video_id="fb1", title="Fallback"
        )

        with patch.object(client, "_get_livestream_scrape", return_value=None):
            with patch.object(
                client, "_get_livestream_ytdlp", return_value=fallback_ls
            ):
                result = await client._get_all_concurrent_streams(yt_channel)

        assert len(result) == 1
        assert result[0].video_id == "fb1"
        await client.close()
