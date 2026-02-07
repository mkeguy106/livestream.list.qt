"""Tests for BaseChatConnection backoff logic."""

from livestream_list.chat.connections.base import (
    INITIAL_RECONNECT_DELAY,
    MAX_RECONNECT_DELAY,
    RECONNECT_BACKOFF_FACTOR,
    RECONNECT_JITTER,
    BaseChatConnection,
)


class _DummyConnection(BaseChatConnection):
    """Minimal concrete subclass for testing base class methods."""

    async def connect_to_channel(self, channel_id, **kwargs):
        pass

    async def disconnect(self):
        pass

    async def send_message(self, text, reply_to_msg_id=""):
        return True


def test_initial_delay():
    conn = _DummyConnection()
    assert conn._reconnect_delay == INITIAL_RECONNECT_DELAY


def test_get_next_backoff_returns_near_initial():
    conn = _DummyConnection()
    delay = conn._get_next_backoff()
    # Should be within jitter range of the initial delay
    max_jitter = INITIAL_RECONNECT_DELAY * RECONNECT_JITTER
    assert INITIAL_RECONNECT_DELAY - max_jitter <= delay <= INITIAL_RECONNECT_DELAY + max_jitter


def test_backoff_increases_exponentially():
    conn = _DummyConnection()
    conn._get_next_backoff()  # consume initial
    # After first call, internal delay should have doubled
    assert conn._reconnect_delay == INITIAL_RECONNECT_DELAY * RECONNECT_BACKOFF_FACTOR


def test_backoff_caps_at_max():
    conn = _DummyConnection()
    # Burn through enough calls to exceed the max
    for _ in range(20):
        conn._get_next_backoff()
    assert conn._reconnect_delay <= MAX_RECONNECT_DELAY


def test_backoff_jitter_range():
    conn = _DummyConnection()
    delays = [conn._get_next_backoff() for _ in range(50)]
    # Jitter should produce some variation (not all identical)
    assert len(set(delays)) > 1


def test_reset_backoff():
    conn = _DummyConnection()
    # Advance backoff several times
    for _ in range(5):
        conn._get_next_backoff()
    assert conn._reconnect_delay > INITIAL_RECONNECT_DELAY
    conn._reset_backoff()
    assert conn._reconnect_delay == INITIAL_RECONNECT_DELAY


def test_backoff_sequence():
    conn = _DummyConnection()
    expected = INITIAL_RECONNECT_DELAY
    for _ in range(5):
        conn._get_next_backoff()
        expected = min(expected * RECONNECT_BACKOFF_FACTOR, MAX_RECONNECT_DELAY)
        assert conn._reconnect_delay == expected


def test_max_delay_value_returned():
    conn = _DummyConnection()
    conn._reconnect_delay = MAX_RECONNECT_DELAY
    delay = conn._get_next_backoff()
    max_jitter = MAX_RECONNECT_DELAY * RECONNECT_JITTER
    assert MAX_RECONNECT_DELAY - max_jitter <= delay <= MAX_RECONNECT_DELAY + max_jitter
    # Should not exceed max
    assert conn._reconnect_delay == MAX_RECONNECT_DELAY
