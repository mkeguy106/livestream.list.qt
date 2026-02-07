"""Tests for YouTube chat parsing functions."""

from livestream_list.chat.connections.youtube import (
    _get_superchat_tier,
    parse_cookie_string,
    validate_cookies,
)

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
