"""Tests for Kick chat's Pusher key discovery and handshake handling."""

import json
from typing import Any

import aiohttp
import pytest

from livestream_list.chat.connections import kick as kick_mod
from livestream_list.chat.connections.kick import KickChatConnection

# The key Kick retired in October 2026. Pusher answers it with 4001.
RETIRED_KEY = "32cbd69e4b950bf97679"
CURRENT_KEY = "34bf7a0ff419a2a775b9"

CONNECTION_ESTABLISHED = {
    "event": "pusher:connection_established",
    "data": json.dumps({"socket_id": "1.2", "activity_timeout": 120}),
}
APP_KEY_NOT_IN_CLUSTER = {
    "event": "pusher:error",
    "data": {
        "code": 4001,
        "message": f"App key {RETIRED_KEY} not in this cluster. "
        "Did you forget to specify the cluster?",
    },
}


def _descriptor(app_key: str, cluster: str = "us2") -> dict[str, Any]:
    return {
        "data": {
            "connections": [
                {"credentials": {"app_key": app_key, "cluster": cluster}, "provider": "pusher"}
            ],
            "mode": "websocket",
        },
        "message": "success",
    }


class _WSMessage:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.type = aiohttp.WSMsgType.TEXT
        self.data = json.dumps(payload)
        self.extra = None


class _FakeWebSocket:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = [_WSMessage(f) for f in frames]
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def receive(self) -> _WSMessage:
        return self._frames.pop(0)

    async def send_str(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeWebSocket":
        return self

    async def __anext__(self) -> _WSMessage:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


class _FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Any:
        return self._payload

    async def text(self) -> str:
        return json.dumps(self._payload)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeKick:
    """Stands in for Kick's realtime descriptor endpoint and Pusher.

    Pusher only accepts ``accepted_key``; any other key gets the 4001 error
    frame Pusher really sends, which is what broke chat after the rotation.
    """

    def __init__(self, descriptor: tuple[int, Any], accepted_key: str = CURRENT_KEY) -> None:
        self.descriptor = descriptor
        self.accepted_key = accepted_key
        self.posts: list[tuple[str, Any]] = []
        self.ws_urls: list[str] = []
        self.ws: _FakeWebSocket | None = None
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append((url, kwargs.get("json")))
        return _FakeResponse(*self.descriptor)

    async def ws_connect(self, url: str) -> _FakeWebSocket:
        self.ws_urls.append(url)
        if f"/app/{self.accepted_key}?" in url:
            self.ws = _FakeWebSocket([CONNECTION_ESTABLISHED])
        else:
            self.ws = _FakeWebSocket([APP_KEY_NOT_IN_CLUSTER])
        return self.ws

    async def close(self) -> None:
        self.closed = True


async def _connect(
    monkeypatch: pytest.MonkeyPatch, kick: _FakeKick
) -> tuple[KickChatConnection, list[str], list[str]]:
    monkeypatch.setattr(kick_mod.aiohttp, "ClientSession", lambda: kick)
    conn = KickChatConnection()
    events: list[str] = []
    errors: list[str] = []
    conn.connected.connect(lambda: events.append("connected"))
    conn.error.connect(errors.append)
    await conn.connect_to_channel("destiny", chatroom_id=1764849)
    return conn, events, errors


async def test_connects_with_key_from_realtime_descriptor(monkeypatch):
    kick = _FakeKick(descriptor=(200, _descriptor(CURRENT_KEY)))

    _, events, errors = await _connect(monkeypatch, kick)

    assert kick.ws_urls == [
        f"wss://ws-us2.pusher.com/app/{CURRENT_KEY}?protocol=7&client=js&version=8.3.0&flash=false"
    ]
    assert events == ["connected"]
    assert errors == []
    assert kick.ws is not None
    assert kick.ws.sent[0] == {
        "event": "pusher:subscribe",
        "data": {"auth": "", "channel": "chatrooms.1764849.v2"},
    }


async def test_descriptor_request_only_accepts_pusher(monkeypatch):
    kick = _FakeKick(descriptor=(200, _descriptor(CURRENT_KEY)))

    await _connect(monkeypatch, kick)

    url, body = kick.posts[0]
    assert url == "https://web.kick.com/api/v1/realtime/connection"
    # Offering centrifugo too makes Kick hand back centrifugo, which we can't speak.
    assert body["capabilities"] == {"accepted_providers": [{"provider": "pusher"}]}
    assert body["client"]["type"] == "web"
    assert body["client"]["id"]


async def test_follows_a_future_key_rotation(monkeypatch):
    kick = _FakeKick(
        descriptor=(200, _descriptor("aaaabbbbccccddddeeee", "eu")),
        accepted_key="aaaabbbbccccddddeeee",
    )

    _, events, _ = await _connect(monkeypatch, kick)

    assert kick.ws_urls[0].startswith("wss://ws-eu.pusher.com/app/aaaabbbbccccddddeeee?")
    assert events == ["connected"]


@pytest.mark.parametrize(
    "descriptor",
    [
        (503, {"message": "unavailable"}),
        (200, {"data": {"connections": [], "mode": "websocket"}}),
        (
            200,
            {
                "data": {
                    "connections": [
                        {"credentials": {"url": "wss://realtime.example"}, "provider": "centrifugo"}
                    ]
                }
            },
        ),
        # A cluster lands in the Pusher hostname; refuse anything that isn't a bare label.
        (200, _descriptor(CURRENT_KEY, cluster="evil.example/x")),
        (200, "not json object"),
    ],
)
async def test_falls_back_to_builtin_key_when_descriptor_unusable(monkeypatch, descriptor):
    kick = _FakeKick(descriptor=descriptor)

    _, events, errors = await _connect(monkeypatch, kick)

    assert kick.ws_urls == [
        f"wss://ws-us2.pusher.com/app/{CURRENT_KEY}?protocol=7&client=js&version=8.3.0&flash=false"
    ]
    assert events == ["connected"]
    assert errors == []


async def test_pusher_error_is_reported_not_treated_as_connected(monkeypatch):
    # Descriptor hands back the retired key, so Pusher rejects the handshake.
    kick = _FakeKick(descriptor=(200, _descriptor(RETIRED_KEY)))

    conn, events, errors = await _connect(monkeypatch, kick)

    assert events == []
    assert len(errors) == 1
    assert "4001" in errors[0]
    assert "not in this cluster" in errors[0]
    assert kick.ws is not None and kick.ws.sent == []  # never subscribed
    assert not conn.is_connected


async def test_pusher_error_does_not_reset_reconnect_backoff(monkeypatch):
    kick = _FakeKick(descriptor=(200, _descriptor(RETIRED_KEY)))
    monkeypatch.setattr(kick_mod.aiohttp, "ClientSession", lambda: kick)
    conn = KickChatConnection()
    conn._reconnect_delay = 8.0

    await conn.connect_to_channel("destiny", chatroom_id=1764849)

    # Resetting here is what made every failed retry wait ~1s instead of backing off.
    assert conn._reconnect_delay == 8.0
