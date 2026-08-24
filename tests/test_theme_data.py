"""Tests for built-in theme definitions and theme data conversion."""

import re

from livestream_list.core.theme_data import (
    BUILTIN_THEME_ORDER,
    BUILTIN_THEMES,
    THEME_COLOR_FIELDS,
    ThemeData,
    theme_data_to_theme_colors,
)

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# --- Registry integrity ---


def test_builtin_order_matches_registry():
    assert list(BUILTIN_THEMES.keys()) == BUILTIN_THEME_ORDER


def test_builtin_slugs_match_keys():
    for key, td in BUILTIN_THEMES.items():
        assert td.slug == key
        assert td.builtin is True


def test_obsidian_mono_registered():
    assert "obsidian-mono" in BUILTIN_THEMES
    td = BUILTIN_THEMES["obsidian-mono"]
    assert td.name == "Obsidian Mono"
    assert td.base == "dark"


# --- Color completeness and validity ---


def test_builtin_themes_define_all_color_fields():
    for slug, td in BUILTIN_THEMES.items():
        missing = set(THEME_COLOR_FIELDS) - set(td.colors)
        assert not missing, f"{slug} is missing color fields: {sorted(missing)}"


def test_builtin_theme_colors_are_valid_hex():
    for slug, td in BUILTIN_THEMES.items():
        for field, value in td.colors.items():
            assert HEX_COLOR_RE.match(value), f"{slug}.{field} = {value!r} is not valid hex"


# --- Conversion ---


def test_theme_data_to_theme_colors_preserves_values():
    td = BUILTIN_THEMES["obsidian-mono"]
    tc = theme_data_to_theme_colors(td)
    for field in THEME_COLOR_FIELDS:
        assert getattr(tc, field) == td.colors[field]


def test_platform_colors_brand_by_default_muted_in_obsidian_mono():
    # Non-mono themes keep the exact platform brand colors
    for slug in ("dark", "light", "high-contrast", "nord-dark", "monokai", "solarized-dark"):
        colors = BUILTIN_THEMES[slug].colors
        assert colors["platform_twitch"] == "#9146FF"
        assert colors["platform_youtube"] == "#FF0000"
        assert colors["platform_kick"] == "#53FC18"
        assert colors["platform_chaturbate"] == "#F47321"
    # Obsidian Mono uses its own muted palette
    mono = BUILTIN_THEMES["obsidian-mono"].colors
    assert mono["platform_twitch"] != "#9146FF"


def test_theme_data_to_theme_colors_fills_missing_from_base():
    td = ThemeData(name="Partial", slug="partial", base="dark", colors={"accent": "#123456"})
    tc = theme_data_to_theme_colors(td)
    assert tc.accent == "#123456"
    # Unspecified fields fall back to the dark base theme
    assert tc.window_bg == BUILTIN_THEMES["dark"].colors["window_bg"]
