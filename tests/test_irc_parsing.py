"""Tests for Twitch IRC parsing functions."""

from livestream_list.chat.connections.twitch import (
    parse_badges,
    parse_emote_positions,
    parse_irc_message,
    parse_irc_tags,
)

# --- parse_irc_tags ---


def test_parse_irc_tags_empty():
    assert parse_irc_tags("") == {}


def test_parse_irc_tags_single():
    assert parse_irc_tags("@color=#FF0000") == {"color": "#FF0000"}


def test_parse_irc_tags_multiple():
    result = parse_irc_tags("@color=#FF0000;display-name=TestUser;subscriber=1")
    assert result == {"color": "#FF0000", "display-name": "TestUser", "subscriber": "1"}


def test_parse_irc_tags_no_at_prefix():
    result = parse_irc_tags("color=#FF0000;subscriber=1")
    assert result == {"color": "#FF0000", "subscriber": "1"}


def test_parse_irc_tags_escaped_semicolon():
    result = parse_irc_tags("@msg=hello\\:world")
    assert result["msg"] == "hello;world"


def test_parse_irc_tags_escaped_space():
    result = parse_irc_tags("@msg=hello\\sworld")
    assert result["msg"] == "hello world"


def test_parse_irc_tags_escaped_backslash():
    result = parse_irc_tags("@msg=hello\\\\world")
    assert result["msg"] == "hello\\world"


def test_parse_irc_tags_empty_value():
    result = parse_irc_tags("@emotes=")
    assert result == {"emotes": ""}


def test_parse_irc_tags_no_equals():
    result = parse_irc_tags("@flagonly")
    assert result == {"flagonly": ""}


def test_parse_irc_tags_value_with_equals():
    result = parse_irc_tags("@key=a=b=c")
    assert result["key"] == "a=b=c"


# --- parse_irc_message ---


def test_parse_irc_message_privmsg():
    raw = "@color=#FF0000 :user!user@user.tmi.twitch.tv PRIVMSG #channel :Hello world"
    result = parse_irc_message(raw)
    assert result["command"] == "PRIVMSG"
    assert result["trailing"] == "Hello world"
    assert result["params"] == ["#channel"]
    assert result["tags"]["color"] == "#FF0000"
    assert "user!user@user.tmi.twitch.tv" in result["prefix"]


def test_parse_irc_message_ping():
    raw = "PING :tmi.twitch.tv"
    result = parse_irc_message(raw)
    assert result["command"] == "PING"
    assert result["trailing"] == "tmi.twitch.tv"


def test_parse_irc_message_no_trailing():
    raw = ":tmi.twitch.tv 001 justinfan12345"
    result = parse_irc_message(raw)
    assert result["command"] == "001"
    assert result["params"] == ["justinfan12345"]
    assert result["trailing"] == ""


def test_parse_irc_message_tags_only():
    raw = "@badge-info= PING"
    result = parse_irc_message(raw)
    assert result["tags"] == {"badge-info": ""}


def test_parse_irc_message_empty_string():
    raw = "@tagsonly"
    result = parse_irc_message(raw)
    # Should return default result since no space after tags
    assert result["command"] == ""


def test_parse_irc_message_trailing_with_colon():
    raw = ":server PRIVMSG #ch :hello :world :test"
    result = parse_irc_message(raw)
    assert result["trailing"] == "hello :world :test"


# --- parse_badges ---


def test_parse_badges_empty():
    assert parse_badges("") == []


def test_parse_badges_single():
    badges = parse_badges("subscriber/12")
    assert len(badges) == 1
    assert badges[0].id == "subscriber/12"
    assert badges[0].name == "subscriber"


def test_parse_badges_multiple():
    badges = parse_badges("moderator/1,subscriber/6,premium/1")
    assert len(badges) == 3
    assert badges[0].name == "moderator"
    assert badges[1].name == "subscriber"
    assert badges[2].name == "premium"


def test_parse_badges_no_slash():
    badges = parse_badges("invalidbadge")
    assert len(badges) == 0


def test_parse_badges_mixed_valid_invalid():
    badges = parse_badges("moderator/1,invalid,subscriber/6")
    assert len(badges) == 2
    assert badges[0].name == "moderator"
    assert badges[1].name == "subscriber"


# --- parse_emote_positions ---


def test_parse_emote_positions_empty():
    assert parse_emote_positions("") == []


def test_parse_emote_positions_single():
    positions = parse_emote_positions("25:0-4")
    assert len(positions) == 1
    assert positions[0][0] == 0  # start
    assert positions[0][1] == 5  # end (inclusive → exclusive)
    assert positions[0][2].id == "25"


def test_parse_emote_positions_multiple_ranges():
    positions = parse_emote_positions("25:0-4,12-16")
    assert len(positions) == 2
    assert positions[0][0] == 0
    assert positions[1][0] == 12


def test_parse_emote_positions_multiple_emotes():
    positions = parse_emote_positions("25:0-4/1234:6-9")
    assert len(positions) == 2
    assert positions[0][2].id == "25"
    assert positions[1][2].id == "1234"


def test_parse_emote_positions_sorted():
    positions = parse_emote_positions("25:10-14/1234:0-3")
    assert positions[0][0] == 0  # Should be sorted by start position
    assert positions[1][0] == 10


def test_parse_emote_positions_no_colon():
    positions = parse_emote_positions("invalidformat")
    assert positions == []


def test_parse_emote_positions_invalid_range():
    positions = parse_emote_positions("25:abc-def")
    assert positions == []
