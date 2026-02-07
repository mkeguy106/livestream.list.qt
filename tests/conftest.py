"""Shared test fixtures for livestream_list tests."""

from datetime import datetime, timezone

import pytest

from livestream_list.chat.models import ChatBadge, ChatMessage, ChatUser
from livestream_list.core.models import Channel, Livestream, StreamPlatform


@pytest.fixture
def twitch_channel():
    return Channel(
        channel_id="testuser",
        platform=StreamPlatform.TWITCH,
        display_name="TestUser",
    )


@pytest.fixture
def youtube_channel():
    return Channel(
        channel_id="UC1234567890abcdef",
        platform=StreamPlatform.YOUTUBE,
        display_name="YouTuber",
    )


@pytest.fixture
def kick_channel():
    return Channel(
        channel_id="kickstreamer",
        platform=StreamPlatform.KICK,
        display_name="KickStreamer",
    )


@pytest.fixture
def twitch_livestream(twitch_channel):
    return Livestream(
        channel=twitch_channel,
        live=True,
        title="Test Stream",
        game="Just Chatting",
        viewers=1234,
        start_time=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def chat_user():
    return ChatUser(
        id="12345",
        name="testuser",
        display_name="TestUser",
        platform=StreamPlatform.TWITCH,
        color="#FF0000",
        badges=[
            ChatBadge(id="subscriber/12", name="subscriber", image_url="https://example.com/sub"),
        ],
    )


@pytest.fixture
def chat_message(chat_user):
    return ChatMessage(
        id="msg-001",
        user=chat_user,
        text="Hello world!",
        timestamp=datetime(2025, 1, 1, 12, 30, 0, tzinfo=timezone.utc),
        platform=StreamPlatform.TWITCH,
    )
