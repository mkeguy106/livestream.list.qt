"""Tests for the Chaturbate per-channel fallback used when the bulk API is unavailable."""

import asyncio
import json
import logging
from typing import Any

from livestream_list.api import chaturbate
from livestream_list.api.chaturbate import ChaturbateApiClient
from livestream_list.core.models import Channel, StreamPlatform
from livestream_list.core.settings import ChaturbateSettings


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload
        self.request_info = None
        self.history = ()

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return json.dumps(self._payload)


class _FakeRequest:
    def __init__(self, server: "_FakeChaturbate", url: str) -> None:
        self.server = server
        self.url = url

    async def __aenter__(self) -> _FakeResponse:
        self.server.in_flight += 1
        self.server.max_in_flight = max(self.server.max_in_flight, self.server.in_flight)
        try:
            await asyncio.sleep(self.server.latency)
        finally:
            self.server.in_flight -= 1
        username = self.url.rstrip("/").rsplit("/", 1)[-1]
        if username in self.server.missing:
            return _FakeResponse(404, {})
        status = "public" if username in self.server.live else "offline"
        return _FakeResponse(200, {"room_status": status, "num_viewers": 7})

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeChaturbate:
    """Stands in for chaturbate.com's per-channel chatvideocontext endpoint."""

    closed = False

    def __init__(self, live: set[str], missing: set[str] = frozenset(), latency=0.01) -> None:
        self.live = live
        self.missing = missing
        self.latency = latency
        self.in_flight = 0
        self.max_in_flight = 0

    def get(self, url: str, **kwargs: Any) -> _FakeRequest:
        return _FakeRequest(self, url)


def _client(fake: _FakeChaturbate) -> ChaturbateApiClient:
    client = ChaturbateApiClient(ChaturbateSettings())
    client._session = fake  # type: ignore[assignment]
    return client


def _channels(n: int) -> list[Channel]:
    return [
        Channel(channel_id=f"room{i}", platform=StreamPlatform.CHATURBATE, display_name=f"room{i}")
        for i in range(n)
    ]


async def test_fallback_for_a_large_list_does_not_stall_the_refresh():
    # 107 channels took ~229s through the old sequential fallback with a 2s sleep
    # between requests, freezing every platform's refresh while it ran.
    channels = _channels(107)
    fake = _FakeChaturbate(live={"room3", "room50"})

    results = await asyncio.wait_for(_client(fake)._get_livestreams_individual(channels), 5)

    assert [r.channel.channel_id for r in results] == [c.channel_id for c in channels]
    assert {r.channel.channel_id for r in results if r.live} == {"room3", "room50"}


async def test_fallback_never_exceeds_its_concurrency_limit():
    fake = _FakeChaturbate(live=set())

    await _client(fake)._get_livestreams_individual(_channels(40))

    assert 1 < fake.max_in_flight <= chaturbate.INDIVIDUAL_CONCURRENCY


async def test_fallback_keeps_going_past_rooms_that_no_longer_exist():
    fake = _FakeChaturbate(live={"room2"}, missing={"room1"})

    results = await _client(fake)._get_livestreams_individual(_channels(3))

    assert [r.live for r in results] == [False, False, True]
    assert results[1].error_message == "HTTP 404"


async def test_bulk_timeout_is_named_in_the_log(caplog, monkeypatch):
    class _TimingOut(_FakeChaturbate):
        def get(self, url, **kwargs):  # type: ignore[no-untyped-def]
            if "room-list" in url:
                raise asyncio.TimeoutError()
            return super().get(url, **kwargs)

    client = _client(_TimingOut(live=set()))
    monkeypatch.setattr(client, "_get_cookie_string", lambda: "sessionid=x")

    with caplog.at_level(logging.WARNING, logger="livestream_list.api.chaturbate"):
        await client.get_livestreams(_channels(2))

    assert "TimeoutError" in caplog.text
