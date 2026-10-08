"""Tests for Twitch GraphQL batched stream lookups."""

import json
import logging
import re
from typing import Any

from livestream_list.api.twitch import TwitchApiClient
from livestream_list.core.models import Channel, StreamPlatform
from livestream_list.core.settings import TwitchSettings

# Twitch began enforcing this on 2026-10-07; queries over it get HTTP 400.
TWITCH_ROOT_ALIAS_LIMIT = 15

ALIAS_RE = re.compile(r'(\w+):\s*user\(login:\s*"([^"]*)"\)')


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

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeTwitchGql:
    """Stands in for gql.twitch.tv, enforcing its root-field alias limit."""

    closed = False

    def __init__(self, users: dict[str, dict[str, Any]]) -> None:
        self.users = users
        self.requests: list[dict[str, Any]] = []

    def _user(self, login: str) -> dict[str, Any] | None:
        return self.users.get(login.lower())

    def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self.requests.append(json)
        query = json["query"]

        aliases = ALIAS_RE.findall(query)
        if len(aliases) > TWITCH_ROOT_ALIAS_LIMIT:
            return _FakeResponse(
                400,
                {
                    "errors": [
                        {
                            "message": f"The number of root field aliases {len(aliases)} "
                            f"exceeds the root field aliases limit allowed "
                            f"({TWITCH_ROOT_ALIAS_LIMIT})"
                        }
                    ]
                },
            )
        if aliases:
            return _FakeResponse(
                200, {"data": {alias: self._user(login) for alias, login in aliases}}
            )
        if "users(logins:" in query:
            logins = json["variables"]["logins"]
            return _FakeResponse(200, {"data": {"users": [self._user(lg) for lg in logins]}})
        return _FakeResponse(400, {"errors": [{"message": "unrecognised query"}]})


def _user(login: str, live: bool) -> dict[str, Any]:
    stream = (
        {
            "id": f"s-{login}",
            "title": f"{login} title",
            "viewersCount": 42,
            "createdAt": "2026-10-07T18:00:00Z",
            "game": {"name": "Just Chatting", "slug": "just-chatting"},
        }
        if live
        else None
    )
    return {
        "id": f"id-{login}",
        "login": login,
        "displayName": login,
        "stream": stream,
        "lastBroadcast": {"startedAt": "2026-10-06T12:00:00Z"},
    }


def _client(fake: _FakeTwitchGql) -> TwitchApiClient:
    client = TwitchApiClient(TwitchSettings())
    client._session = fake  # type: ignore[assignment]
    return client


def _channel(login: str) -> Channel:
    return Channel(channel_id=login, platform=StreamPlatform.TWITCH, display_name=login)


async def test_large_follow_list_is_not_rejected_by_alias_limit():
    logins = [f"streamer{i}" for i in range(276)]
    live = set(logins[::10])
    fake = _FakeTwitchGql({lg: _user(lg, lg in live) for lg in logins})

    results = await _client(fake).get_livestreams([_channel(lg) for lg in logins])

    by_login = {r.channel.channel_id: r for r in results}
    assert len(by_login) == 276
    assert {lg for lg, r in by_login.items() if r.live} == live
    assert by_login["streamer0"].title == "streamer0 title"
    assert by_login["streamer0"].game_slug == "just-chatting"
    assert by_login["streamer1"].last_live_time is not None


async def test_unknown_login_does_not_shift_neighbouring_results():
    fake = _FakeTwitchGql({"alpha": _user("alpha", False), "gamma": _user("gamma", True)})

    results = await _client(fake).get_livestreams(
        [_channel("alpha"), _channel("banned_or_renamed"), _channel("gamma")]
    )

    by_login = {r.channel.channel_id: r for r in results}
    assert by_login["alpha"].live is False
    assert by_login["banned_or_renamed"].live is False
    assert by_login["gamma"].live is True


async def test_mixed_case_channel_id_matches_lowercase_login():
    fake = _FakeTwitchGql({"mixedcase": _user("mixedcase", True)})

    results = await _client(fake).get_livestreams([_channel("MixedCase")])

    assert results[0].live is True


async def test_rejected_batch_logs_twitchs_error_message(caplog):
    class _Rejecting(_FakeTwitchGql):
        def post(self, url, headers, json):  # type: ignore[no-untyped-def]
            self.requests.append(json)
            return _FakeResponse(400, {"errors": [{"message": "some new Twitch limit"}]})

    with caplog.at_level(logging.WARNING, logger="livestream_list.api.twitch"):
        await _client(_Rejecting({})).get_livestreams([_channel("alpha")])

    assert "some new Twitch limit" in caplog.text
