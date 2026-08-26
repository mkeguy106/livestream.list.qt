"""Tests for readable text on themed background colors.

A theme's accent is arbitrary — the built-in Obsidian Mono sets it to a
near-white (#e8e8ec) on purpose, and users can author any accent they like via
the theme editor. Anything painting text onto an accent background therefore
has to pick its text color, not hardcode white.
"""

from livestream_list.gui.theme import contrasting_text_color


def test_white_text_on_dark_accent():
    # Twitch purple, the default dark-theme accent
    assert contrasting_text_color("#6441a5") == "#ffffff"


def test_black_text_on_near_white_accent():
    # Obsidian Mono's accent — this is the bug the user hit: the send button
    # and the followers-only banner rendered white-on-near-white.
    assert contrasting_text_color("#e8e8ec") == "#000000"


def test_black_text_on_pure_white():
    assert contrasting_text_color("#ffffff") == "#000000"


def test_white_text_on_pure_black():
    assert contrasting_text_color("#000000") == "#ffffff"


def test_accepts_color_without_leading_hash():
    assert contrasting_text_color("e8e8ec") == "#000000"


def test_accepts_shorthand_hex():
    assert contrasting_text_color("#fff") == "#000000"


def test_uses_perceived_brightness_not_raw_average():
    # Pure green is far brighter to the eye than pure blue, though both have
    # the same raw channel average. A naive mean would answer identically for
    # the two; weighting by perceived luminance must not.
    assert contrasting_text_color("#00ff00") == "#000000"
    assert contrasting_text_color("#0000ff") == "#ffffff"


def test_unparseable_color_falls_back_to_white():
    # Never raise from inside a stylesheet f-string; a wrong-but-legible
    # default beats a crash while building the UI.
    assert contrasting_text_color("not-a-color") == "#ffffff"


def test_picks_whichever_of_black_or_white_actually_contrasts_more():
    # Solarized's accent is a mid-tone blue. A perceived-brightness threshold
    # puts it on the "use white" side, but white only reaches 3.68:1 there
    # while black reaches 5.7:1 — below vs. above the WCAG AA floor of 4.5:1
    # for normal text. Choose by measured contrast, not by a brightness cutoff.
    assert contrasting_text_color("#268BD2") == "#000000"


def test_every_builtin_theme_accent_meets_wcag_aa():
    from livestream_list.core.theme_data import BUILTIN_THEMES, theme_data_to_theme_colors

    def _relative_luminance(hex_color: str) -> float:
        raw = hex_color.lstrip("#")
        channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    for slug, data in BUILTIN_THEMES.items():
        accent = theme_data_to_theme_colors(data).accent
        lums = sorted([_relative_luminance(contrasting_text_color(accent)), _relative_luminance(accent)])
        ratio = (lums[1] + 0.05) / (lums[0] + 0.05)
        assert ratio >= 4.5, f"{slug}: accent {accent} only reaches {ratio:.2f}:1"
