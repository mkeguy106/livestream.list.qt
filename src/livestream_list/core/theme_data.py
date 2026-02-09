"""Theme data model and I/O for custom themes."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .settings import get_config_dir

# All 32 ThemeColors field names, in order for iteration
THEME_COLOR_FIELDS: list[str] = [
    "window_bg",
    "widget_bg",
    "input_bg",
    "chat_bg",
    "chat_input_bg",
    "popup_bg",
    "toolbar_bg",
    "text_primary",
    "text_secondary",
    "text_muted",
    "chat_system_message",
    "accent",
    "accent_hover",
    "border",
    "border_light",
    "popup_border",
    "selection_bg",
    "selection_text",
    "status_live",
    "status_offline",
    "status_error",
    "status_success",
    "status_info",
    "chat_tab_active",
    "chat_tab_inactive",
    "chat_banner_bg",
    "chat_banner_text",
    "chat_url",
    "chat_url_selected",
    "chat_alt_row_even",
    "chat_alt_row_odd",
    "chat_mention_highlight",
    "list_alt_row_even",
    "list_alt_row_odd",
    "popup_hover",
    "popup_selected",
]

# Color categories for grouped display in the editor UI
THEME_COLOR_CATEGORIES: dict[str, list[str]] = {
    "Backgrounds": [
        "window_bg",
        "widget_bg",
        "input_bg",
        "chat_bg",
        "chat_input_bg",
        "popup_bg",
        "toolbar_bg",
    ],
    "Text": ["text_primary", "text_secondary", "text_muted", "chat_system_message"],
    "Accents": ["accent", "accent_hover"],
    "Borders": ["border", "border_light", "popup_border"],
    "Selection": ["selection_bg", "selection_text"],
    "Status": ["status_live", "status_offline", "status_error", "status_success", "status_info"],
    "Chat": [
        "chat_tab_active",
        "chat_tab_inactive",
        "chat_banner_bg",
        "chat_banner_text",
        "chat_url",
        "chat_url_selected",
        "chat_alt_row_even",
        "chat_alt_row_odd",
        "chat_mention_highlight",
    ],
    "Stream List": ["list_alt_row_even", "list_alt_row_odd"],
    "Popups": ["popup_hover", "popup_selected"],
}

# Human-readable labels for color fields
THEME_COLOR_LABELS: dict[str, str] = {
    "window_bg": "Window",
    "widget_bg": "Widget",
    "input_bg": "Input",
    "chat_bg": "Chat",
    "chat_input_bg": "Chat input",
    "popup_bg": "Popup",
    "toolbar_bg": "Toolbar",
    "text_primary": "Primary",
    "text_secondary": "Secondary",
    "text_muted": "Muted",
    "chat_system_message": "System message",
    "accent": "Accent",
    "accent_hover": "Accent hover",
    "border": "Border",
    "border_light": "Border light",
    "popup_border": "Popup border",
    "selection_bg": "Selection bg",
    "selection_text": "Selection text",
    "status_live": "Live",
    "status_offline": "Offline",
    "status_error": "Error",
    "status_success": "Success",
    "status_info": "Info",
    "chat_tab_active": "Tab active",
    "chat_tab_inactive": "Tab inactive",
    "chat_banner_bg": "Banner bg",
    "chat_banner_text": "Banner text",
    "chat_url": "URL",
    "chat_url_selected": "URL selected",
    "chat_alt_row_even": "Even row",
    "chat_alt_row_odd": "Odd row",
    "chat_mention_highlight": "Mention highlight",
    "list_alt_row_even": "Even row",
    "list_alt_row_odd": "Odd row",
    "popup_hover": "Popup hover",
    "popup_selected": "Popup selected",
}

# Fields that use alpha-enabled color picker
ALPHA_COLOR_FIELDS = {
    "chat_alt_row_even",
    "chat_alt_row_odd",
    "chat_mention_highlight",
    "list_alt_row_even",
    "list_alt_row_odd",
}


@dataclass
class ThemeData:
    """Serializable theme definition."""

    name: str  # Display name: "Nord Dark"
    slug: str = ""  # Filesystem ID: "nord-dark"
    author: str = ""
    base: str = "dark"  # "dark" or "light"
    builtin: bool = False
    colors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "name": self.name,
            "slug": self.slug,
            "author": self.author,
            "base": self.base,
            "colors": dict(self.colors),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ThemeData":
        """Deserialize from dict."""
        return cls(
            name=data.get("name", "Untitled"),
            slug=data.get("slug", ""),
            author=data.get("author", ""),
            base=data.get("base", "dark"),
            builtin=False,
            colors=dict(data.get("colors", {})),
        )


def _name_to_slug(name: str) -> str:
    """Convert a theme name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = slug.replace(" ", "-")
    # Keep only alphanumeric and hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    # Remove consecutive hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "custom"


# ---------------------------------------------------------------------------
# Built-in theme color dicts
# ---------------------------------------------------------------------------

_DARK_COLORS = {
    "window_bg": "#0e1525",
    "widget_bg": "#1a1a2e",
    "input_bg": "#16213e",
    "text_primary": "#eeeeee",
    "text_secondary": "#cccccc",
    "text_muted": "#999999",
    "accent": "#7b5cbf",
    "accent_hover": "#9171d6",
    "border": "#444444",
    "border_light": "#333333",
    "selection_bg": "#7b5cbf",
    "selection_text": "#ffffff",
    "status_live": "#4CAF50",
    "status_offline": "#999999",
    "status_error": "#f44336",
    "status_success": "#4CAF50",
    "status_info": "#2196F3",
    "chat_bg": "#0e1525",
    "chat_input_bg": "#16213e",
    "chat_tab_active": "#7b5cbf",
    "chat_tab_inactive": "#16213e",
    "chat_banner_bg": "#16213e",
    "chat_banner_text": "#cccccc",
    "chat_url": "#58a6ff",
    "chat_url_selected": "#90d5ff",
    "chat_system_message": "#be96ff",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#1affffff",
    "chat_mention_highlight": "#33ff8800",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#0dffffff",
    "popup_bg": "#1a1a2e",
    "popup_hover": "#1f2b4d",
    "popup_selected": "#7b5cbf",
    "popup_border": "#444444",
    "toolbar_bg": "#0e1525",
}

_LIGHT_COLORS = {
    "window_bg": "#f5f5f5",
    "widget_bg": "#ffffff",
    "input_bg": "#ffffff",
    "text_primary": "#1a1a1a",
    "text_secondary": "#444444",
    "text_muted": "#666666",
    "accent": "#6441a5",
    "accent_hover": "#7d5bbe",
    "border": "#cccccc",
    "border_light": "#e0e0e0",
    "selection_bg": "#6441a5",
    "selection_text": "#ffffff",
    "status_live": "#2e7d32",
    "status_offline": "#555555",
    "status_error": "#b71c1c",
    "status_success": "#2e7d32",
    "status_info": "#1565c0",
    "chat_bg": "#ffffff",
    "chat_input_bg": "#f5f5f5",
    "chat_tab_active": "#6441a5",
    "chat_tab_inactive": "#e8e8e8",
    "chat_banner_bg": "#e8e8f0",
    "chat_banner_text": "#333333",
    "chat_url": "#0550ae",
    "chat_url_selected": "#003d82",
    "chat_system_message": "#6b4f96",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#0f000000",
    "chat_mention_highlight": "#40ff8800",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#08000000",
    "popup_bg": "#ffffff",
    "popup_hover": "#f0f0f5",
    "popup_selected": "#6441a5",
    "popup_border": "#cccccc",
    "toolbar_bg": "#e8e8e8",
}

_HIGH_CONTRAST_COLORS = {
    "window_bg": "#000000",
    "widget_bg": "#0a0a0a",
    "input_bg": "#1a1a1a",
    "text_primary": "#ffffff",
    "text_secondary": "#e0e0e0",
    "text_muted": "#bbbbbb",
    "accent": "#ffcc00",
    "accent_hover": "#ffe066",
    "border": "#888888",
    "border_light": "#666666",
    "selection_bg": "#ffcc00",
    "selection_text": "#000000",
    "status_live": "#00ff00",
    "status_offline": "#bbbbbb",
    "status_error": "#ff4444",
    "status_success": "#00ff00",
    "status_info": "#44aaff",
    "chat_bg": "#000000",
    "chat_input_bg": "#1a1a1a",
    "chat_tab_active": "#ffcc00",
    "chat_tab_inactive": "#1a1a1a",
    "chat_banner_bg": "#1a1a1a",
    "chat_banner_text": "#e0e0e0",
    "chat_url": "#44aaff",
    "chat_url_selected": "#88ccff",
    "chat_system_message": "#ddaaff",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#20ffffff",
    "chat_mention_highlight": "#55ff8800",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#10ffffff",
    "popup_bg": "#0a0a0a",
    "popup_hover": "#222222",
    "popup_selected": "#ffcc00",
    "popup_border": "#888888",
    "toolbar_bg": "#000000",
}

_NORD_DARK_COLORS = {
    "window_bg": "#2E3440",
    "widget_bg": "#3B4252",
    "input_bg": "#434C5E",
    "text_primary": "#ECEFF4",
    "text_secondary": "#D8DEE9",
    "text_muted": "#7B88A1",
    "accent": "#88C0D0",
    "accent_hover": "#8FBCBB",
    "border": "#4C566A",
    "border_light": "#434C5E",
    "selection_bg": "#88C0D0",
    "selection_text": "#2E3440",
    "status_live": "#A3BE8C",
    "status_offline": "#7B88A1",
    "status_error": "#BF616A",
    "status_success": "#A3BE8C",
    "status_info": "#81A1C1",
    "chat_bg": "#2E3440",
    "chat_input_bg": "#434C5E",
    "chat_tab_active": "#88C0D0",
    "chat_tab_inactive": "#3B4252",
    "chat_banner_bg": "#3B4252",
    "chat_banner_text": "#D8DEE9",
    "chat_url": "#81A1C1",
    "chat_url_selected": "#88C0D0",
    "chat_system_message": "#B48EAD",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#1affffff",
    "chat_mention_highlight": "#33EBCB8B",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#0dffffff",
    "popup_bg": "#3B4252",
    "popup_hover": "#434C5E",
    "popup_selected": "#88C0D0",
    "popup_border": "#4C566A",
    "toolbar_bg": "#2E3440",
}

_MONOKAI_COLORS = {
    "window_bg": "#272822",
    "widget_bg": "#2D2E27",
    "input_bg": "#3E3D32",
    "text_primary": "#F8F8F2",
    "text_secondary": "#CFCFC2",
    "text_muted": "#75715E",
    "accent": "#A6E22E",
    "accent_hover": "#C2F74C",
    "border": "#49483E",
    "border_light": "#3E3D32",
    "selection_bg": "#49483E",
    "selection_text": "#F8F8F2",
    "status_live": "#A6E22E",
    "status_offline": "#75715E",
    "status_error": "#F92672",
    "status_success": "#A6E22E",
    "status_info": "#66D9EF",
    "chat_bg": "#272822",
    "chat_input_bg": "#3E3D32",
    "chat_tab_active": "#A6E22E",
    "chat_tab_inactive": "#2D2E27",
    "chat_banner_bg": "#2D2E27",
    "chat_banner_text": "#CFCFC2",
    "chat_url": "#66D9EF",
    "chat_url_selected": "#A1EFE4",
    "chat_system_message": "#E6DB74",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#1affffff",
    "chat_mention_highlight": "#33FD971F",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#0dffffff",
    "popup_bg": "#2D2E27",
    "popup_hover": "#3E3D32",
    "popup_selected": "#A6E22E",
    "popup_border": "#49483E",
    "toolbar_bg": "#272822",
}

_SOLARIZED_DARK_COLORS = {
    "window_bg": "#002B36",
    "widget_bg": "#073642",
    "input_bg": "#073642",
    "text_primary": "#FDF6E3",
    "text_secondary": "#EEE8D5",
    "text_muted": "#657B83",
    "accent": "#268BD2",
    "accent_hover": "#2AA198",
    "border": "#586E75",
    "border_light": "#073642",
    "selection_bg": "#268BD2",
    "selection_text": "#FDF6E3",
    "status_live": "#859900",
    "status_offline": "#657B83",
    "status_error": "#DC322F",
    "status_success": "#859900",
    "status_info": "#268BD2",
    "chat_bg": "#002B36",
    "chat_input_bg": "#073642",
    "chat_tab_active": "#268BD2",
    "chat_tab_inactive": "#073642",
    "chat_banner_bg": "#073642",
    "chat_banner_text": "#EEE8D5",
    "chat_url": "#268BD2",
    "chat_url_selected": "#2AA198",
    "chat_system_message": "#6C71C4",
    "chat_alt_row_even": "#00000000",
    "chat_alt_row_odd": "#1affffff",
    "chat_mention_highlight": "#33B58900",
    "list_alt_row_even": "#00000000",
    "list_alt_row_odd": "#0dffffff",
    "popup_bg": "#073642",
    "popup_hover": "#0A4656",
    "popup_selected": "#268BD2",
    "popup_border": "#586E75",
    "toolbar_bg": "#002B36",
}

# ---------------------------------------------------------------------------
# Built-in theme registry
# ---------------------------------------------------------------------------

BUILTIN_THEMES: dict[str, ThemeData] = {
    "dark": ThemeData(
        name="Dark",
        slug="dark",
        base="dark",
        builtin=True,
        colors=_DARK_COLORS,
    ),
    "light": ThemeData(
        name="Light",
        slug="light",
        base="light",
        builtin=True,
        colors=_LIGHT_COLORS,
    ),
    "high-contrast": ThemeData(
        name="High Contrast",
        slug="high-contrast",
        base="dark",
        builtin=True,
        colors=_HIGH_CONTRAST_COLORS,
    ),
    "nord-dark": ThemeData(
        name="Nord Dark",
        slug="nord-dark",
        author="Arctic Ice Studio",
        base="dark",
        builtin=True,
        colors=_NORD_DARK_COLORS,
    ),
    "monokai": ThemeData(
        name="Monokai",
        slug="monokai",
        base="dark",
        builtin=True,
        colors=_MONOKAI_COLORS,
    ),
    "solarized-dark": ThemeData(
        name="Solarized Dark",
        slug="solarized-dark",
        author="Ethan Schoonover",
        base="dark",
        builtin=True,
        colors=_SOLARIZED_DARK_COLORS,
    ),
}

# Ordered list of built-in theme slugs for cycling
BUILTIN_THEME_ORDER: list[str] = [
    "dark",
    "light",
    "high-contrast",
    "nord-dark",
    "monokai",
    "solarized-dark",
]


def theme_data_to_theme_colors(data: "ThemeData"):
    """Convert a ThemeData to a ThemeColors instance.

    Missing fields are filled from the base theme (dark or light).
    """
    from ..gui.theme import ThemeColors

    base_colors = _DARK_COLORS if data.base == "dark" else _LIGHT_COLORS
    merged = dict(base_colors)
    merged.update(data.colors)
    return ThemeColors(**{k: merged[k] for k in THEME_COLOR_FIELDS})


def theme_colors_to_dict(tc) -> dict[str, str]:
    """Extract all 32 color fields from a ThemeColors instance."""
    return {f: getattr(tc, f) for f in THEME_COLOR_FIELDS}


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def get_themes_dir() -> Path:
    """Get the custom themes directory (created on demand)."""
    return get_config_dir() / "themes"


def list_custom_themes() -> list[ThemeData]:
    """Load all custom theme files from the themes directory."""
    themes_dir = get_themes_dir()
    if not themes_dir.exists():
        return []
    results = []
    for path in sorted(themes_dir.glob("*.json")):
        try:
            td = load_theme_file(path)
            results.append(td)
        except Exception:
            continue
    return results


def list_all_themes() -> list[ThemeData]:
    """Return all themes: built-ins first (in order), then custom (alphabetical)."""
    themes = [BUILTIN_THEMES[slug] for slug in BUILTIN_THEME_ORDER]
    themes.extend(list_custom_themes())
    return themes


def load_theme_file(path: Path) -> ThemeData:
    """Load a single theme JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    td = ThemeData.from_dict(data)
    # Derive slug from filename if not set
    if not td.slug:
        td.slug = path.stem
    return td


def save_theme_file(data: ThemeData) -> Path:
    """Save a theme to the themes directory (atomic write). Returns the path."""
    if not data.slug:
        data.slug = _name_to_slug(data.name)
    themes_dir = get_themes_dir()
    themes_dir.mkdir(parents=True, exist_ok=True)
    dest = themes_dir / f"{data.slug}.json"

    fd, tmp_path = tempfile.mkstemp(dir=themes_dir, suffix=".tmp", prefix="theme_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data.to_dict(), f, indent=2)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return dest


def delete_theme_file(slug: str) -> bool:
    """Delete a custom theme file. Returns True if deleted."""
    path = get_themes_dir() / f"{slug}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def export_theme(data: ThemeData, path: Path) -> None:
    """Export a theme to a user-chosen location."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data.to_dict(), f, indent=2)


def import_theme(path: Path) -> ThemeData:
    """Import a theme file into the themes directory."""
    td = load_theme_file(path)
    # Ensure unique slug
    existing_slugs = {t.slug for t in list_all_themes()}
    base_slug = td.slug or _name_to_slug(td.name)
    slug = base_slug
    counter = 1
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    td.slug = slug
    td.builtin = False
    save_theme_file(td)
    return td
