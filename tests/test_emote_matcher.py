from dataclasses import dataclass

from livestream_list.chat.emotes.matcher import find_third_party_emotes


@dataclass
class ChatEmote:
    id: str
    name: str
    url_template: str
    provider: str


def _emote(name: str) -> ChatEmote:
    return ChatEmote(id=name.lower(), name=name, url_template="http://example", provider="7tv")


def _positions(text: str, emote_names: list[str], claimed=None):
    emote_map = {name: _emote(name) for name in emote_names}
    matches = find_third_party_emotes(text, emote_map, claimed)
    return [(start, end, emote.name) for start, end, emote in matches]


def test_match_simple_word():
    text = "hello Kappa world"
    assert _positions(text, ["Kappa"]) == [(6, 11, "Kappa")]


def test_match_punct_wrapped():
    text = "(Kappa)!"
    assert _positions(text, ["Kappa"]) == [(1, 6, "Kappa")]


def test_match_brackets():
    text = "[Kappa]"
    assert _positions(text, ["Kappa"]) == [(1, 6, "Kappa")]


def test_match_combined_punct():
    text = "D:"
    assert _positions(text, ["D:"]) == [(0, 2, "D:")]


def test_skip_url():
    text = "https://example.com/Kappa"
    assert _positions(text, ["Kappa"]) == []


def test_no_overlap():
    text = "Kappa"
    assert _positions(text, ["Kappa"], claimed=[(0, 5)]) == []


def test_multiple_in_single_token():
    text = "Kappa,Kappa"
    assert _positions(text, ["Kappa"]) == [(0, 5, "Kappa"), (6, 11, "Kappa")]
